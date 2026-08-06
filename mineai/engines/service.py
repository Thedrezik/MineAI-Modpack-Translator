from collections import Counter
import re

from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.engines.kobold import KoboldEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    apply_smart_glue,
    is_technical_term,
    mask_protected_fragments,
)


_CJK_PATTERN = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uac00-\ud7af]"
)

_PROMPT_LEAK_MARKERS = (
    "no markers",
    "marker whitelist",
    "strict rules",
    "do not translate",
)


def _source_fingerprint(text: str) -> str:
    """Normalize representation only, without changing text semantics."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _can_cache_identity(original: str) -> bool:
    """Return whether an unchanged result is an intentional technical token."""
    stripped = original.strip()
    if not stripped:
        return False
    if is_technical_term(stripped):
        return True
    return bool(
        re.fullmatch(
            r"[A-Z0-9][A-Z0-9+./_:#-]{0,15}",
            stripped,
        )
    )


def _validate_candidate(
    item: EngineItem,
    candidate: object,
    target_lang: dict,
) -> tuple[bool, str | None, bool]:
    """Return accepted, rejection reason, and intentional-identity flag."""
    if not isinstance(candidate, str):
        return False, "ответ не является строкой", False

    if not candidate.strip():
        return False, "получена пустая строка", False

    lowered = candidate.casefold()
    for marker in _PROMPT_LEAK_MARKERS:
        if marker in lowered:
            return (
                False,
                f"в ответ попала служебная инструкция: {marker}",
                False,
            )

    expected_fragments = Counter(item.mapping.values())
    for fragment, expected_count in expected_fragments.items():
        actual_count = candidate.count(fragment)
        if actual_count != expected_count:
            return (
                False,
                "изменён защищённый фрагмент "
                f"{fragment!r}: ожидалось {expected_count}, "
                f"получено {actual_count}",
                False,
            )

    expected_literals = Counter(PLACEHOLDER_PATTERN.findall(item.original))
    actual_literals = Counter(PLACEHOLDER_PATTERN.findall(candidate))
    if actual_literals != expected_literals:
        return False, "изменены буквальные маркеры [#N#]", False

    same_as_source = candidate.strip() == item.original.strip()
    if same_as_source:
        if target_lang["api"] == "en":
            return True, None, False
        if _can_cache_identity(item.original):
            return True, None, True
        return False, "ответ совпадает с исходным текстом", False

    if not re.search(target_lang["regex"], candidate):
        return False, "в ответе нет символов целевого языка", False

    if target_lang["api"] == "ru" and _CJK_PATTERN.search(candidate):
        return False, "в русском переводе обнаружены CJK-символы", False

    return True, None, False


class TranslationService:
    """Prepares strings, uses cache, delegates to a translation engine."""

    def __init__(
        self,
        engine_name: str,
        cache: TranslationCache,
        config: ConfigManager,
        *,
        google_mode: str = "single",
        ai_mode: str = "safe",
        ai_batch: int = 20,
        ai_provider: str = "local",
    ) -> None:
        self.engine_name = engine_name
        self.cache = cache
        self.config = config
        self.google_mode = google_mode
        self.ai_mode = ai_mode
        self.ai_batch = ai_batch
        self.ai_provider = ai_provider

    def _build_engine(
        self,
        context: str = "",
        prompt_type: str = "mods",
    ) -> TranslationEngine:
        try:
            retries = self.config.getint("AI", "ai_retries")
        except Exception:
            retries = 3

        if self.engine_name == "google":
            return GoogleEngine(
                workers=self.config.getint("GENERAL", "google_workers", 5),
                mode=self.google_mode,
            )
        if self.engine_name == "deepl":
            return DeepLEngine(self.config.get("API", "deepl_key"))
        if self.ai_provider == "openrouter":
            return OpenRouterEngine(
                api_url=self.config.get("OPENROUTER", "api_url"),
                api_key=self.config.get("OPENROUTER", "api_key"),
                model=self.config.get("OPENROUTER", "model")
                or DEFAULT_OPENROUTER_MODEL,
                mode=self.ai_mode,
                context=context,
                prompt_type=prompt_type,
                retries=retries,
                site_url=self.config.get("OPENROUTER", "site_url"),
                app_name=self.config.get("OPENROUTER", "app_name"),
            )
        return KoboldEngine(
            mode=self.ai_mode,
            context=context,
            prompt_type=prompt_type,
            retries=retries,
        )

    def translate_dict(
        self,
        strings: dict[str, str],
        target_lang: dict,
        callbacks: EngineCallbacks,
        *,
        context: str = "",
        prompt_type: str = "mods",
    ) -> dict[str, str]:
        if not strings:
            return {}

        smart_glue = self.config.getboolean("GENERAL", "smart_glue")
        result: dict[str, str] = {}
        pending: dict[str, EngineItem] = {}
        source_owner: dict[str, str] = {}
        aliases: dict[str, list[str]] = {}
        accepted: set[str] = set()
        failure_reasons: dict[str, str] = {}
        cached_count = 0
        imported_count = 0
        deduplicated_count = 0

        def bump(n: int = 1) -> None:
            if callbacks.on_progress:
                callbacks.on_progress(n)

        def commit(owner_key: str, text: object, source_label: str) -> bool:
            item = pending[owner_key]
            accepted_value, reason, identity = _validate_candidate(
                item,
                text,
                target_lang,
            )

            if not accepted_value:
                failure_reasons[owner_key] = f"{source_label}: {reason}"
                candidate_preview = repr(text)[:120] if text is not None else "None"
                callbacks.on_log(
                    "❌ Отклонён перевод "
                    f"{item.original[:70]!r}: {reason}; "
                    f"ответ={candidate_preview}",
                    "red",
                )
                return False

            assert isinstance(text, str)
            output_keys = aliases[owner_key]
            for output_key in output_keys:
                result[output_key] = text
            accepted.add(owner_key)

            if identity:
                self.cache.set_identity(target_lang["api"], item.original)
                callbacks.on_log(
                    "   ↪ Оставлено без изменений и запомнено: "
                    f"{item.original[:70]}",
                    "dim",
                )
            else:
                self.cache.set(target_lang["api"], item.original, text)
                duplicate_suffix = (
                    f" ×{len(output_keys)}" if len(output_keys) > 1 else ""
                )
                callbacks.on_log(
                    f" > {item.original[:40]} -> "
                    f"{text[:40]}{duplicate_suffix}",
                    "dim",
                )

            bump(len(output_keys))
            return True

        def apply_engine_result(
            requested: dict[str, EngineItem],
            engine_result: dict[str, str],
            source_label: str,
        ) -> None:
            for key, text in engine_result.items():
                if key not in requested or key not in pending:
                    callbacks.on_log(
                        f"⚠️ {source_label} вернул неизвестный ключ: {key}",
                        "yellow",
                    )
                    continue
                commit(key, text, source_label)

            for key in requested:
                if key not in engine_result and key not in accepted:
                    failure_reasons[key] = (
                        f"{source_label}: движок не вернул результата"
                    )

        for key, text in strings.items():
            if not callbacks.should_run():
                break
            callbacks.wait_if_paused()

            if smart_glue:
                text = apply_smart_glue(text)

            masked, mapping = mask_protected_fragments(text)
            item = EngineItem(
                key=key,
                original=text,
                masked=masked,
                mapping=mapping,
            )

            hit, is_imported = self.cache.get(target_lang["api"], text)
            if hit is not None:
                valid, reason, _identity = _validate_candidate(
                    item,
                    hit,
                    target_lang,
                )
                if valid:
                    result[key] = hit
                    if is_imported:
                        imported_count += 1
                    else:
                        cached_count += 1
                    bump()
                    continue

                callbacks.on_log(
                    "⚠️ Невалидная запись кэша отброшена "
                    f"для {text[:70]!r}: {reason}",
                    "yellow",
                )
                self.cache.discard(
                    target_lang["api"],
                    text,
                    include_imported=is_imported,
                )

            if not masked:
                result[key] = text
                bump()
                continue

            fingerprint = _source_fingerprint(text)
            existing_owner = source_owner.get(fingerprint)
            if existing_owner is not None:
                aliases[existing_owner].append(key)
                deduplicated_count += 1
                continue

            source_owner[fingerprint] = key
            aliases[key] = [key]
            pending[key] = item

        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count} строк", "dim")
        if imported_count:
            callbacks.on_log(
                f"   📦 Из ресурс-паков: {imported_count} строк",
                "cyan",
            )
        if deduplicated_count:
            callbacks.on_log(
                "   ♻️ Повторяющиеся строки объединены: "
                f"{deduplicated_count}",
                "dim",
            )

        if not pending or not callbacks.should_run():
            return result

        engine = self._build_engine(context, prompt_type)
        is_ai = self.engine_name not in ("google", "deepl")
        max_chars = self.ai_batch * 100 if is_ai else 999999
        max_placeholders_per_batch = 30

        batches: list[dict[str, EngineItem]] = []
        current_batch: dict[str, EngineItem] = {}
        current_chars = 0
        current_placeholders = 0

        for key, item in pending.items():
            text_len = len(item.masked)
            placeholder_count = len(PLACEHOLDER_PATTERN.findall(item.masked))

            if is_ai and (placeholder_count > 15 or text_len > 800):
                if current_batch:
                    batches.append(current_batch)
                    current_batch = {}
                    current_chars = 0
                    current_placeholders = 0
                batches.append({key: item})
                continue

            if is_ai and current_batch and (
                (current_chars + text_len) > max_chars
                or (current_placeholders + placeholder_count)
                > max_placeholders_per_batch
            ):
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
                current_placeholders = 0

            current_batch[key] = item
            current_chars += text_len
            current_placeholders += placeholder_count

            if (
                (not is_ai and len(current_batch) >= 50)
                or (is_ai and len(current_batch) >= self.ai_batch)
            ):
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
                current_placeholders = 0

        if current_batch:
            batches.append(current_batch)

        for index, batch in enumerate(batches):
            if not callbacks.should_run():
                break
            if len(batches) > 1:
                callbacks.on_log(
                    f"📦 Отправка пачки {index + 1}/{len(batches)} "
                    f"({len(batch)} строк)...",
                    "dim",
                )
            batch_result = engine.translate_batch(batch, target_lang, callbacks)
            apply_engine_result(batch, batch_result, "основной движок")

        failed_pending = {
            key: item for key, item in pending.items() if key not in accepted
        }

        try:
            use_fallback = self.config.getboolean("AI", "fallback_google")
        except Exception:
            use_fallback = False

        if failed_pending and is_ai and use_fallback and callbacks.should_run():
            callbacks.on_log(
                f"🔄 ИИ не справился. Переводим {len(failed_pending)} "
                "строк через Google...",
                "cyan",
            )
            google_engine = GoogleEngine(
                workers=self.config.getint("GENERAL", "google_workers", 5),
                mode=self.google_mode,
            )
            google_translated = google_engine.translate_batch(
                failed_pending,
                target_lang,
                callbacks,
            )
            apply_engine_result(
                failed_pending,
                google_translated,
                "Google fallback",
            )
            accepted_by_google = sum(
                1 for key in failed_pending if key in accepted
            )
            if accepted_by_google:
                callbacks.on_log(
                    f"   ✅ Google fallback: принято "
                    f"{accepted_by_google}/{len(failed_pending)} строк",
                    "green",
                )
            if accepted_by_google < len(failed_pending):
                callbacks.on_log(
                    f"   ⚠️ Google fallback: не принято "
                    f"{len(failed_pending) - accepted_by_google} строк",
                    "yellow",
                )

        if is_ai and use_fallback and callbacks.should_run():
            still_failed_complex = {
                key: item
                for key, item in pending.items()
                if key not in accepted
                and len(PLACEHOLDER_PATTERN.findall(item.masked)) > 10
            }
            if still_failed_complex:
                callbacks.on_log(
                    f"🔀 {len(still_failed_complex)} сложных строк "
                    "(маркеры) → Google Translate",
                    "cyan",
                )
                google_fallback = GoogleEngine(
                    workers=self.config.getint("GENERAL", "google_workers", 5),
                    mode="single",
                )
                google_result = google_fallback.translate_batch(
                    still_failed_complex,
                    target_lang,
                    callbacks,
                )
                apply_engine_result(
                    still_failed_complex,
                    google_result,
                    "Google complex fallback",
                )

        for owner_key, item in pending.items():
            if owner_key in accepted:
                continue

            output_keys = aliases[owner_key]
            reason = failure_reasons.get(
                owner_key,
                "движок не вернул валидного результата",
            )
            callbacks.on_log(
                "⚠️ Строка не переведена: "
                f"{item.original[:90]!r}; "
                f"причина: {reason}; "
                "сохранён исходный текст",
                "yellow",
            )
            for output_key in output_keys:
                result[output_key] = item.original
            bump(len(output_keys))

        self.cache.save_if_threshold()
        return result
