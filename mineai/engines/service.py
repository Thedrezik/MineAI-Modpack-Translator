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
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _count_fragment(text: str, fragment: str) -> int:
    """Считает вхождения фрагмента; буквенные фрагменты (II, III, RF...) —
    с границами слова, чтобы 'II' не находился внутри 'III'."""
    if re.fullmatch(r"[A-Za-z]{1,4}", fragment):
        pattern = r"(?<![A-Za-z])" + re.escape(fragment) + r"(?![A-Za-z])"
    else:
        pattern = re.escape(fragment)
    return len(re.findall(pattern, text))


def _can_cache_identity(original: str) -> bool:
    """True only for text that is clearly technical and intentionally unchanged."""
    stripped = original.strip()
    if not stripped:
        return False
    if is_technical_term(stripped):
        return True
    if re.fullmatch(r"[A-Z0-9][A-Z0-9+./_:#-]{0,15}", stripped):
        return True
    if stripped.startswith(("{", "[{")) and '"text"' in stripped:
        return True
    return False


def _validate_candidate(
    item: EngineItem,
    candidate: object,
    target_lang: dict,
) -> tuple[bool, str | None, bool]:
    """Return (accepted, rejection reason, intentional-identity flag)."""
    if not isinstance(candidate, str):
        return False, "ответ не является строкой", False
    if not candidate.strip():
        return False, "получена пустая строка", False
    lowered = candidate.casefold()
    for marker in _PROMPT_LEAK_MARKERS:
        if marker in lowered:
            return False, f"эхо промпта: {marker}", False
    for fragment, expected_count in Counter(item.mapping.values()).items():
        actual_count = _count_fragment(candidate, fragment)
        if actual_count != expected_count:
            return (
                False,
                f"изменён защищённый фрагмент {fragment!r}: "
                f"ожидалось {expected_count}, получено {actual_count}",
                False,
            )
    if Counter(PLACEHOLDER_PATTERN.findall(candidate)) != Counter(
        PLACEHOLDER_PATTERN.findall(item.original)
    ):
        return False, "изменены маркеры [#N#]", False
    same_as_source = candidate.strip() == item.original.strip()
    if same_as_source:
        if target_lang["api"] == "en":
            return True, None, False
        if _can_cache_identity(item.original):
            return True, None, True
        return False, "ответ совпадает с оригиналом", False
    if not re.search(target_lang["regex"], candidate):
        return False, "нет символов целевого языка", False
    if target_lang["api"] == "ru" and _CJK_PATTERN.search(candidate):
        return False, "CJK-символы в русском переводе", False
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
        self, context: str = "", prompt_type: str = "mods"
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

        def metric(name: str, n: int = 1) -> None:
            if callbacks.on_metric:
                callbacks.on_metric(name, n)

        def commit(owner_key: str, text: object, source_label: str) -> bool:
            item = pending[owner_key]
            ok, reason, identity = _validate_candidate(item, text, target_lang)
            if not ok:
                failure_reasons[owner_key] = f"{source_label}: {reason}"
                preview = repr(text)[:120] if text is not None else "None"
                callbacks.on_log(
                    f"❌ Отклонён {item.original[:70]!r}: {reason}; ответ={preview}",
                    "red",
                )
                return False
            assert isinstance(text, str)
            output_keys = aliases[owner_key]
            for key in output_keys:
                result[key] = text
            accepted.add(owner_key)
            if identity:
                self.cache.set_identity(target_lang["api"], item.original)
                metric("protected", len(output_keys))
            else:
                self.cache.set(target_lang["api"], item.original, text)
                metric("ok", len(output_keys))
                if "Google" in source_label:
                    metric("fallback", len(output_keys))
            dup = f" ×{len(output_keys)}" if len(output_keys) > 1 else ""
            callbacks.on_log(f" > {item.original[:40]} -> {text[:40]}{dup}", "dim")
            bump(len(output_keys))
            return True

        def apply_engine_result(
            requested: dict[str, EngineItem],
            engine_result: dict[str, str],
            source_label: str,
        ) -> None:
            for key, text in engine_result.items():
                if key not in requested or key not in pending:
                    continue
                commit(key, text, source_label)
            for key in requested:
                if key not in engine_result and key not in accepted:
                    failure_reasons[key] = f"{source_label}: движок не вернул результата"

        for key, text in strings.items():
            if not callbacks.should_run():
                break
            callbacks.wait_if_paused()
            if smart_glue:
                text = apply_smart_glue(text)
            masked, mapping = mask_protected_fragments(text)
            item = EngineItem(key=key, original=text, masked=masked, mapping=mapping)

            hit, is_imported = self.cache.get(target_lang["api"], text)
            if hit is not None:
                valid, reason, _id = _validate_candidate(item, hit, target_lang)
                if valid:
                    result[key] = hit
                    if is_imported:
                        imported_count += 1
                    else:
                        cached_count += 1
                    bump()
                    metric("ok")
                    metric("cached")
                    continue
                callbacks.on_log(
                    f"⚠️ Запись кэша отброшена для {text[:70]!r}: {reason}",
                    "yellow",
                )
                self.cache.discard(
                    target_lang["api"], text, include_imported=is_imported
                )

            if not masked:
                result[key] = text
                bump()
                metric("protected")
                continue

            fp = _source_fingerprint(text)
            owner = source_owner.get(fp)
            if owner is not None:
                aliases[owner].append(key)
                deduplicated_count += 1
                continue
            source_owner[fp] = key
            aliases[key] = [key]
            pending[key] = item

        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count}", "gray")
        if imported_count:
            callbacks.on_log(f"   📦 Из ресурс-паков: {imported_count}", "cyan")
        if deduplicated_count:
            callbacks.on_log(
                f"   ♻️ Дедупликация, объединены: {deduplicated_count}", "dim"
            )

        if not pending or not callbacks.should_run():
            return result

        engine = self._build_engine(context, prompt_type)
        is_ai = self.engine_name not in ("google", "deepl")
        max_chars = self.ai_batch * 100 if is_ai else 999999
        max_ph_per_batch = 20

        batches: list[dict[str, EngineItem]] = []
        cur: dict[str, EngineItem] = {}
        cur_chars = 0
        cur_ph = 0
        for key, item in pending.items():
            tlen = len(item.masked)
            ph = len(PLACEHOLDER_PATTERN.findall(item.masked))
            if is_ai and (ph > 15 or tlen > 800):
                if cur:
                    batches.append(cur)
                    cur, cur_chars, cur_ph = {}, 0, 0
                batches.append({key: item})
                continue
            if is_ai and cur and (
                cur_chars + tlen > max_chars or cur_ph + ph > max_ph_per_batch
            ):
                batches.append(cur)
                cur, cur_chars, cur_ph = {}, 0, 0
            cur[key] = item
            cur_chars += tlen
            cur_ph += ph
            if (not is_ai and len(cur) >= 50) or (
                is_ai and len(cur) >= self.ai_batch
            ):
                batches.append(cur)
                cur, cur_chars, cur_ph = {}, 0, 0
        if cur:
            batches.append(cur)

        for idx, batch in enumerate(batches):
            if not callbacks.should_run():
                break
            if len(batches) > 1:
                callbacks.on_log(
                    f"📦 Пачка {idx+1}/{len(batches)} ({len(batch)} строк)", "blue"
                )
            batch_result = engine.translate_batch(batch, target_lang, callbacks)
            apply_engine_result(batch, batch_result, "основной движок")

        failed_pending = {k: v for k, v in pending.items() if k not in accepted}
        try:
            use_fallback = self.config.getboolean("AI", "fallback_google")
        except Exception:
            use_fallback = False

        if failed_pending and is_ai and use_fallback and callbacks.should_run():
            callbacks.on_log(
                f"🔄 Fallback: {len(failed_pending)} строк → Google", "cyan"
            )
            ge = GoogleEngine(
                workers=self.config.getint("GENERAL", "google_workers", 5),
                mode=self.google_mode,
            )
            gt = ge.translate_batch(failed_pending, target_lang, callbacks)
            apply_engine_result(failed_pending, gt, "Google fallback")
            got = sum(1 for k in failed_pending if k in accepted)
            if got:
                callbacks.on_log(
                    f"   ✅ Google: {got}/{len(failed_pending)}", "green"
                )
            if got < len(failed_pending):
                rejected = len(failed_pending) - got
                suffix = "строка" if rejected == 1 else "строк"
                callbacks.on_log(
                    f"   ⚠️ Google: не принято {rejected} {suffix}", "yellow"
                )

        if is_ai and use_fallback and callbacks.should_run():
            complex_failed = {
                k: v
                for k, v in pending.items()
                if k not in accepted
                and len(PLACEHOLDER_PATTERN.findall(v.masked)) > 10
            }
            if complex_failed:
                callbacks.on_log(
                    f"🔀 {len(complex_failed)} сложных → Google", "cyan"
                )
                gf = GoogleEngine(
                    workers=self.config.getint("GENERAL", "google_workers", 5),
                    mode="single",
                )
                gr = gf.translate_batch(complex_failed, target_lang, callbacks)
                apply_engine_result(complex_failed, gr, "Google complex fallback")

        for owner_key, item in pending.items():
            if owner_key in accepted:
                continue
            output_keys = aliases[owner_key]
            reason = failure_reasons.get(owner_key, "нет результата")
            callbacks.on_log(
                f"⚠️ Строка не переведена: {item.original[:90]!r}; {reason}",
                "yellow",
            )
            for k in output_keys:
                result[k] = item.original
            bump(len(output_keys))
            metric("failed", len(output_keys))

        self.cache.save_if_threshold()
        return result
