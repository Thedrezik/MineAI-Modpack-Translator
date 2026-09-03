import unittest
from unittest import mock

import os
import inspect
import requests

from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.engines.service import TranslationService
from mineai.engines.kobold import KoboldEngine
from mineai.gui_qt.view_model import engine_readiness
from mineai.runtime.job import TranslationJob, TranslationOptions
from mineai.runtime.state import JobState

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtWidgets import QApplication
    from mineai.gui_qt.dialogs import SettingsDialog
    from mineai.gui_qt.main_window import TranslatorQtWindow
except ImportError:
    QApplication = None
    SettingsDialog = None
    TranslatorQtWindow = None


def _lmstudio_components(test_case):
    try:
        from mineai.engines.lmstudio import (
            LmStudioEngine,
            list_lmstudio_models,
            normalize_lmstudio_base_url,
        )
    except ImportError as exc:
        test_case.fail(f"LM Studio provider is missing: {exc}")
    return LmStudioEngine, list_lmstudio_models, normalize_lmstudio_base_url


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {"data": []}
        self.post_payload = post_payload or {
            "choices": [{"message": {"content": "translated"}}]
        }
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response(self.get_payload)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response(self.post_payload)


class _LegacyLmStudioSession(_Session):
    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        if url.endswith("/api/v1/models"):
            response = _Response({})
            response.raise_for_status = mock.Mock(
                side_effect=requests.HTTPError("not supported")
            )
            return response
        return _Response(
            {
                "data": [
                    {"id": "qwen/loaded", "type": "llm", "state": "loaded"},
                    {"id": "llama/off", "type": "llm", "state": "not-loaded"},
                    {"id": "embed", "type": "embeddings", "state": "loaded"},
                ]
            }
        )


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, section, key):
        return self.values.get((section, key), "")

    def getint(self, section, key, fallback=0):
        raw = self.get(section, key)
        return int(raw) if str(raw).isdigit() else fallback

    def getboolean(self, section, key):
        return str(self.get(section, key)).casefold() == "true"


class LmStudioConfigurationTests(unittest.TestCase):
    def test_defaults_use_official_openai_compatible_address(self):
        self.assertIn("LMSTUDIO", ConfigManager._DEFAULTS)
        self.assertEqual(
            ConfigManager._DEFAULTS["LMSTUDIO"]["base_url"],
            "http://localhost:1234/v1",
        )
        self.assertEqual(ConfigManager._DEFAULTS["LMSTUDIO"]["api_key"], "")
        self.assertEqual(ConfigManager._DEFAULTS["LMSTUDIO"]["model"], "")

    def test_readiness_requires_address_and_model_but_not_token(self):
        self.assertFalse(engine_readiness(_Config(), "LM Studio")[0])
        configured = _Config(
            {
                ("LMSTUDIO", "base_url"): "http://localhost:1234/v1",
                ("LMSTUDIO", "model"): "qwen/model",
            }
        )
        ready, text = engine_readiness(configured, "LM Studio")
        self.assertTrue(ready)
        self.assertEqual(text, "LM Studio · qwen/model")


