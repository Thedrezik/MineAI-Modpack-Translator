from collections import Counter
from collections.abc import Callable
import re
import requests
from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.constants import DEFAULT_OPENROUTER_MODEL
from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.engines.kobold import KoboldEngine
from mineai.engines.lmstudio import LmStudioEngine
from mineai.engines.openrouter import OpenRouterEngine
from mineai.formats.rich_text import (
    contains_unsafe_formatting,
    parse_rich_text,
)
from formatkit.contracts import ANCHOR_PATTERN, FormatValidationError
from mineai.language_validation import (
    has_long_untranslated_english_fragment,
    has_untranslated_leading_article,
    requires_target_script_marker,
    uses_same_latin_script,
)
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    apply_smart_glue,
    count_line_breaks,
    is_article_removed_technical_translation,
    is_technical_term,
    mask_protected_fragments,
    structural_fragments,
    translation_length_issue,
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


def _scoped_cache_source(text: str, scope: str) -> str:
    if not scope:
        return text
    return f"␞{scope}␟{text}"


def _fragment_pattern(fragment: str) -> str:
    if re.fullmatch(r"[A-Za-z]{1,4}", fragment):
        return r"(?<![A-Za-z])" + re.escape(fragment) + r"(?![A-Za-z])"
    return re.escape(fragment)


def _count_protected_fragments(
    text: str,
    fragments: list[str],
) -> Counter:
    """Count longest fragments first so RF inside RF/t is not counted twice."""
    counts: Counter = Counter()
    occupied = [False] * len(text)
    for fragment in sorted(set(fragments), key=lambda value: (-len(value), value)):
        for match in re.finditer(_fragment_pattern(fragment), text):
            start, end = match.span()
            if any(occupied[start:end]):
                continue
            occupied[start:end] = [True] * (end - start)
            counts[fragment] += 1
    return counts


def _protected_fragment_sequence(
    text: str,
    fragments: list[str],
) -> tuple[str, ...]:
    """Return non-overlapping protected fragments in their textual order."""
    occupied = [False] * len(text)
    located: list[tuple[int, str]] = []
    for fragment in sorted(set(fragments), key=lambda value: (-len(value), value)):
        for match in re.finditer(_fragment_pattern(fragment), text):
            start, end = match.span()
            if any(occupied[start:end]):
                continue
            occupied[start:end] = [True] * (end - start)
            located.append((start, fragment))
    return tuple(fragment for _start, fragment in sorted(located))


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


def _is_protected_only_localization(item: EngineItem, candidate: str) -> bool:
    """Allow labels such as ``The UI`` -> ``UI`` after article removal."""
    if not item.mapping:
        return False
    source_visible = PLACEHOLDER_PATTERN.sub(" ", item.masked)
    source_visible = re.sub(
        r"\b(?:a|an|the)\b",
        " ",
        source_visible,
        flags=re.IGNORECASE,
    )
    source_visible = re.sub(r"\b\d+\s*[x×]\b", " ", source_visible, flags=re.IGNORECASE)
    if re.search(r"[A-Za-z]", source_visible):
        return False

    candidate_visible = candidate
    for fragment in sorted(
        set(item.mapping.values()),
        key=lambda value: (-len(value), value),
    ):
        candidate_visible = re.sub(
            _fragment_pattern(fragment),
            " ",
            candidate_visible,
        )
    candidate_visible = re.sub(
        r"\b\d+\s*[x×]\b", " ", candidate_visible, flags=re.IGNORECASE
    )
    return not re.search(r"[A-Za-z]", candidate_visible)


def _is_russian_article_punctuation_localization(
    original: str,
    candidate: str,
    target_lang: dict,
) -> bool:
    """Allow English articles to disappear before immutable game terms."""
    if target_lang.get("api") != "ru":
        return False
    if not candidate.strip():
        return False
    source_remainder = re.sub(
        r"\b(?:a|an|the)\b",
        "",
        original,
        flags=re.IGNORECASE,
    )
    if re.search(r"[A-Za-z0-9]", source_remainder):
        return False
    return not re.search(r"[A-Za-z0-9]", candidate)


