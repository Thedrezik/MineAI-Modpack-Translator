import os
import tempfile
import unittest
from unittest import mock

_original_cwd = os.getcwd()
with tempfile.TemporaryDirectory() as _import_cwd:
    os.chdir(_import_cwd)
    try:
        from mineai.config import ConfigManager
        from mineai.engines.base import EngineCallbacks, TranslationEngine
        from mineai.engines.service import TranslationService
    finally:
        os.chdir(_original_cwd)


TARGET_LANG = {
    "api": "ru",
    "name": "Russian",
    "regex": r"[А-Яа-яЁё]",
}


class _Cache:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get(self, api_code: str, source: str):
        value = self.values.get((api_code, source))
        return value, False

    def set(self, api_code: str, source: str, translated: str) -> None:
        self.values[(api_code, source)] = translated

    def save_if_threshold(self) -> None:
        pass


class _Config:
    def __init__(self, fallback_google: bool) -> None:
        self.fallback_google = fallback_google

    def getboolean(self, section: str, key: str) -> bool:
        if (section, key) == ("GENERAL", "smart_glue"):
            return False
        if (section, key) == ("AI", "fallback_google"):
            return self.fallback_google
        raise AssertionError((section, key))

    def getint(self, _section: str, _key: str, fallback: int = 0) -> int:
        return fallback


class _FailingAiEngine(TranslationEngine):
    def translate_batch(self, items, target_lang, callbacks):
        return {}


class _AiService(TranslationService):
    def _build_engine(self, context: str = "", prompt_type: str = "mods"):
        return _FailingAiEngine()


def _callbacks() -> EngineCallbacks:
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda _message, _tag: None,
        on_status=lambda _message: None,
        on_progress=lambda _count: None,
    )


class ServiceFallbackTests(unittest.TestCase):
    def test_ai_defaults_are_available_for_existing_settings_files(self) -> None:
        self.assertEqual(ConfigManager._DEFAULTS["AI"]["ai_retries"], "3")
        self.assertEqual(ConfigManager._DEFAULTS["AI"]["fallback_google"], "False")

    def test_disabled_fallback_never_creates_google_for_complex_markers(self) -> None:
        service = _AiService("ai", _Cache(), _Config(False))
        complex_source = " ".join(["%s"] * 11) + " Complex value"

        with mock.patch("mineai.engines.service.GoogleEngine") as google_engine:
            result = service.translate_dict(
                {"complex": complex_source},
                TARGET_LANG,
                _callbacks(),
            )

        self.assertEqual(result, {"complex": complex_source})
        google_engine.assert_not_called()

    def test_enabled_fallback_allows_google_for_failed_ai_output(self) -> None:
        service = _AiService("ai", _Cache(), _Config(True))
        google = mock.Mock()
        google.translate_batch.return_value = {"key": "Перевод"}

        with mock.patch(
            "mineai.engines.service.GoogleEngine",
            return_value=google,
        ) as google_engine:
            result = service.translate_dict(
                {"key": "Original value"},
                TARGET_LANG,
                _callbacks(),
            )

        self.assertEqual(result, {"key": "Перевод"})
        google_engine.assert_called_once()
        google.translate_batch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
