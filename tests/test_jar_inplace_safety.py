import json
import os
import stat
import tempfile
import unittest
from unittest import mock
import zipfile

from mineai.engines.base import EngineCallbacks
from mineai.processors.jar import JarProcessor
from mineai.runtime.state import JobState


TARGET_LANG = {
    "api": "ru",
    "file": "ru_ru",
    "name": "Russian",
    "regex": r"[А-Яа-я]",
}


class _Service:
    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        return {key: "Привет" for key in strings}


def _callbacks(logs):
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda *_args: None,
    )


def _write_jar(path):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "assets/example/lang/en_us.json",
            json.dumps({"example.hello": "Hello"}),
        )
        archive.writestr("assets/example/data.txt", "unchanged")


class JarInplaceSafetyTests(unittest.TestCase):
    def test_legacy_target_lang_is_replaced_without_duplicate_zip_entry(self):
        state = JobState(is_running=True)
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "legacy.jar")
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "assets/example/lang/en_us.lang",
                    "example.hello=Hello\n",
                )
                archive.writestr(
                    "assets/example/lang/ru_ru.lang",
                    "example.hello=Старый перевод\n",
                )

            JarProcessor(_Service(), state, _callbacks(logs)).process(
                path,
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="inplace",
                translate_mods=True,
                translate_books=False,
                pack_writer=None,
            )

            with zipfile.ZipFile(path) as archive:
                target = "assets/example/lang/ru_ru.lang"
                self.assertEqual(archive.namelist().count(target), 1)
                self.assertIn("Привет", archive.read(target).decode("utf-8"))

    def test_valid_temp_archive_atomically_replaces_original(self):
        state = JobState(is_running=True)
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "example.jar")
            _write_jar(path)
            os.chmod(path, 0o640)
            expected_mode = stat.S_IMODE(os.stat(path).st_mode)

            written_path = JarProcessor(_Service(), state, _callbacks(logs)).process(
                path,
                target_lang=TARGET_LANG,
                mode="force",
                output_mode="inplace",
                translate_mods=True,
                translate_books=False,
                pack_writer=None,
            )

            self.assertEqual(written_path, path)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.testzip(), None)
                self.assertIn("assets/example/lang/ru_ru.json", archive.namelist())
                translated = json.loads(
                    archive.read("assets/example/lang/ru_ru.json").decode("utf-8")
                )
                self.assertEqual(translated["example.hello"], "Привет")
            self.assertEqual(
                stat.S_IMODE(os.stat(path).st_mode),
                expected_mode,
            )
            self.assertFalse(os.path.exists(path + ".temp"))

    def test_validation_failure_preserves_original_jar(self):
        state = JobState(is_running=True)
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "example.jar")
            _write_jar(path)
            with open(path, "rb") as handle:
                original = handle.read()
            processor = JarProcessor(_Service(), state, _callbacks(logs))

            with mock.patch.object(
                processor,
                "_validate_inplace_archive",
                side_effect=zipfile.BadZipFile("invalid temp archive"),
            ):
                with self.assertRaisesRegex(
                    zipfile.BadZipFile, "invalid temp archive"
                ):
                    processor.process(
                        path,
                        target_lang=TARGET_LANG,
                        mode="force",
                        output_mode="inplace",
                        translate_mods=True,
                        translate_books=False,
                        pack_writer=None,
                    )

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original)
            self.assertFalse(os.path.exists(path + ".temp"))

    def test_unexpected_processing_error_cleans_temp_and_propagates(self):
        state = JobState(is_running=True)
        logs = []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "example.jar")
            _write_jar(path)
            with open(path, "rb") as handle:
                original = handle.read()
            processor = JarProcessor(_Service(), state, _callbacks(logs))

            with mock.patch.object(
                processor,
                "_process_lang_entry",
                side_effect=RuntimeError("translation failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "translation failure"):
                    processor.process(
                        path,
                        target_lang=TARGET_LANG,
                        mode="force",
                        output_mode="inplace",
                        translate_mods=True,
                        translate_books=False,
                        pack_writer=None,
                    )

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), original)
            self.assertFalse(os.path.exists(path + ".temp"))


if __name__ == "__main__":
    unittest.main()
