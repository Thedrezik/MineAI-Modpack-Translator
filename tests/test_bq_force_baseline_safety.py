import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mineai.engines.base import EngineCallbacks
from mineai.processors.bq_baseline import write_bq_baseline_state
from mineai.processors.bq_json import BQProcessor
from mineai.processors.estimator import StringEstimator
from mineai.runtime.state import JobState


TARGET_LANG = {
    "api": "ru",
    "file": "ru_ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Service:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def translate_dict(self, strings, _target_lang, _callbacks, **_kwargs):
        self.calls.append(dict(strings))
        return {
            key: f"Перевод: {value}"
            for key, value in strings.items()
        }


def _callbacks(state: JobState) -> EngineCallbacks:
    return EngineCallbacks(
        should_run=state.should_run,
        wait_if_paused=state.wait_if_paused,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
    )


def _quest(name: str, desc: str | None = None, *, version: int = 1) -> dict:
    bq = {"name:8": name}
    if desc is not None:
        bq["desc:8"] = desc
    return {
        "properties:10": {"betterquesting:10": bq},
        "modpack_version": version,
        "preserved_payload": {"keep": version},
    }


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class BetterQuestingForceBaselineSafetyTests(unittest.TestCase):
    def test_tracked_stale_backup_uses_updated_current_and_preserves_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Quest.json"
            backup = Path(str(path) + ".bak")

            old_source = _quest("Old title", version=1)
            _write(path, old_source)
            shutil.copy2(path, backup)

            _write(path, _quest("Старый перевод", version=1))
            write_bq_baseline_state(str(path))

            updated_source = _quest(
                "Updated title",
                "Brand new objective",
                version=2,
            )
            updated_bytes = json.dumps(
                updated_source,
                ensure_ascii=False,
            ).encode("utf-8")
            path.write_bytes(updated_bytes)

            state = JobState(is_running=True)
            estimator = StringEstimator(state)
            self.assertEqual(
                estimator._estimate_bq(
                    str(path),
                    "force",
                    TARGET_LANG["regex"],
                ),
                2,
            )

            service = _Service()
            BQProcessor(service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(
                service.calls,
                [{
                    "name:8": "Updated title",
                    "desc:8": "Brand new objective",
                }],
            )
            output = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(output["modpack_version"], 2)
            self.assertEqual(output["preserved_payload"], {"keep": 2})
            bq = output["properties:10"]["betterquesting:10"]
            self.assertEqual(bq["name:8"], "Перевод: Updated title")
            self.assertEqual(
                bq["desc:8"],
                "Перевод: Brand new objective",
            )

            self.assertEqual(backup.read_bytes(), updated_bytes)
            old_hash = hashlib.sha256(
                json.dumps(old_source, ensure_ascii=False).encode("utf-8")
            ).hexdigest()[:12]
            stale = Path(f"{backup}.stale-{old_hash}")
            self.assertTrue(stale.exists())
            self.assertEqual(
                json.loads(stale.read_text(encoding="utf-8")),
                old_source,
            )
            self.assertTrue(Path(str(path) + ".bak.mineai").exists())

            second_service = _Service()
            BQProcessor(second_service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )
            self.assertEqual(
                second_service.calls,
                [{
                    "name:8": "Updated title",
                    "desc:8": "Brand new objective",
                }],
            )

    def test_legacy_structurally_matching_backup_keeps_force_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Quest.json"
            backup = Path(str(path) + ".bak")
            source = _quest("Original title", "Original objective", version=1)
            translated = _quest(
                "Предыдущий перевод",
                "Предыдущее описание",
                version=1,
            )
            _write(backup, source)
            _write(path, translated)

            state = JobState(is_running=True)
            service = _Service()
            BQProcessor(service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(
                service.calls,
                [{
                    "name:8": "Original title",
                    "desc:8": "Original objective",
                }],
            )
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8")),
                source,
            )
            self.assertEqual(
                list(Path(temp_dir).glob("Quest.json.bak.stale-*")),
                [],
            )
            self.assertTrue(Path(str(path) + ".bak.mineai").exists())

    def test_legacy_unverified_backup_fails_safe_to_current_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Quest.json"
            backup = Path(str(path) + ".bak")
            old_source = _quest("Legacy title", version=1)
            updated_source = _quest(
                "Current title",
                "Current objective",
                version=3,
            )
            _write(backup, old_source)
            _write(path, updated_source)

            state = JobState(is_running=True)
            service = _Service()
            BQProcessor(service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(
                service.calls[0],
                {
                    "name:8": "Current title",
                    "desc:8": "Current objective",
                },
            )
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8")),
                updated_source,
            )
            stale_files = list(Path(temp_dir).glob("Quest.json.bak.stale-*"))
            self.assertEqual(len(stale_files), 1)
            self.assertEqual(
                json.loads(stale_files[0].read_text(encoding="utf-8")),
                old_source,
            )

    def test_unchanged_tracked_output_still_forces_from_english_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Quest.json"
            backup = Path(str(path) + ".bak")
            source = _quest("Original title", "Original objective")
            _write(backup, source)
            _write(
                path,
                _quest("Предыдущий перевод", "Предыдущее описание"),
            )
            write_bq_baseline_state(str(path))

            state = JobState(is_running=True)
            service = _Service()
            BQProcessor(service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(
                service.calls,
                [{
                    "name:8": "Original title",
                    "desc:8": "Original objective",
                }],
            )
            self.assertEqual(
                list(Path(temp_dir).glob("Quest.json.bak.stale-*")),
                [],
            )

    def test_corrupt_state_never_restores_structurally_stale_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "Quest.json"
            backup = Path(str(path) + ".bak")
            _write(backup, _quest("Old title"))
            _write(path, _quest("Updated title", "New field", version=2))
            Path(str(path) + ".bak.mineai").write_text(
                "not-json",
                encoding="utf-8",
            )

            state = JobState(is_running=True)
            service = _Service()
            BQProcessor(service, state, _callbacks(state)).process(
                str(path),
                target_lang=TARGET_LANG,
                mode="force",
            )

            self.assertEqual(
                service.calls[0],
                {
                    "name:8": "Updated title",
                    "desc:8": "New field",
                },
            )
            self.assertEqual(
                json.loads(backup.read_text(encoding="utf-8"))["modpack_version"],
                2,
            )


if __name__ == "__main__":
    unittest.main()