def _split_fallback_text(text: str, max_chars: int = 480) -> list[str]:
    """Split failed prose into exact, reasonably sized sentence chunks."""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    cursor = 0
    minimum = max_chars // 3
    while len(text) - cursor > max_chars:
        window = text[cursor : cursor + max_chars + 1]
        boundaries = list(
            re.finditer(r"(?<=[.!?;:])\s+|,\s+", window)
        )
        usable = [match.end() for match in boundaries if match.end() >= minimum]
        if usable:
            cut = usable[-1]
        else:
            whitespace = [
                match.end()
                for match in re.finditer(r"\s+", window)
                if match.end() >= minimum
            ]
            cut = whitespace[-1] if whitespace else max_chars
        chunks.append(text[cursor : cursor + cut])
        cursor += cut
    chunks.append(text[cursor:])
    return chunks


def _render_validation_error(template, candidate: str) -> str | None:
    try:
        template.render_translation(candidate)
    except FormatValidationError as exc:
        return f"FormatKit: {exc}"
    return None


def _formatted_candidate_error(template, candidate: str) -> str | None:
    if any(
        contains_unsafe_formatting(segment)
        for segment in ANCHOR_PATTERN.split(candidate)
        if segment
    ):
        return "FormatKit: translated prose introduced formatting syntax"
    return _render_validation_error(template, candidate)


