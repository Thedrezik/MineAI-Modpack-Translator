import re
from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.kobold import KoboldEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.text_processing import apply_smart_glue, mask_protected_fragments


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

    def _build_engine(self, context: str = "", prompt_type: str = "mods") -> TranslationEngine:
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
                model=self.config.get("OPENROUTER", "model") or DEFAULT_OPENROUTER_MODEL,
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
        cached_count = 0
        imported_count = 0
        translated: dict[str, str] = {}

        def bump(n: int = 1) -> None:
            if callbacks.on_progress:
                callbacks.on_progress(n)

        def is_acceptable(text: str, original: str) -> bool:
            if not isinstance(text, str) or not text.strip():
                return False
            low = text.lower()
            if any(
                marker in low
                for marker in (
                    "no markers",
                    "marker whitelist",
                    "strict rules",
                    "do not translate",
                )
            ):
                return False
            if text.strip() == original.strip():
                return target_lang["api"] == "en"
            return bool(re.search(target_lang["regex"], text))

        def commit(key: str, text: str) -> bool:
            original = pending[key].original
            if not is_acceptable(text, original):
                return False
            result[key] = text
            translated[key] = text
            self.cache.set(target_lang["api"], original, text)
            callbacks.on_log(f" > {original[:40]} -> {text[:40]}", "dim")
            bump()
            return True

        for key, text in strings.items():
            if not callbacks.should_run():
                break
            callbacks.wait_if_paused()
            if smart_glue:
                text = apply_smart_glue(text)

            hit, is_imported = self.cache.get(target_lang["api"], text)
            if hit is not None:
                result[key] = hit
                if is_imported:
                    imported_count += 1
                else:
                    cached_count += 1
                bump()
                continue
            masked, mapping = mask_protected_fragments(text)
            if not masked:
                result[key] = text
                bump()
                continue
            pending[key] = EngineItem(
                key=key,
                original=text,
                masked=masked,
                mapping=mapping,
            )

        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count} строк", "dim")
        if imported_count:
            callbacks.on_log(
                f"   📦 Из ресурс-паков: {imported_count} строк",
                "cyan",
            )

        if not pending or not callbacks.should_run():
            return result

        engine = self._build_engine(context, prompt_type)
        is_ai = self.engine_name not in ("google", "deepl")
        max_chars = self.ai_batch * 100 if is_ai else 999999
        max_placeholders_per_batch = 30

        from mineai.text_processing import PLACEHOLDER_PATTERN

        batches = []
        current_batch = {}
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
            for key, text in batch_result.items():
                commit(key, text)

        failed_pending = {
            key: value for key, value in pending.items() if key not in translated
        }

        try:
            use_fallback = self.config.getboolean("AI", "fallback_google")
        except Exception:
            use_fallback = False

        if (
            failed_pending
            and is_ai
            and use_fallback
            and callbacks.should_run()
        ):
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
            for key, text in google_translated.items():
                commit(key, text)

        if is_ai and use_fallback and callbacks.should_run():
            still_failed_complex = {
                key: value
                for key, value in pending.items()
                if key not in translated
                and len(PLACEHOLDER_PATTERN.findall(value.masked)) > 10
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
                for key, text in google_result.items():
                    commit(key, text)

        for key, item in pending.items():
            if key not in translated:
                result[key] = item.original
                bump()
        self.cache.save_if_threshold()
        return result