class LmStudioApiTests(unittest.TestCase):
    def test_base_url_accepts_host_or_v1_address(self):
        _, _, normalize = _lmstudio_components(self)
        self.assertEqual(normalize(""), "http://localhost:1234/v1")
        self.assertEqual(
            normalize("http://localhost:1234"),
            "http://localhost:1234/v1",
        )
        self.assertEqual(
            normalize("http://localhost:1234/v1/"),
            "http://localhost:1234/v1",
        )

    def test_model_list_uses_openai_endpoint_and_optional_token(self):
        _, list_models, _ = _lmstudio_components(self)
        session = _Session(
            get_payload={
                "data": [
                    {"id": "qwen/model"},
                    {"id": "llama/model"},
                    {"id": ""},
                ]
            }
        )

        models = list_models(
            "http://localhost:1234/v1/",
            api_key="secret",
            session=session,
        )

        self.assertEqual(models, ["qwen/model", "llama/model"])
        self.assertEqual(session.get_calls[0][0], "http://localhost:1234/v1/models")
        self.assertEqual(
            session.get_calls[0][1]["headers"]["Authorization"],
            "Bearer secret",
        )

    def test_loaded_model_list_uses_native_api_and_ignores_unloaded_or_embedding(self):
        from mineai.engines.lmstudio import list_loaded_lmstudio_models

        session = _Session(
            get_payload={
                "models": [
                    {
                        "type": "llm",
                        "key": "qwen/model",
                        "loaded_instances": [
                            {"id": "qwen/model:1"},
                            {"id": "qwen/model:2"},
                        ],
                    },
                    {
                        "type": "llm",
                        "key": "llama/model",
                        "loaded_instances": [],
                    },
                    {
                        "type": "embedding",
                        "key": "embed/model",
                        "loaded_instances": [{"id": "embed/model"}],
                    },
                ]
            }
        )

        models = list_loaded_lmstudio_models(
            "http://localhost:1234/v1",
            api_key="secret",
            session=session,
        )

        self.assertEqual(models, ["qwen/model:1", "qwen/model:2"])
        self.assertEqual(
            session.get_calls[0][0],
            "http://localhost:1234/api/v1/models",
        )

    def test_loaded_model_list_falls_back_to_legacy_state_endpoint(self):
        from mineai.engines.lmstudio import list_loaded_lmstudio_models

        session = _LegacyLmStudioSession()

        models = list_loaded_lmstudio_models(
            "http://localhost:1234/v1",
            session=session,
        )

        self.assertEqual(models, ["qwen/loaded"])
        self.assertEqual(
            [url for url, _options in session.get_calls],
            [
                "http://localhost:1234/api/v1/models",
                "http://localhost:1234/api/v0/models",
            ],
        )

    def test_chat_requests_reuse_session_and_send_selected_model(self):
        engine_type, _, _ = _lmstudio_components(self)
        session = _Session()
        engine = engine_type(
            "http://localhost:1234/v1",
            "qwen/model",
            session=session,
        )

        self.assertEqual(engine._request("first", 120), "translated")
        self.assertEqual(engine._request("second", 240), "translated")

        self.assertEqual(len(session.post_calls), 2)
        first_url, first_options = session.post_calls[0]
        self.assertEqual(first_url, "http://localhost:1234/v1/chat/completions")
        self.assertEqual(first_options["json"]["model"], "qwen/model")
        self.assertEqual(first_options["json"]["max_tokens"], 120)
        self.assertIn("repeat_penalty", first_options["json"])
        self.assertEqual(first_options["json"]["repeat_penalty"], 1.0)
        self.assertNotIn("repetition_penalty", first_options["json"])
        self.assertFalse(first_options["json"]["stream"])
        self.assertNotIn("Authorization", first_options["headers"])

    def test_truncated_completion_is_rejected_before_translation_validation(self):
        engine_type, _, _ = _lmstudio_components(self)
        session = _Session(
            post_payload={
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '["Перевод"'},
                    }
                ]
            }
        )
        engine = engine_type(
            "http://localhost:1234/v1",
            "qwen/model",
            session=session,
        )
        logs = []

        result = engine._request(
            'Rules\n\nDATA:\n["Long source text"]',
            4096,
            on_log=lambda message, tag: logs.append((message, tag)),
        )

        self.assertIsNone(result)
        self.assertTrue(any("max_tokens" in message for message, _ in logs))

    def test_clean_array_request_uses_a_bounded_output_budget(self):
        engine_type, _, _ = _lmstudio_components(self)
        session = _Session()
        engine = engine_type(
            "http://localhost:1234/v1",
            "qwen/model",
            session=session,
        )

        engine._request(
            'Rules\n\nDATA:\n["Short title", "Another short title"]',
            4096,
        )

        sent_limit = session.post_calls[0][1]["json"]["max_tokens"]
        self.assertGreaterEqual(sent_limit, 256)
        self.assertLess(sent_limit, 4096)

    def test_batch_prompt_uses_json_schema_but_plain_repair_does_not(self):
        engine_type, _, _ = _lmstudio_components(self)
        session = _Session()
        engine = engine_type(
            "http://localhost:1234/v1",
            "qwen/model",
            session=session,
        )

        engine._request('Rules\n\nDATA:\n{"0": "Text"}', 120)
        engine._request('Rules\n\nDATA:\n["Text", "More"]', 120)
        engine._request("Repair this one sentence", 120)

        batch_payload = session.post_calls[0][1]["json"]
        array_payload = session.post_calls[1][1]["json"]
        repair_payload = session.post_calls[2][1]["json"]
        response_format = batch_payload["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(
            response_format["json_schema"]["schema"]["required"],
            ["0"],
        )
        self.assertFalse(
            response_format["json_schema"]["schema"]["additionalProperties"]
        )
        array_schema = array_payload["response_format"]["json_schema"]["schema"]
        self.assertEqual(array_schema["type"], "array")
        self.assertEqual(array_schema["minItems"], 2)
        self.assertEqual(array_schema["maxItems"], 2)
        self.assertNotIn("response_format", repair_payload)

    def test_service_constructs_lmstudio_provider(self):
        engine_type, _, _ = _lmstudio_components(self)
        config = _Config(
            {
                ("AI", "ai_retries"): "2",
                ("LMSTUDIO", "base_url"): "http://localhost:1234/v1",
                ("LMSTUDIO", "model"): "qwen/model",
                ("LMSTUDIO", "api_key"): "",
            }
        )
        service = TranslationService(
            "ai",
            mock.Mock(spec=TranslationCache),
            config,
            ai_provider="lmstudio",
        )

        engine = service._build_engine()

        self.assertIsInstance(engine, engine_type)
        self.assertEqual(engine.model, "qwen/model")
        self.assertEqual(engine.retries, 2)

    def test_service_reuses_lmstudio_connection_across_files(self):
        config = _Config(
            {
                ("AI", "ai_retries"): "2",
                ("LMSTUDIO", "base_url"): "http://localhost:1234/v1",
                ("LMSTUDIO", "model"): "qwen/model",
                ("LMSTUDIO", "api_key"): "",
            }
        )
        service = TranslationService(
            "ai",
            mock.Mock(spec=TranslationCache),
            config,
            ai_provider="lmstudio",
        )

        first = service._build_engine(context="first file")
        second = service._build_engine(context="second file")

        self.assertIs(first.session, second.session)

    def test_cancellation_is_not_reported_as_network_error(self):
        engine_type, _, _ = _lmstudio_components(self)
        from mineai.engines.http_retry import RequestCancelled

        engine = engine_type("http://localhost:1234/v1", "qwen/model")
        logs = []
        with mock.patch(
            "mineai.engines.lmstudio.request_with_retry",
            side_effect=RequestCancelled("stop"),
        ):
            with self.assertRaises(RequestCancelled):
                engine._request("prompt", 100, on_log=lambda *args: logs.append(args))

        self.assertEqual(logs, [])