def _validate_candidate(
    item: EngineItem,
    candidate: object,
    target_lang: dict,
) -> tuple[bool, str | None, bool]:
    """Return (accepted, rejection reason, intentional-identity flag)."""
    if not isinstance(candidate, str):
        return False, "ответ не является строкой", False
    if not candidate.strip():
        if _is_russian_article_punctuation_localization(
            item.original,
            candidate,
            target_lang,
        ):
            return True, None, False
        return False, "получена пустая строка", False
    lowered = candidate.casefold()
    for marker in _PROMPT_LEAK_MARKERS:
        if marker in lowered:
            return False, f"эхо промпта: {marker}", False
    original_breaks = count_line_breaks(item.original)
    candidate_breaks = count_line_breaks(candidate)
    if candidate_breaks != original_breaks:
        return (
            False,
            "изменено количество переносов строк: "
            f"{original_breaks} -> {candidate_breaks}",
            False,
        )
    expected_fragments = Counter(item.mapping.values())
    actual_fragments = _count_protected_fragments(
        candidate,
        list(expected_fragments),
    )
    for fragment, expected_count in expected_fragments.items():
        actual_count = actual_fragments[fragment]
        if actual_count != expected_count:
            return (
                False,
                f"изменён защищённый фрагмент {fragment!r}: "
                f"ожидалось {expected_count}, получено {actual_count}",
                False,
            )
    fragments = list(expected_fragments)
    if _protected_fragment_sequence(
        candidate, fragments
    ) != _protected_fragment_sequence(item.original, fragments):
        return False, "изменён порядок защищённых фрагментов", False
    if structural_fragments(candidate) != structural_fragments(item.original):
        return False, "изменён порядок или набор кодов форматирования", False
    if PLACEHOLDER_PATTERN.findall(candidate) != PLACEHOLDER_PATTERN.findall(
        item.original
    ):
        return False, "изменены маркеры [#N#]", False
    length_issue = translation_length_issue(item.original, candidate)
    if length_issue:
        return False, length_issue, False
    if has_untranslated_leading_article(item.original, candidate, target_lang):
        return False, "оставлен английский артикль в начале строки", False
    if has_long_untranslated_english_fragment(candidate, target_lang):
        return False, "оставлен длинный английский фрагмент", False
    same_as_source = candidate.strip() == item.original.strip()
    if same_as_source:
        if target_lang["api"] == "en":
            return True, None, False
        if _is_protected_only_localization(item, candidate):
            return True, None, True
        if _can_cache_identity(item.original):
            return True, None, True
        return False, "ответ совпадает с оригиналом", False
    if is_article_removed_technical_translation(
        item.original,
        candidate,
        target_lang.get("api", ""),
    ):
        return True, None, False
    if (
        requires_target_script_marker(target_lang)
        and not re.search(target_lang["regex"], candidate)
    ):
        if _is_russian_article_punctuation_localization(
            item.original,
            candidate,
            target_lang,
        ):
            return True, None, False
        if _is_protected_only_localization(item, candidate):
            return True, None, False
        return False, "нет символов целевого языка", False
    if uses_same_latin_script(target_lang) and _CJK_PATTERN.search(candidate):
        return False, "CJK-символы в латинском переводе", False
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
        fallback_caches: list[tuple[str, TranslationCache]] | None = None,
        force_google_fallback: bool = False,
    ) -> None:
        self.engine_name = engine_name
        self.cache = cache
        self.config = config
        self.google_mode = google_mode
        self.ai_mode = ai_mode
        self.ai_batch = ai_batch
        self.ai_provider = ai_provider
        self.fallback_caches = list(fallback_caches or ())
        self.force_google_fallback = force_google_fallback
        self._ai_http_session = requests.Session() if engine_name == "ai" else None

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
        if self.ai_provider == "lmstudio":
            return LmStudioEngine(
                base_url=self.config.get("LMSTUDIO", "base_url"),
                api_key=self.config.get("LMSTUDIO", "api_key"),
                model=self.config.get("LMSTUDIO", "model"),
                mode=self.ai_mode,
                context=context,
                prompt_type=prompt_type,
                retries=retries,
                session=self._ai_http_session,
            )
        return KoboldEngine(
            mode=self.ai_mode,
            context=context,
            prompt_type=prompt_type,
            retries=retries,
            session=self._ai_http_session,
        )

    def discard_cached_translation(
        self,
        api_code: str,
        source_text: str,
        scope: str = "",
    ) -> None:
        self.cache.discard(
            api_code,
            _scoped_cache_source(source_text, scope),
        )

    def translate_dict(
        self,
        strings: dict[str, str],
        target_lang: dict,
        callbacks: EngineCallbacks,
        *,
        context: str = "",
        prompt_type: str = "mods",
        cache_contexts: dict[str, str] | None = None,
        candidate_validators: dict[
            str,
            Callable[[str], str | None],
        ] | None = None,
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
        cache_sources: dict[str, str] = {}
        cached_count = 0
        imported_count = 0
        deduplicated_count = 0
        repaired_cache_count = 0
        repaired_caches: set[TranslationCache] = set()
        fallback_cache_hits: dict[str, int] = {}

        def cache_layers():
            yield "", self.cache
            yield from self.fallback_caches

        def validate(
            owner_key: str,
            item: EngineItem,
            candidate: object,
        ) -> tuple[bool, str | None, bool]:
            ok, reason, identity = _validate_candidate(
                item,
                candidate,
                target_lang,
            )
            if not ok or not isinstance(candidate, str):
                return ok, reason, identity
            validator = (candidate_validators or {}).get(owner_key)
            if validator is None:
                return ok, reason, identity
            format_reason = validator(candidate)
            if format_reason:
                return False, format_reason, False
            return True, None, identity

        def bump(n: int = 1) -> None:
            if callbacks.on_progress:
                callbacks.on_progress(n)

        def metric(name: str, n: int = 1) -> None:
            if callbacks.on_metric:
                callbacks.on_metric(name, n)

        def commit(owner_key: str, text: object, source_label: str) -> bool:
            item = pending[owner_key]
            ok, reason, identity = validate(owner_key, item, text)
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
            cache_source = cache_sources[owner_key]
            if identity:
                self.cache.set_identity(target_lang["api"], cache_source)
                metric("protected", len(output_keys))
            else:
                self.cache.set(target_lang["api"], cache_source, text)
                metric("ok", len(output_keys))
                if "Google" in source_label:
                    metric("fallback", len(output_keys))
            dup = f" ×{len(output_keys)}" if len(output_keys) > 1 else ""
            callbacks.on_log(f" > {item.original} -> {text}{dup}", "dim")
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
            if is_technical_term(text):
                technical_cache_source = _scoped_cache_source(
                    text,
                    (cache_contexts or {}).get(key, ""),
                )
                hit = None
                is_imported = False
                cache_label = ""
                hit_cache = self.cache
                for candidate_label, candidate_cache in cache_layers():
                    hit, is_imported = candidate_cache.get(
                        target_lang["api"],
                        technical_cache_source,
                    )
                    if hit is not None:
                        cache_label = candidate_label
                        hit_cache = candidate_cache
                        break
                result[key] = text
                if hit is not None:
                    if is_imported:
                        imported_count += 1
                    elif hit_cache is not self.cache:
                        fallback_cache_hits[cache_label] = (
                            fallback_cache_hits.get(cache_label, 0) + 1
                        )
                    else:
                        cached_count += 1
                    if hit_cache is not self.cache:
                        self.cache.set_identity(
                            target_lang["api"],
                            technical_cache_source,
                        )
                    metric("ok")
                    metric("cached")
                else:
                    self.cache.set_identity(
                        target_lang["api"],
                        technical_cache_source,
                    )
                    metric("protected")
                bump()
                continue
            masked, mapping = mask_protected_fragments(text)
            item = EngineItem(key=key, original=text, masked=masked, mapping=mapping)
            cache_source = _scoped_cache_source(
                text,
                (cache_contexts or {}).get(key, ""),
            )

            cache_accepted = False
            for cache_label, candidate_cache in cache_layers():
                hit, is_imported = candidate_cache.get(
                    target_lang["api"],
                    cache_source,
                )
                if hit is None:
                    continue
                valid, reason, identity = validate(key, item, hit)
                if valid:
                    result[key] = hit
                    if is_imported:
                        imported_count += 1
                    elif candidate_cache is not self.cache:
                        fallback_cache_hits[cache_label] = (
                            fallback_cache_hits.get(cache_label, 0) + 1
                        )
                    else:
                        cached_count += 1
                    if candidate_cache is not self.cache:
                        if identity:
                            self.cache.set_identity(
                                target_lang["api"], cache_source
                            )
                        else:
                            self.cache.set(
                                target_lang["api"], cache_source, hit
                            )
                    bump()
                    metric("ok")
                    metric("cached")
                    cache_accepted = True
                    break
                callbacks.on_log(
                    f"⚠️ Запись {cache_label or 'кэша'} отброшена "
                    f"для {text[:70]!r}: {reason}",
                    "yellow",
                )
                candidate_cache.discard(
                    target_lang["api"],
                    cache_source,
                    include_imported=is_imported,
                )
                repaired_caches.add(candidate_cache)
                repaired_cache_count += 1
            if cache_accepted:
                continue

            if not masked:
                result[key] = text
                bump()
                metric("protected")
                continue

            fp = _source_fingerprint(cache_source)
            owner = source_owner.get(fp)
            if owner is not None:
                aliases[owner].append(key)
                deduplicated_count += 1
                continue
            source_owner[fp] = key
            aliases[key] = [key]
            pending[key] = item
            cache_sources[key] = cache_source

        if cached_count:
            callbacks.on_log(f"   🗃️ Из кэша: {cached_count}", "gray")
        if imported_count:
            callbacks.on_log(f"   📦 Из ресурс-паков: {imported_count}", "cyan")
        for cache_label, count in fallback_cache_hits.items():
            callbacks.on_log(f"   🗃️ Из {cache_label}: {count}", "gray")
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
        cur_format_sensitive = False
        for key, item in pending.items():
            tlen = len(item.masked)
            ph = len(PLACEHOLDER_PATTERN.findall(item.masked))
            format_sensitive = bool(
                key in (candidate_validators or {})
                and ANCHOR_PATTERN.search(item.original)
            )
            next_limit = (
                5
                if cur_format_sensitive or format_sensitive
                else (self.ai_batch if is_ai else 50)
            )
            if cur and len(cur) >= next_limit:
                batches.append(cur)
                cur, cur_chars, cur_ph = {}, 0, 0
                cur_format_sensitive = False
            if is_ai and (ph > 15 or tlen > 800):
                if cur:
                    batches.append(cur)
                    cur, cur_chars, cur_ph = {}, 0, 0
                    cur_format_sensitive = False
                batches.append({key: item})
                continue
            if is_ai and cur and (
                cur_chars + tlen > max_chars or cur_ph + ph > max_ph_per_batch
            ):
                batches.append(cur)
                cur, cur_chars, cur_ph = {}, 0, 0
                cur_format_sensitive = False
            cur[key] = item
            cur_chars += tlen
            cur_ph += ph
            cur_format_sensitive = cur_format_sensitive or format_sensitive
            active_limit = (
                5
                if cur_format_sensitive
                else (self.ai_batch if is_ai else 50)
            )
            if len(cur) >= active_limit:
                batches.append(cur)
                cur, cur_chars, cur_ph = {}, 0, 0
                cur_format_sensitive = False
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

        identity_failed = {
            key: item
            for key, item in pending.items()
            if key not in accepted
            and "ответ совпадает с оригиналом" in failure_reasons.get(key, "")
        }
        if identity_failed and callbacks.should_run():
            callbacks.on_log(
                f"🔁 Строгий повтор названий: {len(identity_failed)}",
                "cyan",
            )
            retry_context = (
                f"{context}\n"
                "These are translatable Minecraft names or titles. Translate "
                "every English name into the target language; do not return "
                "the original English text."
            ).strip()
            retry_engine = (
                GoogleEngine(
                    workers=self.config.getint("GENERAL", "google_workers", 5),
                    mode="single",
                )
                if self.engine_name == "google"
                else self._build_engine(retry_context, prompt_type)
            )
            retry_result = retry_engine.translate_batch(
                identity_failed,
                target_lang,
                callbacks,
            )
            apply_engine_result(
                identity_failed,
                retry_result,
                "строгий повтор",
            )

        format_failed = {
            key: item
            for key, item in pending.items()
            if key not in accepted
            and "FormatKit:" in failure_reasons.get(key, "")
        }
        if format_failed and callbacks.should_run():
            callbacks.on_log(
                f"🔁 Строгий повтор структуры: {len(format_failed)}",
                "cyan",
            )
            retry_context = (
                f"{context}\n"
                "Every [#N#] marker is a fixed boundary. Keep every marker "
                "in its original position relative to the translated words; "
                "never move visible text across a marker."
            ).strip()
            retry_engine = (
                GoogleEngine(
                    workers=self.config.getint("GENERAL", "google_workers", 5),
                    mode="single",
                )
                if self.engine_name == "google"
                else self._build_engine(retry_context, prompt_type)
            )
            retry_result = retry_engine.translate_batch(
                format_failed,
                target_lang,
                callbacks,
            )
            apply_engine_result(
                format_failed,
                retry_result,
                "строгий повтор структуры",
            )

        failed_pending = {k: v for k, v in pending.items() if k not in accepted}
        try:
            use_fallback = self.force_google_fallback or self.config.getboolean(
                "AI", "fallback_google"
            )
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

        segmented_failed = {
            key: item
            for key, item in pending.items()
            if key not in accepted
            and key in (candidate_validators or {})
            and (
                ANCHOR_PATTERN.search(item.original)
                or len(item.original) > 240
            )
        }
        if segmented_failed and callbacks.should_run():
            segment_items: dict[str, EngineItem] = {}
            segment_layouts: dict[str, list[str]] = {}
            required_segments: dict[str, set[str]] = {}
            for owner_key, owner_item in segmented_failed.items():
                anchor_parts = re.split(
                    f"({ANCHOR_PATTERN.pattern})",
                    owner_item.original,
                )
                parts: list[str] = []
                for part in anchor_parts:
                    if ANCHOR_PATTERN.fullmatch(part):
                        parts.append(part)
                    else:
                        parts.extend(_split_fallback_text(part))
                segment_layouts[owner_key] = parts
                required_segments[owner_key] = set()
                for index, part in enumerate(parts):
                    if not part or ANCHOR_PATTERN.fullmatch(part):
                        continue
                    leading = part[: len(part) - len(part.lstrip())]
                    trailing = part[len(part.rstrip()) :]
                    end = len(part) - len(trailing) if trailing else len(part)
                    core = part[len(leading) : end]
                    if not re.search(r"[A-Za-z]", core) or is_technical_term(core):
                        continue
                    masked, mapping = mask_protected_fragments(core)
                    segment_key = f"{owner_key}::segment::{index}"
                    segment_items[segment_key] = EngineItem(
                        key=segment_key,
                        original=core,
                        masked=masked,
                        mapping=mapping,
                    )
                    required_segments[owner_key].add(segment_key)

            if segment_items:
                callbacks.on_log(
                    "🧩 Детерминированный fallback: "
                    f"{len(segmented_failed)} блоков / "
                    f"{len(segment_items)} текстовых сегментов",
                    "cyan",
                )
                segment_engine = (
                    GoogleEngine(
                        workers=self.config.getint(
                            "GENERAL",
                            "google_workers",
                            5,
                        ),
                        mode="single",
                    )
                    if self.engine_name == "google" or (is_ai and use_fallback)
                    else self._build_engine(
                        f"{context}\nTranslate each visible text segment.",
                        prompt_type,
                    )
                )
                segment_result = segment_engine.translate_batch(
                    segment_items,
                    target_lang,
                    callbacks,
                )
                valid_segments: dict[str, str] = {}
                for segment_key, candidate in segment_result.items():
                    segment_item = segment_items.get(segment_key)
                    if segment_item is None:
                        continue
                    ok, _reason, _identity = _validate_candidate(
                        segment_item,
                        candidate,
                        target_lang,
                    )
                    if ok:
                        valid_segments[segment_key] = candidate

                for owner_key, parts in segment_layouts.items():
                    if not required_segments[owner_key].issubset(valid_segments):
                        continue
                    for segment_key in required_segments[owner_key]:
                        index = int(segment_key.rsplit("::", 1)[1])
                        original_part = parts[index]
                        leading = original_part[
                            : len(original_part) - len(original_part.lstrip())
                        ]
                        trailing = original_part[len(original_part.rstrip()) :]
                        parts[index] = (
                            leading + valid_segments[segment_key] + trailing
                        )
                    commit(
                        owner_key,
                        "".join(parts),
                        "сегментный fallback",
                    )

        for owner_key, item in pending.items():
            if owner_key in accepted:
                continue
            output_keys = aliases[owner_key]
            
            for k in output_keys:
                result[k] = item.original
                
            if not callbacks.should_run():
                continue
                
            reason = failure_reasons.get(owner_key, "нет результата")
            callbacks.on_log(
                f"⚠️ Строка не переведена: {item.original[:90]!r}; {reason}",
                "yellow",
            )
            bump(len(output_keys))
            metric("failed", len(output_keys))

        if repaired_cache_count:
            for repaired_cache in repaired_caches:
                repaired_cache.save()
            if self.cache not in repaired_caches:
                self.cache.save_if_threshold()
            callbacks.on_log(
                "🧹 Кэш автоматически исправлен: "
                f"{repaired_cache_count} некорректных записей",
                "yellow",
            )
        else:
            self.cache.save_if_threshold()
        return result

    def translate_formatted_dict(
        self,
        strings: dict[str, str],
        target_lang: dict,
        callbacks: EngineCallbacks,
        *,
        context: str = "",
        prompt_type: str = "books",
    ) -> dict[str, str]:
        """Translate prose nodes while reconstructing all syntax from source."""
        if not strings:
            return {}

        templates = {
            key: parse_rich_text(value)
            for key, value in strings.items()
        }
        flat: dict[str, str] = {}
        cache_contexts: dict[str, str] = {}

        for key, template in templates.items():
            payload, _anchors = template.translation_payload()
            visible = ANCHOR_PATTERN.sub(" ", payload)
            if not re.search(r"[A-Za-z]", visible):
                continue
            if is_technical_term(visible):
                continue
            flat[key] = payload
            cache_contexts[key] = f"{context}|{key}|text"

        inner_callbacks = EngineCallbacks(
            should_run=callbacks.should_run,
            wait_if_paused=callbacks.wait_if_paused,
            on_log=callbacks.on_log,
            on_status=callbacks.on_status,
            on_progress=None,
            on_metric=None,
        )
        translated = self.translate_dict(
            flat,
            target_lang,
            inner_callbacks,
            context=context,
            prompt_type=prompt_type,
            cache_contexts=cache_contexts,
            candidate_validators={
                key: lambda candidate, template=templates[key]: (
                    _formatted_candidate_error(template, candidate)
                )
                for key in flat
            },
        )

        failed_roots: set[str] = set()
        result: dict[str, str] = {}
        for key, source in strings.items():
            payload = flat.get(key)
            if payload is None:
                result[key] = source
                continue
            candidate = translated.get(key, payload)
            visible_segments = ANCHOR_PATTERN.split(candidate)
            if any(
                contains_unsafe_formatting(segment)
                for segment in visible_segments
                if segment
            ):
                callbacks.on_log(
                    f"⚠️ Форматирование в текстовом узле {key!r} "
                    "отклонено; исходная разметка сохранена",
                    "yellow",
                )
                self.cache.discard(
                    target_lang["api"],
                    _scoped_cache_source(payload, cache_contexts[key]),
                )
                candidate = payload
            try:
                result[key] = templates[key].render_translation(candidate)
            except FormatValidationError:
                self.cache.discard(
                    target_lang["api"],
                    _scoped_cache_source(payload, cache_contexts[key]),
                )
                result[key] = source
            if result[key] == source:
                failed_roots.add(key)
            if not callbacks.should_run():
                continue
            if callbacks.on_progress:
                callbacks.on_progress(1)
            if callbacks.on_metric:
                if key not in flat:
                    callbacks.on_metric("protected", 1)
                elif key in failed_roots:
                    callbacks.on_metric("failed", 1)
                else:
                    callbacks.on_metric("ok", 1)
        return result
