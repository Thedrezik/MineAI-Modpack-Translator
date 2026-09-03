import json
import os
import tempfile
import unittest
from unittest import mock

import requests

from mineai.cache import TranslationCache
from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.kobold import KoboldEngine
from mineai.engines.llm_common import BatchLlmEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.json_utils import load_lenient_json
from mineai.output.pack_writer import PackWriter
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState


class LenientJsonSafetyTests(unittest.TestCase):
    def test_preserves_comment_like_content_inside_strings(self):
        raw = (
            '{"url":"https://example.com/docs",'
            '"text":"Use /* advanced */ mode",'
            '"literal":"comma, } stays"}'
        )

        data = load_lenient_json(raw)

        self.assertEqual(data["url"], "https://example.com/docs")
        self.assertEqual(data["text"], "Use /* advanced */ mode")
        self.assertEqual(data["literal"], "comma, } stays")

    def test_preserves_escaped_quotes_and_backslashes(self):
        expected = {
            "quote": 'He said "// not a comment"',
            "path": r"C:\\mods\\/* literal */",
        }
        raw = json.dumps(expected, ensure_ascii=False)
        raw = raw[:-1] + ",}"

        self.assertEqual(load_lenient_json(raw), expected)

    def test_still_accepts_real_comments_and_trailing_commas(self):
        raw = """
        {
            // line comment
            "a": 1,
            /* block comment */
            "b": [1, 2,],
        }
        """

        self.assertEqual(load_lenient_json(raw), {"a": 1, "b": [1, 2]})


