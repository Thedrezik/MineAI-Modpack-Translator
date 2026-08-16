import tempfile
import unittest
import zipfile
from pathlib import Path

from mineai.engines.base import EngineCallbacks
from mineai.processors.jar import JarProcessor
from mineai.runtime.state import JobState
from mineai.text_processing import mask_protected_fragments, unmask_translation


TARGET_LANG = {
    "file": "ru_ru",
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Config:
    def getboolean(self, _section: str, _key: str) -> bool:
        return False


class _PayloadOnlyService:
    def __init__(self) -> None:
        self.config = _Config()
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        result: dict[str, str] = {}
        for key, source in strings.items():
            masked, mapping = mask_protected_fragments(source)
            translated = masked.replace(
                "ME Extended Interface is a",
                "МЕ Расширенный интерфейс — это",
            ).replace(
                "with a larger configuration inventory.",
                "с более обширным списком конфигураций.",
            )
            result[key] = unmask_translation(translated, mapping)
        return result


class _Writer:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, payload: bytes) -> None:
        self.files[path] = payload


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
        on_progress=lambda *_args: None,
    )


class MarkdownStructuralPipelineTests(unittest.TestCase):
    def test_processor_never_sends_block_structure_to_translation_service(self) -> None:
        source_path = "assets/ae2/ae2guide/structure-test.md"
        target_path = "assets/ae2/ae2guide/_ru_ru/structure-test.md"
        source_line = (
            '*   ME Extended Interface is a <ItemLink id="ae2:interface" /> '
            "with a larger configuration inventory.  "
        )
        expected_payload = (
            'ME Extended Interface is a <ItemLink id="ae2:interface" /> '
            "with a larger configuration inventory."
        )
        expected_output = (
            '*   МЕ Расширенный интерфейс — это <ItemLink id="ae2:interface" /> '
            "с более обширным списком конфигураций.  "
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            jar_path = Path(temp_dir) / "ae2.jar"
            with zipfile.ZipFile(jar_path, "w") as archive:
                archive.writestr(source_path, source_line.encode("utf-8"))
            original_jar = jar_path.read_bytes()

            state = JobState()
            state.start()
            service = _PayloadOnlyService()
            writer = _Writer()

            JarProcessor(service, state, _callbacks()).process(
                str(jar_path),
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="resourcepack",
                translate_mods=False,
                translate_books=True,
                pack_writer=writer,
            )

            self.assertEqual(service.calls, [{"0": expected_payload}])
            self.assertNotIn("*   ", service.calls[0]["0"])
            self.assertIn('<ItemLink id="ae2:interface" />', service.calls[0]["0"])
            self.assertIn(target_path, writer.files)
            self.assertNotIn(source_path, writer.files)
            self.assertEqual(
                writer.files[target_path].decode("utf-8"),
                expected_output,
            )
            self.assertEqual(jar_path.read_bytes(), original_jar)


if __name__ == "__main__":
    unittest.main()
