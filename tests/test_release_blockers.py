import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.engines.base import EngineItem
        from mineai.engines.service import _validate_candidate
        from mineai.gui.lifecycle import install_graceful_close
        from mineai.processors.translation_state import (
            collect_bq_selection_with_baseline,
            collect_snbt_selection_with_baseline,
        )
    finally:
        os.chdir(_original_cwd)


class SameScriptValidationTests(unittest.TestCase):
    def test_valid_latin_translations_without_diacritics_are_accepted(self) -> None:
        item = EngineItem(
            key="item",
            original="Iron Sword",
            masked="Iron Sword",
        )
        cases = [
            ({"api": "es", "regex": r"[áéíóúüñÁÉÍÓÚÜÑ]"}, "Espada de hierro"),
            ({"api": "de", "regex": r"[äöüßÄÖÜẞ]"}, "Eisenschwert"),
            ({"api": "fr", "regex": r"[àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ]"}, "Epee en fer"),
            ({"api": "pt", "regex": r"[ãõáéíóúâêôÃÕÁÉÍÓÚÂÊÔ]"}, "Espada de ferro"),
            ({"api": "it", "regex": r"[àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ]"}, "Spada di ferro"),
            ({"api": "pl", "regex": r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"}, "Miecz z zelaza"),
        ]

        for target_lang, translated in cases:
            with self.subTest(api=target_lang["api"]):
                accepted, reason, identity = _validate_candidate(
                    item,
                    translated,
                    target_lang,
                )
                self.assertTrue(accepted, reason)
                self.assertIsNone(reason)
                self.assertFalse(identity)

    def test_same_script_translation_still_rejects_unchanged_source(self) -> None:
        item = EngineItem(
            key="item",
            original="Iron Sword",
            masked="Iron Sword",
        )
        accepted, reason, identity = _validate_candidate(
            item,
            "Iron Sword",
            {"api": "es", "regex": r"[áéíóúüñÁÉÍÓÚÜÑ]"},
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "ответ совпадает с оригиналом")
        self.assertFalse(identity)

    def test_same_script_translation_rejects_cjk_leakage(self) -> None:
        item = EngineItem(
            key="item",
            original="Iron Sword",
            masked="Iron Sword",
        )
        accepted, reason, identity = _validate_candidate(
            item,
            "Espada de hierro 村民",
            {"api": "es", "regex": r"[áéíóúüñÁÉÍÓÚÜÑ]"},
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "CJK-символы в латинском переводе")
        self.assertFalse(identity)

    def test_distinct_script_validation_remains_strict(self) -> None:
        item = EngineItem(
            key="item",
            original="Iron Sword",
            masked="Iron Sword",
        )
        accepted, reason, _identity = _validate_candidate(
            item,
            "Iron Blade",
            {"api": "ru", "regex": r"[А-Яа-яЁё]"},
        )
        self.assertFalse(accepted)
        self.assertEqual(reason, "нет символов целевого языка")


class SameScriptQuestBaselineTests(unittest.TestCase):
    @staticmethod
    def _bq(name: str, desc: str) -> dict:
        return {
            "properties:10": {
                "betterquesting:10": {
                    "name:8": name,
                    "desc:8": desc,
                }
            }
        }

    def test_bq_preserves_previous_latin_translation_without_accents(self) -> None:
        original = self._bq("Iron Sword", "New description")
        current = self._bq("Espada de hierro", "Nueva descripcion")
        selection = collect_bq_selection_with_baseline(
            current,
            "append",
            r"[áéíóúüñÁÉÍÓÚÜÑ]",
            original_data=original,
            same_latin_script=True,
        )
        self.assertEqual(selection.total_translatable, 2)
        self.assertEqual(selection.pending, {})

    def test_bq_still_picks_up_unchanged_source_field(self) -> None:
        original = self._bq("Iron Sword", "New description")
        current = self._bq("Espada de hierro", "New description")
        selection = collect_bq_selection_with_baseline(
            current,
            "append",
            r"[áéíóúüñÁÉÍÓÚÜÑ]",
            original_data=original,
            same_latin_script=True,
        )
        self.assertEqual(selection.pending, {"desc:8": "New description"})

    def test_snbt_preserves_previous_latin_translation_without_accents(self) -> None:
        original = '{title:"Iron Sword",description:["Old objective"]}'
        current = '{title:"Espada de hierro",description:["Objetivo antiguo"]}'
        selection = collect_snbt_selection_with_baseline(
            original,
            current,
            "append",
            r"[áéíóúüñÁÉÍÓÚÜÑ]",
            same_latin_script=True,
        )
        self.assertEqual(selection.total_translatable, 2)
        self.assertEqual(selection.pending, [])

    def test_snbt_new_trailing_source_entry_is_still_pending(self) -> None:
        original = '{title:"Iron Sword",description:["Old objective"]}'
        current = (
            '{title:"Espada de hierro",description:'
            '["Objetivo antiguo","New objective"]}'
        )
        selection = collect_snbt_selection_with_baseline(
            original,
            current,
            "append",
            r"[áéíóúüñÁÉÍÓÚÜÑ]",
            same_latin_script=True,
        )
        self.assertEqual(selection.total_translatable, 3)
        self.assertEqual(selection.pending, ["New objective"])


class GracefulCloseTests(unittest.TestCase):
    @staticmethod
    def _app(active_job):
        app = SimpleNamespace()
        app._job = active_job
        app.protocol = mock.Mock()
        app.after = mock.Mock()
        app.destroy = mock.Mock()
        app.set_status = mock.Mock()
        app.job_state = mock.Mock()
        return app

    def test_window_close_stops_active_job_and_waits_for_cleanup(self) -> None:
        active_job = mock.Mock()
        app = self._app(active_job)
        install_graceful_close(app)

        app.protocol.assert_called_once()
        protocol_name, on_close = app.protocol.call_args.args
        self.assertEqual(protocol_name, "WM_DELETE_WINDOW")

        on_close()
        active_job.stop.assert_called_once_with()
        app.job_state.stop.assert_not_called()
        app.destroy.assert_not_called()
        app.set_status.assert_called_once_with("🛑 Завершение работы...", 1.0)

        app.after.assert_called_once()
        delay, poll = app.after.call_args.args
        self.assertEqual(delay, 50)

        app._job = None
        poll()
        app.destroy.assert_called_once_with()

    def test_idle_window_closes_immediately(self) -> None:
        app = self._app(None)
        install_graceful_close(app)
        on_close = app.protocol.call_args.args[1]

        on_close()

        app.job_state.stop.assert_called_once_with()
        app.destroy.assert_called_once_with()
        app.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