class RetryCancellationTests(unittest.TestCase):
    def test_request_cancelled_is_not_a_network_exception(self):
        self.assertFalse(issubclass(RequestCancelled, requests.RequestException))

    def test_cancelled_before_first_attempt_does_not_call_request(self):
        request = mock.Mock()

        with self.assertRaises(RequestCancelled):
            request_with_retry(
                request,
                operation="test",
                should_continue=lambda: False,
            )

        request.assert_not_called()

    def test_backoff_stops_when_job_is_cancelled(self):
        states = iter((True, True, False))
        request = mock.Mock(side_effect=requests.Timeout("slow"))

        with mock.patch("mineai.engines.http_retry.time.sleep", return_value=None):
            with self.assertRaises(RequestCancelled):
                request_with_retry(
                    request,
                    operation="test",
                    attempts=4,
                    base_delay=10,
                    should_continue=lambda: next(states),
                )

        request.assert_called_once_with()

    def test_rate_limit_retry_can_be_disabled_for_endpoint_failover(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "60"
        request = mock.Mock(return_value=response)

        with self.assertRaises(requests.HTTPError):
            request_with_retry(
                request,
                operation="test",
                retry_429=False,
            )

        request.assert_called_once_with()


class EngineCancellationTests(unittest.TestCase):
    @staticmethod
    def callbacks(on_log=lambda *_args: None) -> EngineCallbacks:
        return EngineCallbacks(
            should_run=lambda: True,
            wait_if_paused=lambda: None,
            on_log=on_log,
            on_status=lambda *_args: None,
        )

    def test_google_request_does_not_swallow_cancellation_as_network_failure(self):
        engine = GoogleEngine(workers=1)

        with mock.patch(
            "mineai.engines.google.request_with_retry",
            side_effect=RequestCancelled("stop"),
        ):
            with self.assertRaises(RequestCancelled):
                engine._request("Hello", "ru")

    def test_google_switches_endpoint_after_a_rate_limited_host(self):
        engine = GoogleEngine(workers=1)
        rate_limited = requests.Response()
        rate_limited.status_code = 429
        rate_limited.headers["Retry-After"] = "0"
        rate_limited.url = engine.API_URL
        translated = requests.Response()
        translated.status_code = 200
        translated.url = engine.API_URL
        translated._content = b'[[["Privet", "Hello", null, null, 1]], null, "en"]'
        translated.encoding = "utf-8"

        with mock.patch(
            "mineai.engines.google.requests.get",
            side_effect=(rate_limited, translated),
        ) as request:
            result = engine._request("Hello", "ru")

        self.assertEqual(result, "Privet")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0].args[0], engine.API_URL)
        self.assertNotEqual(request.call_args_list[-1].args[0], engine.API_URL)

    def test_google_falls_back_to_mobile_html_after_both_gtx_hosts_are_limited(self):
        engine = GoogleEngine(workers=1)
        rate_limited = requests.Response()
        rate_limited.status_code = 429
        rate_limited.headers["Retry-After"] = "60"
        rate_limited.url = engine.API_URL
        translated = requests.Response()
        translated.status_code = 200
        translated.url = engine.MOBILE_API_URL
        translated._content = (
            b'<html><div class="result-container">'
            b'\xd0\x9f\xd1\x80\xd0\xb8\xd0\xb2\xd0\xb5\xd1\x82 '
            b'[#0#]</div></html>'
        )
        translated.encoding = "utf-8"

        with mock.patch(
            "mineai.engines.google.requests.get",
            side_effect=(rate_limited, rate_limited, translated),
        ) as request:
            result = engine._request("Hello [#0#]", "ru")

        self.assertEqual(result, "Привет [#0#]")
        self.assertEqual(request.call_count, 3)
        self.assertEqual(request.call_args_list[-1].args[0], engine.MOBILE_API_URL)

    def test_google_retry_delay_honors_retry_after_header(self):
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "3.5"
        error = requests.HTTPError(response=response)

        self.assertEqual(GoogleEngine._retry_delay(1, error), 3.5)

    def test_google_single_mode_handles_worker_cancellation_cleanly(self):
        engine = GoogleEngine(workers=1, mode="single")
        item = EngineItem(key="k", original="Hello", masked="Hello")

        with mock.patch.object(
            engine,
            "_request",
            side_effect=RequestCancelled("stop"),
        ):
            result = engine.translate_batch(
                {"k": item},
                {"api": "ru"},
                self.callbacks(),
            )

        self.assertEqual(result, {})

    def test_google_batch_mode_handles_worker_cancellation_cleanly(self):
        engine = GoogleEngine(workers=1, mode="batch")
        item = EngineItem(key="k", original="Hello", masked="Hello")

        with mock.patch.object(
            engine,
            "_request",
            side_effect=RequestCancelled("stop"),
        ):
            result = engine.translate_batch(
                {"k": item},
                {"api": "ru"},
                self.callbacks(),
            )

        self.assertEqual(result, {})

    def test_google_single_retries_reordered_placeholders(self):
        engine = GoogleEngine(workers=1, mode="single")
        item = EngineItem(
            key="k",
            original="Read [Guide](guide.md).",
            masked="Read [#0#]Guide[#1#].",
            mapping={"[#0#]": "[", "[#1#]": "](guide.md)"},
        )

        with mock.patch.object(
            engine,
            "_request",
            side_effect=(
                "Читайте [#1#]руководство[#0#].",
                "Читайте [#0#]руководство[#1#].",
            ),
        ) as request:
            result = engine.translate_batch(
                {"k": item},
                {"api": "ru"},
                self.callbacks(),
            )

        self.assertEqual(result, {"k": "Читайте [руководство](guide.md)."})
        self.assertEqual(request.call_count, 2)

    def test_google_sends_only_text_not_structured_unit_id(self):
        engine = GoogleEngine(workers=1, mode="single")
        internal_key = "json:/pages/0/title"
        item = EngineItem(
            key=internal_key,
            original="Sliding Doors",
            masked="Sliding Doors",
        )

        with mock.patch.object(
            engine,
            "_request",
            return_value="Раздвижные двери",
        ) as request:
            result = engine.translate_batch(
                {internal_key: item},
                {"api": "ru"},
                self.callbacks(),
            )

        self.assertEqual(result, {internal_key: "Раздвижные двери"})
        self.assertEqual(request.call_args.args[0], "Sliding Doors")
        self.assertNotIn(internal_key, request.call_args.args[0])

    def test_google_batch_retries_smeared_duplicate_rows_separately(self):
        engine = GoogleEngine(workers=1, mode="batch")
        items = {
            "first": EngineItem(
                "first",
                "The first independent sentence describes a crafting machine.",
                "The first independent sentence describes a crafting machine.",
            ),
            "second": EngineItem(
                "second",
                "The second independent sentence explains a wireless terminal.",
                "The second independent sentence explains a wireless terminal.",
            ),
        }
        duplicate = "Это ошибочно объединённый перевод двух разных длинных строк."

        with mock.patch.object(
            engine,
            "_request",
            side_effect=(
                duplicate + GoogleEngine.BATCH_SEP + duplicate,
                "Первая строка описывает машину для крафта.",
                "Вторая строка объясняет беспроводной терминал.",
            ),
        ) as request, mock.patch(
            "mineai.engines.google.time.sleep", return_value=None
        ):
            result = engine.translate_batch(
                items,
                {"api": "ru"},
                self.callbacks(),
            )

        self.assertNotEqual(result["first"], result["second"])
        self.assertEqual(request.call_count, 3)

    def test_openrouter_cancellation_bubbles_to_engine_boundary_without_error_log(self):
        engine = OpenRouterEngine("key", "model")
        logs: list[tuple[str, str]] = []

        with mock.patch(
            "mineai.engines.openrouter.request_with_retry",
            side_effect=RequestCancelled("stop"),
        ):
            with self.assertRaises(RequestCancelled):
                engine._request("prompt", 100, on_log=lambda *args: logs.append(args))

        self.assertEqual(logs, [])

    def test_openrouter_translate_batch_handles_cancellation_and_resets_callbacks(self):
        engine = OpenRouterEngine("key", "model")

        with mock.patch.object(
            BatchLlmEngine,
            "translate_batch",
            side_effect=RequestCancelled("stop"),
        ):
            result = engine.translate_batch(
                {},
                {"name": "Russian"},
                self.callbacks(),
            )

        self.assertEqual(result, {})
        self.assertIsNone(engine._should_continue)
        self.assertIsNone(engine._on_log)

    def test_kobold_cancellation_bubbles_to_engine_boundary_without_error_log(self):
        engine = KoboldEngine()
        logs: list[tuple[str, str]] = []

        with mock.patch(
            "mineai.engines.kobold.request_with_retry",
            side_effect=RequestCancelled("stop"),
        ):
            with self.assertRaises(RequestCancelled):
                engine._request("prompt", 100, on_log=lambda *args: logs.append(args))

        self.assertEqual(logs, [])

    def test_kobold_translate_batch_handles_cancellation_and_resets_callbacks(self):
        engine = KoboldEngine()

        with mock.patch.object(
            BatchLlmEngine,
            "translate_batch",
            side_effect=RequestCancelled("stop"),
        ):
            result = engine.translate_batch(
                {},
                {"name": "Russian"},
                self.callbacks(),
            )

        self.assertEqual(result, {})
        self.assertIsNone(engine._should_continue)
        self.assertIsNone(engine._on_log)

    def test_deepl_cancellation_is_not_reported_as_network_error(self):
        engine = DeepLEngine("key")
        item = EngineItem(key="k", original="Hello", masked="Hello")
        logs: list[tuple[str, str]] = []

        with mock.patch(
            "mineai.engines.deepl.request_with_retry",
            side_effect=RequestCancelled("stop"),
        ):
            result = engine.translate_batch(
                {"k": item},
                {"deepl": "RU"},
                self.callbacks(on_log=lambda *args: logs.append(args)),
            )

        self.assertEqual(result, {})
        self.assertEqual(logs, [])


