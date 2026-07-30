import json
import re
from collections import Counter
from typing import Callable

import requests

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    polish_translation,
    unmask_translation,
)


RETRY_BATCH_SIZE = 10


def build_translation_prompt(
    payload: dict[str, str],
    lang_name: str,
    *,
    mode: str,
    context: str,
) -> str:
    blob = json.dumps(payload, ensure_ascii=False)
    if mode == "context" and context:
        return (
            f"Ты локализатор Minecraft. Переведи строки мода/квеста "
            f"«{context}» на {lang_name}. Сохраняй игровой стиль и лор. "
            f"Не переводи JSON-ключи. Все маркеры вида [#N#], например "
            f"[#0#] и [#1#], сохраняй без изменений: не удаляй, не "
            f"добавляй, не дублируй и не переименовывай их. Верни ТОЛЬКО "
            f"валидный JSON с теми же ключами. Данные: {blob}"
        )
    return (
        f"Translate JSON string values from English to {lang_name}. "
        f"Do not translate keys. Preserve every [#N#] placeholder exactly, "
        f"for example [#0#] and [#1#]. Do not add, remove, duplicate, or "
        f"rename placeholders. Return ONLY valid JSON with the same keys. "
        f"Data: {blob}"
    )


def parse_llm_json_response(content: str) -> dict[str, object]:
    text = re.sub(
        r"^```json\s*|^```\s*|```$",
        "",
        content.strip(),
        flags=re.IGNORECASE,
    ).strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("LLM response is not a JSON object")
    return data


def placeholders_match(text: str, expected_text: str) -> bool:
    """Return whether all placeholders are preserved with equal multiplicity."""
    expected_ids = Counter(PLACEHOLDER_PATTERN.findall(expected_text))
    actual_ids = Counter(PLACEHOLDER_PATTERN.findall(text))
    return actual_ids == expected_ids


class BatchLlmEngine(TranslationEngine):
    """Batched JSON translation via any chat-completions API."""

    def __init__(
        self,
        *,
        mode: str = "safe",
        context: str = "",
        call_api: Callable[[str, int], str | None],
        label: str = "ИИ",
    ) -> None:
        self.mode = mode
        self.context = context
        self._call_api = call_api
        self.label = label
        self.batch_size = 40 if mode == "context" else 20
        self.max_tokens = 4096 if mode == "context" else 2048

    def translate_batch(
        self,
        items: dict[str, EngineItem],
        target_lang: dict,
        callbacks: EngineCallbacks,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        keys = list(items.keys())
        i = 0
        while i < len(keys) and callbacks.should_run():
            callbacks.wait_if_paused()
            if not callbacks.should_run():
                break

            chunk = keys[i : i + self.batch_size]
            failed = self._translate_chunk(
                chunk,
                items,
                target_lang,
                result,
                callbacks,
            )
            if failed and callbacks.should_run():
                callbacks.on_log(
                    f"❌ Ошибка {self.label}. Повторяем проблемные строки...",
                    "yellow",
                )
                for j in range(0, len(failed), RETRY_BATCH_SIZE):
                    if not callbacks.should_run():
                        break
                    callbacks.wait_if_paused()
                    if not callbacks.should_run():
                        break

                    sub = failed[j : j + RETRY_BATCH_SIZE]
                    self._translate_chunk(
                        sub,
                        items,
                        target_lang,
                        result,
                        callbacks,
                    )
            i += self.batch_size
        return result

    def _translate_chunk(
        self,
        chunk_keys: list[str],
        items: dict[str, EngineItem],
        target_lang: dict,
        result: dict[str, str],
        callbacks: EngineCallbacks,
    ) -> list[str]:
        payload = {key: items[key].masked for key in chunk_keys}
        prompt = build_translation_prompt(
            payload,
            target_lang["name"],
            mode=self.mode,
            context=self.context,
        )
        callbacks.on_status(f"⏳ {self.label}: пакет {len(chunk_keys)} строк...")
        try:
            content = self._call_api(prompt, self.max_tokens)
            if not content:
                return chunk_keys

            translated = parse_llm_json_response(content)
            unexpected = set(translated) - set(chunk_keys)
            if unexpected:
                callbacks.on_log(
                    f"⚠️ {self.label}: отброшены лишние JSON-ключи — "
                    f"{len(unexpected)}",
                    "yellow",
                )

            failed: list[str] = []
            for key in chunk_keys:
                raw = translated.get(key)
                if not isinstance(raw, str):
                    failed.append(key)
                    continue
                if not placeholders_match(raw, items[key].masked):
                    failed.append(key)
                    continue

                text = unmask_translation(raw, items[key].mapping)
                result[key] = polish_translation(text)

            if failed:
                callbacks.on_log(
                    f"❌ {self.label}: не прошли проверку — "
                    f"{len(failed)} строк",
                    "red",
                )
            return failed
        except (json.JSONDecodeError, TypeError, KeyError) as exc:
            callbacks.on_log(f"❌ {self.label}: неверный JSON — {exc}", "red")
            return chunk_keys
        except requests.RequestException as exc:
            callbacks.on_log(f"❌ {self.label}: сеть — {exc}", "red")
            return chunk_keys
