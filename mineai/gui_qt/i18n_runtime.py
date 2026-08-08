"""Localized dynamic strings used by readiness and dashboard metrics."""

from __future__ import annotations

from mineai.gui_qt.i18n import translator


_TEXT = {
    "ru": {
        "engine.local": "Локальный ИИ",
        "ready.google": "Google готов",
        "ready.deepl": "DeepL API настроен",
        "ready.deepl_missing": "Не указан API-ключ DeepL",
        "ready.openrouter_key": "Не указан ключ OpenRouter",
        "ready.openrouter_model": "Не выбрана модель OpenRouter",
        "ready.local_model": "Не выбрана локальная GGUF-модель",
        "ready.local_unavailable": "Файл GGUF-модели недоступен",
        "ready.unknown": "Неизвестный движок",
        "stats.processed_share": "{percent:.1f}% от обработанных",
        "stats.done": "готово",
        "stats.remaining_lines": "≈ {count} строк",
        "stats.rate": "{rate:.0f} строк/мин",
    },
    "en": {
        "engine.local": "Local AI",
        "ready.google": "Google ready",
        "ready.deepl": "DeepL API configured",
        "ready.deepl_missing": "DeepL API key is missing",
        "ready.openrouter_key": "OpenRouter key is missing",
        "ready.openrouter_model": "OpenRouter model is not selected",
        "ready.local_model": "Local GGUF model is not selected",
        "ready.local_unavailable": "GGUF model file is unavailable",
        "ready.unknown": "Unknown engine",
        "stats.processed_share": "{percent:.1f}% of processed",
        "stats.done": "done",
        "stats.remaining_lines": "≈ {count} lines",
        "stats.rate": "{rate:.0f} lines/min",
    },
}


def tr(key: str, **kwargs) -> str:
    language = translator.language if translator.language in _TEXT else "ru"
    template = _TEXT[language].get(key, _TEXT["ru"].get(key, key))
    return template.format(**kwargs) if kwargs else template