class LocalRequestOptimizationTests(unittest.TestCase):
    def test_koboldcpp_reuses_one_http_session(self):
        self.assertIn("session", inspect.signature(KoboldEngine).parameters)
        session = _Session()
        engine = KoboldEngine(session=session)

        self.assertEqual(engine._request("first", 120), "translated")
        self.assertEqual(engine._request("second", 240), "translated")

        self.assertEqual(len(session.post_calls), 2)
        self.assertEqual(
            session.post_calls[0][0],
            "http://localhost:5001/v1/chat/completions",
        )


class _DialogConfig(_Config):
    def __init__(self, values=None):
        super().__init__(values)
        self.saved = []

    def set_many(self, section, values):
        self.saved.append((section, values))


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class LmStudioSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _config():
        return _DialogConfig(
            {
                ("AI", "exe_path"): "koboldcpp.exe",
                ("AI", "model_path"): "",
                ("AI", "gpu_layers"): "99",
                ("AI", "ai_retries"): "3",
                ("OPENROUTER", "api_url"): "https://example.invalid/v1",
                ("OPENROUTER", "api_key"): "",
                ("OPENROUTER", "model"): "model",
                ("OPENROUTER", "site_url"): "",
                ("OPENROUTER", "app_name"): "MineAI Translator",
                ("LMSTUDIO", "base_url"): "http://localhost:1234/v1",
                ("LMSTUDIO", "api_key"): "",
                ("LMSTUDIO", "model"): "",
                ("GENERAL", "smart_glue"): "True",
                ("GENERAL", "google_workers"): "5",
                ("API", "deepl_key"): "",
            }
        )

    def test_has_dedicated_tab_and_connection_controls(self):
        dialog = SettingsDialog(self._config(), lambda: None)
        try:
            self.assertTrue(hasattr(dialog, "tabs"))
            labels = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
            self.assertIn("LM Studio", labels)
            self.assertEqual(dialog.lm_url.text(), "http://localhost:1234/v1")
            self.assertTrue(dialog.lm_model.isEditable())
            self.assertIsNotNone(dialog.lm_refresh)
            self.assertIsNotNone(dialog.lm_test)
        finally:
            dialog.close()

    def test_successful_probe_selects_first_model_when_empty(self):
        dialog = SettingsDialog(self._config(), lambda: None)
        try:
            self.assertTrue(hasattr(dialog, "_lmstudio_probe_finished"))
            dialog._lmstudio_probe_finished(True, ["qwen/model", "llama/model"], "")
            self.assertEqual(dialog.lm_model.currentText(), "qwen/model")
            self.assertIn("2", dialog.lm_status.text())
            self.assertTrue(dialog.lm_refresh.isEnabled())
            self.assertTrue(dialog.lm_test.isEnabled())
        finally:
            dialog.close()

    def test_probe_replaces_stale_id_with_single_loaded_model(self):
        config = self._config()
        config.values[("LMSTUDIO", "model")] = "old/unloaded-model"
        dialog = SettingsDialog(config, lambda: None)
        try:
            dialog._lmstudio_probe_finished(True, ["qwen/loaded-instance"], "")

            self.assertEqual(
                dialog.lm_model.currentText(),
                "qwen/loaded-instance",
            )
            self.assertEqual(dialog.lm_model.count(), 1)
        finally:
            dialog.close()

    def test_probe_lists_all_loaded_models_and_preserves_valid_selection(self):
        config = self._config()
        config.values[("LMSTUDIO", "model")] = "llama/loaded"
        dialog = SettingsDialog(config, lambda: None)
        try:
            dialog._lmstudio_probe_finished(
                True,
                ["qwen/loaded", "llama/loaded"],
                "",
            )

            self.assertEqual(
                [
                    dialog.lm_model.itemText(index)
                    for index in range(dialog.lm_model.count())
                ],
                ["qwen/loaded", "llama/loaded"],
            )
            self.assertEqual(dialog.lm_model.currentText(), "llama/loaded")
        finally:
            dialog.close()

    def test_save_persists_lmstudio_settings(self):
        config = self._config()
        dialog = SettingsDialog(config, lambda: None)
        try:
            self.assertTrue(hasattr(dialog, "lm_url"))
            dialog.lm_url.setText("http://127.0.0.1:1234/v1/")
            dialog.lm_key.setText("token")
            dialog.lm_model.setCurrentText("qwen/model")
            dialog._save()
        finally:
            dialog.close()

        section, values = next(item for item in config.saved if item[0] == "LMSTUDIO")
        self.assertEqual(section, "LMSTUDIO")
        self.assertEqual(values["base_url"], "http://127.0.0.1:1234/v1")
        self.assertEqual(values["api_key"], "token")
        self.assertEqual(values["model"], "qwen/model")


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class LmStudioMainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_engine_selector_exposes_lmstudio_provider(self):
        window = TranslatorQtWindow()
        try:
            self.assertGreaterEqual(window.engine_combo.findText("LM Studio"), 0)
            window.engine_combo.setCurrentText("LM Studio")
            self.assertEqual(window._translation_options().ai_provider, "lmstudio")
        finally:
            window.close()