class PackCancellationTests(unittest.TestCase):
    def test_abort_removes_only_archives_created_by_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PackWriter(directory, "MineAI_Pack", "1.20.1", "Russian")
            rp_path = writer.rp_zip_path
            dp_path = writer.dp_zip_path
            self.assertTrue(os.path.exists(rp_path))
            self.assertTrue(os.path.exists(dp_path))

            writer.abort()
            writer.abort()

            self.assertFalse(os.path.exists(rp_path))
            self.assertFalse(os.path.exists(dp_path))


class _Config:
    def get(self, _section, _key):
        return ""

    def getboolean(self, _section, _key):
        return False


class TranslationJobCancellationTests(unittest.TestCase):
    def test_cancelled_resourcepack_job_aborts_partial_archives(self):
        state = JobState(is_running=True)
        cache = mock.Mock(spec=TranslationCache)
        writer = mock.Mock(spec=PackWriter)
        job = TranslationJob(
            _Config(),
            cache,
            cache,
            state,
            on_log=lambda *_args: None,
            on_status=lambda *_args: None,
            on_row=lambda *_args: None,
        )
        options = TranslationOptions(
            mc_dir="/modpack",
            language_label="Русский",
            mc_version="1.20.1",
            output_mode="resourcepack",
            pack_name="MineAI_Pack",
            engine="google",
            google_mode="single",
            ai_mode="safe",
            ai_batch=20,
            ai_provider="local",
            process_mode="append",
            translate_mods=True,
            translate_books=False,
            translate_quests=False,
        )

        def stop_during_file(*_args, **_kwargs):
            state.stop()

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_loose_lang_files", return_value=["en_us.json"]),
            mock.patch("mineai.runtime.job.discover_snbt_files", return_value=[]),
            mock.patch("mineai.runtime.job.discover_bq_files", return_value=[]),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch("mineai.runtime.job.PackWriter", return_value=writer),
            mock.patch("mineai.runtime.job.TranslationService"),
            mock.patch("mineai.runtime.job.LooseJsonProcessor.process", side_effect=stop_during_file),
        ):
            job.run_translation(options)

        writer.abort.assert_called_once_with()
        writer.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
