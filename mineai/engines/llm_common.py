import json
import os
import re
from collections import Counter
from typing import Callable

import requests

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.io_utils import atomic_write_text
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    polish_translation,
    unmask_translation,
)


RETRY_BATCH_SIZES = (10, 5, 1)

PROMPTS_FILE = "prompts.json"

def get_default_prompts() -> dict[str, str]:
    return {
        "mods": "Translate the following JSON string values from English to {lang_name}.",
        "books": "Ты локализатор Minecraft. Переведи текст книги/справочника на {lang_name}. Сохраняй игровой лор и литературный стиль.",
        "quests": "Ты локализатор Minecraft. Переведи строки мода/квеста «{context}» на {lang_name}. Сохраняй игровой стиль и лор.",
        "technical": "STRICT RULES:\n1. Do not translate or change JSON keys.\n2. Preserve every [#N#] placeholder exactly (e.g. [#0#], [#1#]). Do not add, remove, duplicate, or rename them.\n3. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside the JSON strings.\n4. Output ONLY raw valid JSON. No markdown formatting, no ```json tags, no explanations, no introductory text."
    }

def load_prompts() -> dict[str, str]:
    if not os.path.exists(PROMPTS_FILE):
        save_prompts(get_default_prompts())
        return get_default_prompts()
    try:
        with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return get_default_prompts()

def save_prompts(prompts_dict: dict[str, str]) -> None:
    payload = json.dumps(prompts_dict, ensure_ascii=False, indent=4)
    atomic_write_text(PROMPTS_FILE, payload)


def dump_ai_error(prompt: str, response: str, error_msg: str) -> None:
    try:
        with open("ai_error_log.txt", "a", encoding="utf-8") as f:
            f.write(f"=== ОШИБКА ИИ ===\n")
            f.write(f"ПРИЧИНА: {error_msg}\n")
            f.write(f"--- ЗАПРОС ---\n{prompt}\n")
            f.write(f"--- ОТВЕТ ---\n{response}\n")
            f.write("===================\n\n")
    except Exception:
        pass
def build_translation_prompt(
    payload: dict[str, str],
    lang_name: str,
    *,
    mode: str,
    context: str,
    prompt_type: str = "mods",
) -> str:
    blob = json.dumps(payload, ensure_ascii=False)
    
    prompts = load_prompts()
    intro_template = prompts.get(prompt_type, get_default_prompts()["mods"])
    intro = intro_template.replace("{lang_name}", lang_name).replace("{context}", context)
    
    # Подтягиваем технические правила из файла
    tech_rules = prompts.get("technical", get_default_prompts()["technical"])

    return (
        f"{intro}\n\n"
        f"{tech_rules}\n\n"
        f"DATA:\n{blob}"
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
        prompt_type: str = "mods",
        call_api: Callable[[str, int], str | None],
        label: str = "ИИ",
        retries: int = 3,  # <--- НОВАЯ СТРОКА
    ) -> None:
        self.mode = mode
        self.context = context
        self.prompt_type = prompt_type
        self._call_api = call_api
        self.label = label
        self.retries = retries  # <--- НОВАЯ СТРОКА
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
            
            # --- НОВАЯ СИСТЕМА КАСКАДНЫХ ПОВТОРОВ (10 -> 5 -> 1) ---
            active_retries = RETRY_BATCH_SIZES[:self.retries]  # <--- НОВАЯ СТРОКА (Обрезаем список попыток)
            
            for retry_number, retry_batch_size in enumerate(
                active_retries,  # <--- ЗАМЕНИЛИ RETRY_BATCH_SIZES на active_retries
                start=1,
            ):
                if not failed or not callbacks.should_run():
                    break

                callbacks.on_log(
                    f"🔁 {self.label}: повтор {retry_number}/"
                    f"{len(RETRY_BATCH_SIZES)} — {len(failed)} строк",
                    "yellow",
                )
                retry_failed: list[str] = []
                for j in range(0, len(failed), retry_batch_size):
                    if not callbacks.should_run():
                        break
                    callbacks.wait_if_paused()
                    if not callbacks.should_run():
                        break

                    sub = failed[j : j + retry_batch_size]
                    retry_failed.extend(
                        self._translate_chunk(
                            sub,
                            items,
                            target_lang,
                            result,
                            callbacks,
                        )
                    )
                failed = retry_failed

            if failed and callbacks.should_run():
                callbacks.on_log(
                    f"⚠️ {self.label}: не удалось перевести после повторов — "
                    f"{len(failed)} строк; сохранён исходный текст",
                    "yellow",
                )
            # --- КОНЕЦ НОВОЙ СИСТЕМЫ ПОВТОРОВ ---
                
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
            prompt_type=self.prompt_type,
        )
        callbacks.on_status(f"⏳ {self.label}: пакет {len(chunk_keys)}...")
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
                    # ЛОГИРОВАНИЕ УТЕРЯННОГО КЛЮЧА
                    dump_ai_error(items[key].masked, str(raw), "Не прошли проверку (ключ утерян или значение не является текстом)")
                    continue
                
                # ЗАЩИТА ОТ ГАЛЛЮЦИНАЦИЙ: проверяем длину
                orig_len = len(items[key].masked)
                if len(raw) > (orig_len * 2.5) + 50:
                    failed.append(key)
                    dump_ai_error(items[key].masked, raw, f"Слишком длинный текст ({len(raw)} симв. при оригинале {orig_len} симв.)")
                    continue

                if not placeholders_match(raw, items[key].masked):
                    failed.append(key)
                    # ЛОГИРОВАНИЕ СЛОМАННЫХ МАРКЕРОВ
                    dump_ai_error(items[key].masked, raw, "Не прошли проверку (потеряны, добавлены или искажены маркеры [#N#])")
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
            # ЛОГИРОВАНИЕ СЛОМАННОГО JSON
            dump_ai_error(prompt, content if 'content' in locals() else "Нет ответа", str(exc))
            callbacks.on_log(f"❌ {self.label}: неверный JSON (сохранен в ai_error_log.txt)", "red")
            return chunk_keys
        except requests.RequestException as exc:
            callbacks.on_log(f"❌ {self.label}: сеть — {exc}", "red")
            return chunk_keys
