import io
import json
import unittest
import zipfile
from types import SimpleNamespace

from mineai.constants import LANGUAGES
from mineai.formatkit_bridge import (
    FORMATKIT_SOURCE_SHA,
    build_locale_output,
    plan_locale_work,
    target_path_for_locale,
)
from mineai.processors.formatkit_pilot import (
    FormatKitJarProcessor,
    FormatKitStringEstimator,
)


RU = LANGUAGES["Русский"]
EXPECTED_FORMATKIT_SHA = "5cfd1e28c1581caf144f9a5ef767c631d9199f8c"


class FormatKitBridgeTests(unittest.TestCase):
    def test_pilot_is_pinned_to_certified_formatkit_snapshot(self) -> None:
        self.assertEqual(FORMATKIT_SOURCE_SHA, EXPECTED_FORMATKIT_SHA)

    def test_append_reuses_safe_target_and_filters_technical_values(self) -> None:
        source = json.dumps(
            {
                "a": "Hello",
                "b": "Run /create with %s",
                "tech": "demo.resource_id",
            },
            ensure_ascii=False,
        )
        target = json.dumps(
            {
                "a": "Привет",
                "b": "Run /create with %s",
            },
            ensure_ascii=False,
        )
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target,
            "append",
        )
        self.assertIsNotNone(work)
        assert work is not None
        self.assertEqual(work.total_translatable, 2)
        self.assertEqual(set(work.pending), {"key:b"})
        self.assertNotIn("key:tech", work.pending)
        self.assertIn("[#", work.pending["key:b"])

        output = json.loads(build_locale_output(work, {"key:b": "Запустить [#0#] с [#1#]"}))
        self.assertEqual(output["a"], "Привет")
        self.assertIn("/create", output["b"])
        self.assertIn("%s", output["b"])
        self.assertEqual(output["tech"], "demo.resource_id")

    def test_unicode_prose_slash_is_not_protected_but_real_command_is(self) -> None:
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            '{"a":"Документация/Wiki and /create"}',
            "ru_ru",
            None,
            "force",
        )
        assert work is not None
        text = work.pending["key:a"]
        self.assertIn("Документация/Wiki", text)
        self.assertNotIn("/create", text)
        self.assertIn("[#", text)

    def test_identical_duplicate_key_is_one_unit_and_updates_all_aliases(self) -> None:
        source = '{\n  "same": "Hello",\n  "same": "Hello"\n}\n'
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            None,
            "append",
        )
        assert work is not None
        self.assertEqual(list(work.pending), ["key:same"])
        output = build_locale_output(work, {"key:same": "Привет"})
        self.assertEqual(output.count('"Привет"'), 2)

    def test_tempad_structured_component_keeps_color_and_index(self) -> None:
        source_value = [
            "",
            {"text": "Open Tempad", "color": "gold"},
            {"index": 1, "color": "gray"},
        ]
        source = json.dumps({"tempad.demo": source_value}, ensure_ascii=False)
        work = plan_locale_work(
            "assets/tempad/lang/en_us.json",
            source,
            "ru_ru",
            None,
            "append",
        )
        assert work is not None
        self.assertEqual(len(work.pending), 1)
        unit_id = next(iter(work.pending))
        output = json.loads(build_locale_output(work, {unit_id: "Открыть Темпад"}))
        self.assertEqual(output["tempad.demo"][1]["text"], "Открыть Темпад")
        self.assertEqual(output["tempad.demo"][1]["color"], "gold")
        self.assertEqual(output["tempad.demo"][2], {"index": 1, "color": "gray"})

    def test_serialized_component_exposes_only_visible_text_leaves(self) -> None:
        nested = json.dumps(
            [
                {"text": "There is an Update for "},
                {
                    "text": "Actually Additions",
                    "color": "dark_green",
                    "clickEvent": {"action": "open_url", "value": "%s"},
                },
            ],
            separators=(",", ":"),
        )
        source = json.dumps({"update.message": nested})
        work = plan_locale_work(
            "assets/actuallyadditions/lang/en_us.json",
            source,
            "ru_ru",
            None,
            "append",
        )
        assert work is not None
        self.assertEqual(len(work.pending), 2)
        translated = {}
        for unit_id, text in work.pending.items():
            translated[unit_id] = (
                "Доступно обновление для " if "There is" in text else "Actually Additions"
            )
        output = json.loads(build_locale_output(work, translated))
        decoded = json.loads(output["update.message"])
        self.assertEqual(decoded[0]["text"], "Доступно обновление для ")
        self.assertEqual(decoded[1]["color"], "dark_green")
        self.assertEqual(decoded[1]["clickEvent"], {"action": "open_url", "value": "%s"})

    def test_malformed_optional_target_is_discarded_not_structural_truth(self) -> None:
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            '{"a":"Hello"}',
            "ru_ru",
            '{"a":"Привет"',
            "append",
        )
        assert work is not None
        self.assertIsNotNone(work.target_parse_error)
        self.assertEqual(set(work.pending), {"key:a"})

    def test_mineai_skip_keeps_legacy_source_identical_pending_semantics(self) -> None:
        source = '{"a":"Hello","b":"World"}'
        target = '{"a":"Hello","b":"Мир"}'
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            target,
            "skip",
        )
        assert work is not None
        self.assertEqual(set(work.pending), {"key:a"})

    def test_config_locale_target_paths_are_exact_allow_list(self) -> None:
        self.assertEqual(
            target_path_for_locale("config/collapsiblegroups/lang/en_us.json", "ru_ru"),
            "config/collapsiblegroups/lang/ru_ru.json",
        )
        self.assertEqual(
            target_path_for_locale("config/jaopca/lang/en_us.json", "ru_ru"),
            "config/jaopca/lang/ru_ru.json",
        )
        self.assertIsNone(target_path_for_locale("config/random/lang/en_us.json", "ru_ru"))

    def test_missing_translation_result_falls_back_to_source_and_validates(self) -> None:
        source = '{"a":"Hello %s"}'
        work = plan_locale_work(
            "assets/demo/lang/en_us.json",
            source,
            "ru_ru",
            None,
            "append",
        )
        assert work is not None
        self.assertEqual(build_locale_output(work, {}), source)