class LmStudioJobTests(unittest.TestCase):
    def test_lmstudio_does_not_launch_koboldcpp(self):
        logs = []
        state = JobState(is_running=True)
        cache = mock.Mock(spec=TranslationCache)
        config = _Config(
            {
                ("AI", "ai_retries"): "3",
                ("GENERAL", "smart_glue"): "False",
                ("LMSTUDIO", "base_url"): "http://localhost:1234/v1",
                ("LMSTUDIO", "model"): "qwen/model",
            }
        )
        job = TranslationJob(
            config,
            cache,
            cache,
            state,
            on_log=lambda message, tag="white": logs.append((message, tag)),
            on_status=lambda *_args: None,
            on_row=lambda *_args: None,
        )
        options = TranslationOptions(
            mc_dir="/modpack",
            language_label="Русский",
            mc_version="1.20.1",
            output_mode="inplace",
            pack_name="MineAI_Pack",
            engine="ai",
            google_mode="single",
            ai_mode="safe",
            ai_batch=20,
            ai_provider="lmstudio",
            process_mode="append",
            translate_mods=True,
            translate_books=False,
            translate_quests=False,
        )

        with (
            mock.patch("mineai.runtime.job.discover_jar_files", return_value=[]),
            mock.patch(
                "mineai.runtime.job.discover_loose_lang_files",
                return_value=["en_us.json"],
            ),
            mock.patch("mineai.runtime.job.StringEstimator.estimate", return_value=1),
            mock.patch("mineai.runtime.job.TranslationService"),
            mock.patch("mineai.runtime.job.LooseJsonProcessor.process"),
            mock.patch.object(job.ai_launcher, "ensure_running") as launch,
        ):
            job.run_translation(options)

        launch.assert_not_called()
        self.assertTrue(any("LM Studio: qwen/model" in message for message, _ in logs))


if __name__ == "__main__":
    unittest.main()
