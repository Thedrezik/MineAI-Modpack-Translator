from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.kobold import KoboldEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.glossary import SmartGlossary
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
        glossary: SmartGlossary | None = None,
    ) -> None:
        self.engine_name = engine_name
        self.cache = cache
        self.config = config
        self.google_mode = google_mode
        self.ai_mode = ai_mode
        self.ai_batch = ai_batch
        self.ai_provider = ai_provider
        self.glossary = glossary

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
                retries=retries,  # <--- НОВАЯ СТРОКА
                site_url=self.config.get("OPENROUTER", "site_url"),
                app_name=self.config.get("OPENROUTER", "app_name"),
            )
        return KoboldEngine(mode=self.ai_mode, context=context, prompt_type=prompt_type, retries=retries)  # <--- ДОБАВИЛИ retries=retries В КОНЕЦ

    def translate_dict(
        self,
        strings: dict[str, str],
        target_lang: dict,
        callbacks: EngineCallbacks,
        *,
        context: str = "",
        prompt_type: str = "mods",
        scope: str = "*",
    ) -> dict[str, str]:
        if not strings:
            return {}

        smart_glue = self.config.getboolean("GENERAL", "smart_glue")
        result: dict[str, str] = {}
        pending: dict[str, EngineItem] = {}
        cached_count = 0
        exact_count = 0
        term_count = 0
        cache_variant = self.glossary.fingerprint if self.glossary else ""

        for key, text in strings.items():
            if not callbacks.should_run():
                break
            callbacks.wait_if_paused()
            if smart_glue:
                text = apply_smart_glue(text)

            exact = self.glossary.exact_translation(text, scope) if self.glossary else None
            if exact is not None:
                result[key] = exact
                exact_count += 1
                continue

            hit = self.cache.get(target_lang["api"], text, variant=cache_variant)
            if hit is not None:
                result[key] = hit
                cached_count += 1
                continue

            masked, mapping = mask_protected_fragments(text)
            if self.glossary:
                masked, substitutions = self.glossary.mask_terms(masked, mapping, scope)
                term_count += substitutions
            if not masked:
                result[key] = text
                continue
            pending[key] = EngineItem(key=key, original=text, masked=masked, mapping=mapping)

        if exact_count:
            callbacks.on_log(f"   📖 Точных совпадений глоссария: {exact_count}", "dim")
        if term_count:
            callbacks.on_log(f"   🧩 Подставлено терминов: {term_count}", "dim")
        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count} строк", "dim")

        if not pending or not callbacks.should_run():
            return result

        engine = self._build_engine(context, prompt_type)
        
        # --- НОВЫЙ АЛГОРИТМ РАЗБИЕНИЯ НА ПАЧКИ ПО СИМВОЛАМ ---
        is_ai = self.engine_name not in ("google", "deepl")  # <--- ИЗМЕНИЛИ ЭТУ СТРОКУ (теперь ИИ определяется правильно)
        # Лимит символов: размер пачки * 100. Например, 20 строк = 2000 символов макс.
        max_chars = self.ai_batch * 100 if is_ai else 999999
        
        batches = []
        current_batch = {}
        current_chars = 0
        
        for k, item in pending.items():
            text_len = len(item.original)
            
            # Если пачка переполнена по символам - сохраняем и начинаем новую
            if is_ai and current_batch and (current_chars + text_len) > max_chars:
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
                
            current_batch[k] = item
            current_chars += text_len
            
            # Если пачка переполнена по количеству строк - тоже сохраняем
            if (not is_ai and len(current_batch) >= 50) or (is_ai and len(current_batch) >= self.ai_batch):
                batches.append(current_batch)
                current_batch = {}
                current_chars = 0
                
        if current_batch:
            batches.append(current_batch)
            
        translated = {}
        for i, batch in enumerate(batches):
            if not callbacks.should_run():
                break
            # Выводим в лог информацию, если пачек больше одной
            if len(batches) > 1:
                callbacks.on_log(f"📦 Отправка пачки {i+1}/{len(batches)} ({len(batch)} строк)...", "dim")
            
            batch_result = engine.translate_batch(batch, target_lang, callbacks)
            translated.update(batch_result)
        # --- КОНЕЦ НОВОГО АЛГОРИТМА ---

        # --- НОВЫЙ БЛОК: ПОДСТРАХОВКА GOOGLE ---
        failed_pending = {k: v for k, v in pending.items() if k not in translated}
        
        try:
            use_fallback = self.config.getboolean("AI", "fallback_google")
        except Exception:
            use_fallback = False
            
        if failed_pending and is_ai and use_fallback and callbacks.should_run():
            callbacks.on_log(f"🔄 ИИ не справился. Переводим {len(failed_pending)} строк через Google...", "cyan")
            google_engine = GoogleEngine(
                workers=self.config.getint("GENERAL", "google_workers", 5),
                mode=self.google_mode,
            )
            google_translated = google_engine.translate_batch(failed_pending, target_lang, callbacks)
            
            # Добавляем успешные переводы Google в общий словарь (они автоматически пойдут в кэш!)
            translated.update(google_translated)
        # --- КОНЕЦ БЛОКА ПОДСТРАХОВКИ ---

        for key, text in translated.items():
            original = pending[key].original
            result[key] = text
            self.cache.set(
                target_lang["api"],
                original,
                text,
                variant=cache_variant,
            )
            callbacks.on_log(f" > {original[:40]} -> {text[:40]}", "dim")

        for key, item in pending.items():
            if key not in translated:
                result[key] = item.original

        self.cache.save_if_threshold()
        return result