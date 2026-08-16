"""Pilot-v2 LLM safety layer driven by the first FTB Evolution runtime log.

The legacy batch engine remains intact. Importing this module tightens its
marker helpers, while the subclass below only changes batching for marker-heavy
strings. This keeps the pilot easy to remove or compare against v1.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Callable

import requests

from mineai.engines import llm_common as _base
from mineai.text_processing import PLACEHOLDER_PATTERN


SINGLE_ITEM_MARKER_THRESHOLD = 8
_BARE_MARKER_PATTERN = re.compile(
    r"#\s*(\d+)\s*#|\[\s*#\s*(\d+)\s*\]|\[\s*(\d+)\s*#\s*\]"
)

_ORIGINAL_BUILD_TRANSLATION_PROMPT = _base.build_translation_prompt
_ORIGINAL_GET_DEFAULT_PROMPTS = _base.get_default_prompts


def _placeholder_sequence(text: str) -> tuple[str, ...]:
    return tuple(PLACEHOLDER_PATTERN.findall(text))


def _bare_marker_sequence(text: str) -> tuple[str, ...]:
    stripped = PLACEHOLDER_PATTERN.sub("", text)
    result: list[str] = []
    for match in _BARE_MARKER_PATTERN.finditer(stripped):
        result.append(next(group for group in match.groups() if group is not None))
    return tuple(result)


def marker_validation_error(text: str, expected_text: str) -> str | None:
    """Reject lost, added, reordered, or hallucinated numbered markers."""
    expected = _placeholder_sequence(expected_text)
    actual = _placeholder_sequence(text)
    if Counter(actual) != Counter(expected):
        return "Потеряны/добавлены маркеры [#N#]"
    if actual != expected:
        return "Изменён порядок маркеров [#N#]"
    if _bare_marker_sequence(text) != _bare_marker_sequence(expected_text):
        return "Добавлены/изменены ложные маркеры #N#"
    return None


def placeholders_match(text: str, expected_text: str) -> bool:
    return marker_validation_error(text, expected_text) is None


def build_marker_manifest(payload: dict[str, str]) -> str:
    """Show the exact source marker sequence instead of sorting marker ids."""
    lines: list[str] = []
    for key, value in payload.items():
        sequence = PLACEHOLDER_PATTERN.findall(value)
        if not sequence:
            lines.append(f'"{key}": (plain text, no placeholders)')
            continue
        listing = " ".join(f"[#{marker_id}#]" for marker_id in sequence)
        lines.append(f'"{key}": {listing}')
    return (
        "MARKER WHITELIST / SOURCE ORDER (copy exactly per key):\n"
        + "\n".join(lines)
        + "\nRULE: the translation of each key must contain exactly this marker "
        "sequence — no skips, no renumbering, no repeats, no reordering, "
        "and no extra markers."
    )


def get_default_prompts() -> dict[str, str]:
    prompts = dict(_ORIGINAL_GET_DEFAULT_PROMPTS())
    prompts["technical"] = (
        "STRICT RULES:\n"
        "1. Copy every JSON key exactly; translate only human-readable string values.\n"
        "2. Preserve ALL [#N#] placeholders exactly and keep the exact SOURCE ORDER. "
        "Never add, remove, rename, duplicate, or move markers.\n"
        "3. Never invent placeholder-like tokens or formatting markers absent from the source.\n"
        "4. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside JSON strings.\n"
        "5. Output ONLY one raw valid JSON object. No markdown, code fences, comments, "
        "explanations, or intro text."
    )
    return prompts


def _renumber_rules(text: str) -> str:
    out: list[str] = []
    number = 1
    for line in text.splitlines():
        if re.match(r"^\s*\d+\.\s+", line):
            line = re.sub(r"^\s*\d+\.\s+", f"{number}. ", line, count=1)
            number += 1
        out.append(line)
    return "\n".join(out)


def build_translation_prompt(
    payload: dict[str, str],
    lang_name: str,
    *,
    mode: str,
    context: str,
    prompt_type: str = "mods",
) -> str:
    prompt = _ORIGINAL_BUILD_TRANSLATION_PROMPT(
        payload,
        lang_name,
        mode=mode,
        context=context,
        prompt_type=prompt_type,
    )
    separator = "\n\nDATA:\n"
    if separator not in prompt:
        return prompt
    head, data = prompt.split(separator, 1)
    head = _renumber_rules(head)
    has_markers = any(PLACEHOLDER_PATTERN.search(value) for value in payload.values())
    if has_markers:
        safety = (
            "STRUCTURAL SAFETY (runtime-enforced): preserve the exact [#N#] "
            "sequence in SOURCE ORDER for every key. Never move, add, or duplicate "
            "markers, and never invent marker-like tokens."
        )
    else:
        safety = (
            "STRUCTURAL SAFETY: Copy JSON keys exactly. Do not invent placeholders "
            "or formatting markers. Return raw JSON only."
        )
    return f"{head}\n\n{safety}{separator}{data}"


def repair_markers(
    call_api: Callable[[str, int], str | None],
    masked_source: str,
    broken_translation: str,
    max_tokens: int,
) -> str | None:
    prompt = (
        "The translation below lost, moved, or corrupted numbered markers.\n"
        "Restore EXACTLY the source marker sequence: same ids, same counts, same SOURCE ORDER.\n"
        "Remove marker-like tokens absent from the source. Do not retranslate.\n"
        "Output ONLY the corrected text, no explanations.\n\n"
        f"SOURCE:\n{masked_source}\n\n"
        f"BROKEN TRANSLATION:\n{broken_translation}\n"
    )
    content = call_api(prompt, max_tokens)
    if not content:
        return None
    raw = re.sub(
        r"^```[a-z]*\s*|\s*```$",
        "",
        content.strip(),
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()
    if raw.startswith("{") and raw.endswith("}"):
        try:
            json.loads(raw)
            return None
        except (json.JSONDecodeError, ValueError):
            pass
    return raw if raw and marker_validation_error(raw, masked_source) is None else None


# The inherited _translate_chunk() resolves these helpers through llm_common's
# module globals at runtime, so patching them here upgrades validation without
# copying the large legacy implementation.
_base.get_default_prompts = get_default_prompts
_base.build_marker_manifest = build_marker_manifest
_base.build_translation_prompt = build_translation_prompt
_base.placeholders_match = placeholders_match
_base.repair_markers = repair_markers


class BatchLlmEngine(_base.BatchLlmEngine):
    """V2 batch policy: isolate marker-heavy strings before the legacy retry path."""

    def translate_batch(self, items, target_lang, callbacks):
        result: dict[str, str] = {}
        keys = list(items.keys())
        index = 0
        while index < len(keys) and callbacks.should_run():
            callbacks.wait_if_paused()
            if not callbacks.should_run():
                break

            first_markers = len(PLACEHOLDER_PATTERN.findall(items[keys[index]].masked))
            if first_markers >= SINGLE_ITEM_MARKER_THRESHOLD:
                chunk = [keys[index]]
            else:
                end = index
                while end < len(keys) and end - index < self.batch_size:
                    marker_count = len(PLACEHOLDER_PATTERN.findall(items[keys[end]].masked))
                    if marker_count >= SINGLE_ITEM_MARKER_THRESHOLD:
                        break
                    end += 1
                chunk = keys[index:end]

            failed = self._translate_chunk(chunk, items, target_lang, result, callbacks)

            hopeless = [
                key for key in failed
                if len(PLACEHOLDER_PATTERN.findall(items[key].masked)) > 20
            ]
            if hopeless:
                callbacks.on_log(
                    f"⚠️ {self.label}: {len(hopeless)} строк с >20 маркерами — повторы отключены",
                    "yellow",
                )
                failed = [key for key in failed if key not in hopeless]

            active_retries = _base.RETRY_BATCH_SIZES[: self.retries]
            for retry_number, retry_batch_size in enumerate(active_retries, start=1):
                if not failed or not callbacks.should_run():
                    break
                callbacks.on_log(
                    f"🔁 {self.label}: повтор {retry_number}/{len(_base.RETRY_BATCH_SIZES)} — "
                    f"{len(failed)} строк",
                    "orange",
                )
                retry_failed: list[str] = []
                for retry_index in range(0, len(failed), retry_batch_size):
                    if not callbacks.should_run():
                        break
                    callbacks.wait_if_paused()
                    if not callbacks.should_run():
                        break
                    subset = failed[retry_index : retry_index + retry_batch_size]
                    retry_failed.extend(
                        self._translate_chunk(subset, items, target_lang, result, callbacks)
                    )
                failed = retry_failed

            if failed and callbacks.should_run():
                callbacks.on_log(
                    f"⚠️ {self.label}: не удалось перевести после повторов — "
                    f"{len(failed)} строк; сохранён исходный текст",
                    "yellow",
                )

            index += len(chunk)
        return result


__all__ = [
    "BatchLlmEngine",
    "SINGLE_ITEM_MARKER_THRESHOLD",
    "build_marker_manifest",
    "build_translation_prompt",
    "marker_validation_error",
    "placeholders_match",
]