class FormatKitPilotProcessorTests(unittest.TestCase):
    @staticmethod
    def _archive(source: str, target: str | None = None):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as out:
            out.writestr("assets/demo/lang/en_us.json", source)
            if target is not None:
                out.writestr("assets/demo/lang/ru_ru.json", target)
        data.seek(0)
        return data

    def test_estimator_uses_same_formatkit_pending_count(self) -> None:
        source = '{"a":"Hello","b":"Run /create with %s","tech":"demo.resource_id"}'
        target = '{"a":"Привет","b":"Run /create with %s"}'
        data = self._archive(source, target)
        with zipfile.ZipFile(data, "r") as archive:
            item = archive.getinfo("assets/demo/lang/en_us.json")
            locale = {entry.filename.lower(): entry for entry in archive.infolist()}
            estimator = FormatKitStringEstimator(SimpleNamespace())
            count = estimator._count_lang(
                archive,
                item,
                locale,
                "ru_ru.json",
                "append",
                RU["regex"],
            )
        self.assertEqual(count, 1)

    def test_resourcepack_processor_writes_validated_formatkit_output(self) -> None:
        source = '{\n  "a": "Hello",\n  "b": "Run /create with %s"\n}\n'
        target = '{"a":"Привет","b":"Run /create with %s"}'
        data = self._archive(source, target)

        class Service:
            def translate_dict(self, pending, _lang, _callbacks, **_kwargs):
                self.pending = dict(pending)
                unit_id = next(iter(pending))
                return {unit_id: "Запустить [#0#] с [#1#]"}

        class PackWriter:
            def __init__(self):
                self.writes = {}

            def write(self, path, payload):
                self.writes[path] = payload

        service = Service()
        writer = PackWriter()
        logs = []
        callbacks = SimpleNamespace(on_log=lambda text, color: logs.append((text, color)))
        state = SimpleNamespace(should_run=lambda: True)
        processor = FormatKitJarProcessor(service, state, callbacks)

        with zipfile.ZipFile(data, "r") as archive:
            item = archive.getinfo("assets/demo/lang/en_us.json")
            locale = {entry.filename.lower(): entry for entry in archive.infolist()}
            modified = processor._process_lang_entry(
                archive,
                None,
                item,
                locale,
                "ru_ru.json",
                RU,
                "append",
                "resourcepack",
                writer,
                "Demo",
                set(),
            )

        self.assertTrue(modified)
        payload = writer.writes["assets/demo/lang/ru_ru.json"].decode("utf-8")
        output = json.loads(payload)
        self.assertEqual(output["a"], "Привет")
        self.assertIn("/create", output["b"])
        self.assertIn("%s", output["b"])
        self.assertIn("[Интерфейс/FormatKit]", "\n".join(text for text, _ in logs))


if __name__ == "__main__":
    unittest.main()
