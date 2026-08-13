import json
import os
import re
from collections import Counter
from typing import Callable

import requests

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.io_utils import atomic_write_text
from mineai.language_validation import has_untranslated_leading_article
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    collapse_added_line_breaks,
    count_line_breaks,
    is_technical_term,
    polish_translation,
    suspicious_duplicate_keys,
    translation_length_issue,
    unmask_translation,
)


RETRY_BATCH_SIZES = (10, 5, 1)

PROMPTS_FILE = "prompts.json"

def get_default_prompts() -> dict[str, str]:
    return {
        "mods": "Translate the following JSON string values from English to {lang_name}.",
        "books": "Ты локализатор Minecraft. Переведи текст книги/справочника на {lang_name}. Сохраняй игровой лор и литературный стиль.",
        "quests": "Ты локализатор Minecraft. Переведи строки мода/квеста «{context}» на {lang_name}. Сохраняй игровой стиль и лор.",
        "technical": "STRICT RULES:\n1. Do not translate or change JSON keys.\n2. Preserve ALL [#N#] placeholders exactly and in the same order. If a word is wrapped like [#0#]Word[#1#], wrap the translation like [#0#]Слово[#1#]. DO NOT drop, repeat or reorder markers.\n3. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside the JSON strings.\n4. Output ONLY raw valid JSON. No markdown formatting, no explanations, no intro text."
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
            f.write("=== НЕУДАЧНАЯ ПОПЫТКА ИИ ===\n")
            f.write("СТАТУС: промежуточная; может быть исправлена повтором\n")
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
    force_translation: bool = False,
) -> str:
    blob = json.dumps(payload, ensure_ascii=False)
    prompts = load_prompts()
    intro_template = prompts.get(prompt_type, get_default_prompts()["mods"])
    intro = intro_template.replace("{lang_name}", lang_name).replace("{context}", context)
    if prompt_type == "books" and lang_name.casefold() in {"russian", "русский"}:
        intro += (
            "\nPreferred Minecraft terminology for Russian:\n"
            "- Copy Paste Gadget = Гаджет копирования и вставки\n"
            "- Cut Paste Gadget = Гаджет вырезания и вставки\n"
            "- Potion Charm = Амулет зелья\n"
            "- Gem Case = Футляр для самоцветов"
        )
    if force_translation:
        intro += (
            "\nEvery value below was selected because it requires translation. "
            "Do not copy ordinary source text unchanged; translate names and titles "
            f"naturally into {lang_name}. Preserve only protected placeholders and "
            "genuine non-translatable terms."
        )
    tech_rules = prompts.get("technical", get_default_prompts()["technical"])
    tech_rules += (
        "\nEach JSON key is an independent source row. Never combine text from "
        "different keys and never copy one key's translation into another key."
    )

    # --- ЯВНЫЙ СПИСОК МАРКЕРОВ: что именно нельзя менять ---
    from mineai.text_processing import PLACEHOLDER_PATTERN
    has_markers = any(PLACEHOLDER_PATTERN.search(v) for v in payload.values())

    if not has_markers:
        # Убираем все упоминания о маркерах из промпта
        tech_rules = re.sub(r'(?i)\n?.*\[#N#\].*', '', tech_rules)
        tech_rules = re.sub(r'(?i)\n?.*markers.*', '', tech_rules)
        tech_rules = tech_rules.replace("{markers}", "")
        return f"{intro}\n\n{tech_rules.strip()}\n\nDATA:\n{blob}"

    manifest = build_marker_manifest(payload)
    if "{markers}" in tech_rules:
        # Если вставил {markers} в редакторе промптов — список встанет туда
        tech_rules = tech_rules.replace("{markers}", manifest)
    else:
        # Иначе блок дописывается сразу после тех. правил
        tech_rules = f"{tech_rules}\n\n{manifest}"

    return (
        f"{intro}\n\n"
        f"{tech_rules}\n\n"
        f"DATA:\n{blob}"
    )


def parse_llm_json_response(content: str) -> dict[str, object]:
    # Убираем markdown-обёртки (```json ... ```)
    text = re.sub(
        r"^```(?:json)?\s*\n?|\n?```\s*$",
        "",
        content.strip(),
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()

    # Ищем первый валидный JSON-объект, игнорируя скобки внутри строк
    start_idx = text.find('{')
    if start_idx != -1:
        stack = 0
        in_str = False
        escaped = False
        for i in range(start_idx, len(text)):
            c = text[i]
            if in_str:
                if escaped:
                    escaped = False
                elif c == '\\':
                    escaped = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == '{':
                    stack += 1
                elif c == '}':
                    stack -= 1
                    if stack == 0:
                        text = text[start_idx:i+1]
                        break

    # Чиним литеральные newlines внутри JSON-строк:
    # заменяем реальный \n между кавычками на экранированный \\n
    def _fix_newlines_in_strings(m: re.Match) -> str:
        return m.group(0).replace("\n", "\\n").replace("\r", "\\r")

    text = re.sub(r'"(?:[^"\\]|\\.)*"', _fix_newlines_in_strings, text)
    text = re.sub(r'\\(["\\/bfnrtu]|u[0-9a-fA-F]{4})|\\', lambda m: m.group(0) if m.group(1) else r"\\\\", text)  # v9: санация \-эскейпов

    # Санация невалидных \-эскейпов (\К, \П и т.п.): удваиваем "одинокий" слэш,
    # не трогая валидные эскейпы (\", \\, \n, \uXXXX и т.д.)
    text = re.sub(
        r'\\(["\\/bfnrtu]|u[0-9a-fA-F]{4})|\\',
        lambda m: m.group(0) if m.group(1) else r"\\\\",
        text,
    )

    data = json.loads(text)
    if not isinstance(data, dict):
        raise TypeError("LLM response is not a JSON object")
    return data

def placeholders_match(text: str, expected_text: str) -> bool:
    """Return whether placeholders are preserved with exact ids, counts and order."""
    expected_ids = PLACEHOLDER_PATTERN.findall(expected_text)
    actual_ids = PLACEHOLDER_PATTERN.findall(text)
    return actual_ids == expected_ids


def _suspicious_duplicate_keys(
    keys: list[str],
    translated: dict[str, object],
    items: dict[str, EngineItem],
) -> set[str]:
    return suspicious_duplicate_keys(
        {key: items[key].masked for key in keys},
        {key: translated.get(key) for key in keys},
    )


def _is_untranslated_candidate(
    item: EngineItem,
    candidate: str,
    target_lang: dict,
) -> bool:
    if target_lang.get("api") == "en":
        return False
    if candidate.strip() != item.masked.strip():
        return False
    if is_technical_term(item.original):
        return False
    visible_source = PLACEHOLDER_PATTERN.sub("", item.masked)
    return bool(re.search(r"[A-Za-z]", visible_source))


def repair_markers(
    call_api: Callable[[str, int], str | None],
    masked_source: str,
    broken_translation: str,
    max_tokens: int,
) -> str | None:
    """Просит модель восстановить маркеры [#N#] в готовом переводе.

    Дешёвый второй шанс: перевод уже хороший, но модель потеряла/сдвинула
    маркеры. Возвращает исправленный текст или None.
    """
    prompt = (
        "The translation below lost or corrupted some [#N#] markers.\n"
        "Restore the markers so the translation contains EXACTLY the same\n"
        "markers as the source (same ids, same counts). Do not retranslate.\n"
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
    repaired_text = _text_without_numbered_markers(raw)
    source_text = _text_without_numbered_markers(masked_source)
    broken_text = _text_without_numbered_markers(broken_translation)
    reverted_to_source = repaired_text == source_text and broken_text != source_text
    if raw and placeholders_match(raw, masked_source) and not reverted_to_source:
        return raw
    return None


def _text_without_numbered_markers(text: str) -> str:
    text = PLACEHOLDER_PATTERN.sub("", text)
    text = re.sub(r"(?<!\[)#\s*\d+\s*#(?!\])", "", text)
    return re.sub(r"\s+", " ", text).strip()

def build_marker_manifest(payload: dict[str, str]) -> str:
    """Явный чек-лист маркеров для каждого ключа запроса.
    Модель видит точный список вместо абстрактного правила."""
    lines: list[str] = []
    for key, value in payload.items():
        counts = Counter(PLACEHOLDER_PATTERN.findall(value))
        if not counts:
            lines.append(f'"{key}": (plain text, no placeholders)')
            continue
        listing = " ".join(
            f"[#{n}#]" + (f"x{c}" if c > 1 else "")
            for n, c in sorted(counts.items(), key=lambda kv: int(kv[0]))
        )
        lines.append(f'"{key}": {listing}')
    return (
        "MARKER WHITELIST (exact markers allowed per key):\n"
        + "\n".join(lines)
        + "\nRULE: the translation of each key must contain exactly the markers from its list "
        "in the same order — "
        "no others (if the list ends at [#11#], writing [#12#] is an error), "
        "no skips, no renumbering, no repeats (x2 means exactly two copies)."
    )

def split_by_placeholders(masked: str, max_per_chunk: int = 10) -> list[str]:
    """
    Разбивает строку с маркерами на чанки по ≤max_per_chunk маркеров.
    Разбивает по естественным границам (пробел, точка, \\ \\).
    """
    matches = list(PLACEHOLDER_PATTERN.finditer(masked))
    if len(matches) <= max_per_chunk:
        return [masked]

    chunks = []
    start = 0
    count = 0

    for match in matches:
        count += 1
        if count == max_per_chunk:
            end = match.end()
            # Ищем естественную границу разрыва в следующих 80 символах
            rest = masked[end:]
            break_match = re.search(r'(\s*\\\\?\s*\\\\?\s*|[.!?\n]\s+|\s{2,})', rest[:80])
            if break_match:
                end += break_match.end()
            else:
                # Фоллбэк: ближайший пробел
                space_match = re.search(r'\s', rest[:50])
                if space_match:
                    end += space_match.end()
            chunks.append(masked[start:end])
            start = end
            count = 0

    if start < len(masked):
        chunks.append(masked[start:])

    return [c for c in chunks if c.strip()]

def _fix_marker_typos(raw: str, masked_source: str) -> str:
    """Auto-fixes AI typos in markers."""
    orig_markers = PLACEHOLDER_PATTERN.findall(masked_source)
    if not orig_markers:
        return raw

    expected_ids = set(orig_markers)

    def restore_bare_marker(match: re.Match) -> str:
        marker_id = match.group(1)
        before = match.string[:match.start()].rstrip()
        after = match.string[match.end():].lstrip()
        if before.endswith("[") and after.startswith("]"):
            return match.group(0)
        return f"[#{marker_id}#]" if marker_id in expected_ids else match.group(0)

    raw = re.sub(
        r"(?<!\[)#\s*(\d+)\s*#(?!\])",
        restore_bare_marker,
        raw,
    )
    
    # Ищет сломанные маркеры: [#4%], [%4#], [4#], [№4№], 【#4】 и т.д.
    pattern = r'[\[【]\s*[#%№]+\s*(\d+)\s*[#%№]*\s*[\]】]|[\[【]\s*[#%№]*\s*(\d+)\s*[#%№]+\s*[\]】]'
    
    # Восстанавливаем опечатки до идеального [#N#]
    return re.sub(pattern, lambda m: f"[#{m.group(1) or m.group(2)}#]", raw)

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
        self.max_tokens = 8192 if mode == "context" else 4096

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
            
            # --- СИСТЕМА КАСКАДНЫХ ПОВТОРОВ (10 -> 5 -> 1) ---
            # Фильтруем безнадёжные строки (>20 маркеров) — их повторять бессмысленно
            hopeless = [
                k for k in failed
                if len(PLACEHOLDER_PATTERN.findall(items[k].masked)) > 20
            ]
            if hopeless:
                callbacks.on_log(
                    f"⚠️ {self.label}: {len(hopeless)} строк с >20 маркерами — повторы отключены",
                    "yellow",
                )
                # ВАЖНО: НЕ кладём оригинал в result! Иначе сервис закэширует
                # "английский = английский", и строка никогда не переведётся.
                # Просто убираем из failed: ретраи бесполезны, а сервис сам
                # подставит оригинал (без кэша) или отдаст строку в Google-фоллбэк.
                failed = [k for k in failed if k not in hopeless]

            active_retries = RETRY_BATCH_SIZES[:self.retries]
            for retry_number, retry_batch_size in enumerate(
                active_retries,
                start=1,
            ):
                if not failed or not callbacks.should_run():
                    break

                callbacks.on_log(
                    f"🔁 {self.label}: повтор {retry_number}/"
                    f"{len(RETRY_BATCH_SIZES)} — {len(failed)} строк",
                    "orange",
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
                            force_translation=True,
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
        *,
        force_translation: bool = False,
    ) -> list[str]:
        PLACEHOLDER_THRESHOLD = 20
        CHUNK_SIZE = 3
        # Разделяем ключи на обычные и сложные
        normal_keys = []
        complex_keys = []
        for key in chunk_keys:
            ph_count = len(PLACEHOLDER_PATTERN.findall(items[key].masked))
            if ph_count > PLACEHOLDER_THRESHOLD:
                complex_keys.append(key)
            else:
                normal_keys.append(key)

        all_failed: list[str] = []

        # === ОБРАБОТКА СЛОЖНЫХ СТРОК (>20 маркеров) по чанкам ===
        for key in complex_keys:
            item = items[key]
            ph_total = len(PLACEHOLDER_PATTERN.findall(item.masked))
            sub_chunks = split_by_placeholders(item.masked, max_per_chunk=CHUNK_SIZE)
            translated_parts: list[str] = []

            callbacks.on_log(
                f"🧩 {self.label}: строка с {ph_total} маркерами → {len(sub_chunks)} чанков",
                "blue",
            )

            success = True
            for sub_idx, sub_text in enumerate(sub_chunks, 1):
                if not callbacks.should_run():
                    return chunk_keys
                callbacks.wait_if_paused()
                if not callbacks.should_run():
                    return chunk_keys

                # Для чанков НЕ используем JSON — модель отвечает чистым текстом
                chunk_manifest = build_marker_manifest({"TEXT": sub_text})
                sub_prompt = (
                    f"Translate the following text into {target_lang['name']}.\n"
                    f"RULES:\n"
                    f"1. Output ONLY the translated text, no explanations.\n"
                    f"2. You MUST preserve ALL [#N#] placeholders exactly as they appear in the original text.\n\n"
                    f"{chunk_manifest}\n\n"
                    f"TEXT TO TRANSLATE:\n{sub_text}"
                )

                chunk_ok = False
                for _attempt in range(2):  # 2 попытки на КАЖДЫЙ чанк
                    try:
                        content = self._call_api(sub_prompt, self.max_tokens)
                        if not content:
                            continue
                        # Убираем возможные ```-обёртки
                        raw = re.sub(
                            r"^```[a-z]*\s*|\s*```$",
                            "",
                            content.strip(),
                            flags=re.IGNORECASE | re.MULTILINE,
                        ).strip()
                        if raw:
                            raw = _fix_marker_typos(raw, sub_text)
                            
                        if raw and placeholders_match(raw, sub_text):
                            translated_parts.append(raw)
                            chunk_ok = True
                            callbacks.on_log(f"   ✔️ Чанк {sub_idx}/{len(sub_chunks)} переведен", "green")
                            break
                        if raw:
                            fixed = repair_markers(
                                self._call_api, sub_text, raw, self.max_tokens
                            )
                            if fixed is not None:
                                translated_parts.append(fixed)
                                chunk_ok = True
                                break
                    except requests.RequestException:
                        continue

                if not chunk_ok:
                    callbacks.on_log(
                        f"❌ {self.label}: чанк {sub_idx}/{len(sub_chunks)} не прошёл проверку",
                        "red",
                    )
                    dump_ai_error(
                        sub_text,
                        content if 'content' in locals() else "Нет ответа",
                        f"Чанк {sub_idx}/{len(sub_chunks)} строки с {ph_total} маркерами",
                    )
                    success = False
                    break

            if success and len(translated_parts) == len(sub_chunks):
                combined_masked = "".join(translated_parts)
                text = unmask_translation(combined_masked, item.mapping)
                result[key] = polish_translation(
                    text,
                    boundary_source=item.original,
                )
            else:
                all_failed.append(key)
        # === КОНЕЦ ОБРАБОТКИ СЛОЖНЫХ СТРОК ===

        # === ОБРАБОТКА ОБЫЧНЫХ СТРОК (≤20 маркеров) — стандартный путь ===
        if normal_keys:
            payload = {key: items[key].masked for key in normal_keys}
            prompt = build_translation_prompt(
                payload,
                target_lang["name"],
                mode=self.mode,
                context=self.context,
                prompt_type=self.prompt_type,
                force_translation=force_translation,
            )
            callbacks.on_status(f"⏳ {self.label}: пакет {len(normal_keys)}...")
            try:
                content = self._call_api(prompt, self.max_tokens)
                if not content:
                    return all_failed + normal_keys
                translated = parse_llm_json_response(content)
                suspicious_duplicates = _suspicious_duplicate_keys(
                    normal_keys, translated, items
                )
                unexpected = set(translated) - set(normal_keys)
                if unexpected:
                    callbacks.on_log(
                        f"⚠️ {self.label}: отброшены лишние JSON-ключи — {len(unexpected)}",
                        "yellow",
                    )
                for key in normal_keys:
                    raw = translated.get(key)
                    if not isinstance(raw, str):
                        all_failed.append(key)
                        dump_ai_error(items[key].masked, str(raw), "Ключ утерян или значение не текст")
                        continue
                    if key in suspicious_duplicates:
                        all_failed.append(key)
                        dump_ai_error(
                            items[key].masked,
                            raw,
                            "Одинаковый длинный ответ для разных строк пакета",
                        )
                        continue
                    length_issue = translation_length_issue(items[key].masked, raw)
                    if length_issue:
                        all_failed.append(key)
                        dump_ai_error(items[key].masked, raw, length_issue)
                        continue
                    raw = _fix_marker_typos(raw, items[key].masked)
                    raw = collapse_added_line_breaks(items[key].masked, raw)

                    if count_line_breaks(raw) != count_line_breaks(
                        items[key].masked
                    ):
                        all_failed.append(key)
                        continue

                    if has_untranslated_leading_article(
                        items[key].masked,
                        raw,
                        target_lang,
                    ):
                        all_failed.append(key)
                        continue

                    if _is_untranslated_candidate(items[key], raw, target_lang):
                        all_failed.append(key)
                        continue

                    if not placeholders_match(raw, items[key].masked):
                        fixed = repair_markers(
                            self._call_api, items[key].masked, raw, self.max_tokens
                        )
                        if fixed is None:
                            all_failed.append(key)
                            dump_ai_error(items[key].masked, raw, "Потеряны/добавлены маркеры [#N#]")
                            continue
                        raw = fixed
                    text = unmask_translation(raw, items[key].mapping)
                    result[key] = polish_translation(
                        text,
                        boundary_source=items[key].original,
                    )
                if len(all_failed) > len(complex_keys):
                    callbacks.on_log(
                        f"❌ {self.label}: не прошли проверку — {len(all_failed) - len(complex_keys)} строк",
                        "red",
                    )
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                dump_ai_error(prompt, content if 'content' in locals() else "Нет ответа", str(exc))
                callbacks.on_log(f"❌ {self.label}: неверный JSON (сохранен в ai_error_log.txt)", "red")
                return all_failed + normal_keys
            except requests.RequestException as exc:
                callbacks.on_log(f"❌ {self.label}: сеть — {exc}", "red")
                return all_failed + normal_keys

        return all_failed
