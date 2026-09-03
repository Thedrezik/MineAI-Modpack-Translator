import json
import os
import re
from collections import Counter
from typing import Callable

import requests

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.clean_transport import (
    VisibleTextNode,
    extract_visible_nodes,
    numeric_placeholder_tokens,
    rebuild_masked,
    response_is_clean,
    sanitize_prompt_context,
)
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
GLOSSARY_FILE = "glossary.json"

# User dictionaries are intentionally external to the executable.  A compact
# seed is materialized beside the application on first use and can then be
# edited without rebuilding the EXE.
DEFAULT_GLOSSARY = {
    "_comment": "MineAI default glossary; edit this file to customize terms.",
    "Nether": "Нижний мир",
    "The End": "Край",
    "Overworld": "Обычный мир",
    "Quest": "Квест",
    "Quests": "Квесты",
    "Reward": "Награда",
    "Chapter": "Глава",
    "Task": "Задание",
    "Crafting Table": "Верстак",
    "Furnace": "Печь",
    "Blast Furnace": "Плавильная печь",
    "Smoker": "Коптильня",
    "Anvil": "Наковальня",
    "Ore": "Руда",
    "Ingot": "Слиток",
    "Nugget": "Самородок",
    "Dust": "Пыль",
    "Machine": "Машина",
    "Generator": "Генератор",
    "Reactor": "Реактор",
    "Tank": "Бак",
    "Pipe": "Труба",
    "Cable": "Кабель",
    "Energy": "Энергия",
    "Power": "Мощность",
    "Fluid": "Жидкость",
    "Gas": "Газ",
    "Tier": "Уровень",
    "Upgrade": "Улучшение",
    "Progress": "Прогресс",
    "Unlock": "Разблокировать",
    "Complete": "Завершить",
    "Craft": "Создать",
    "Smelt": "Переплавить",
    "Mine": "Добыть",
    "Kill": "Убить",
    "Obtain": "Получить",
    "Collect": "Собрать",
    "Reach": "Достичь",
}


def load_glossary() -> dict[str, str]:
    """Load eng->target glossary from glossary.json; skip comment keys (_...)."""
    if not os.path.exists(GLOSSARY_FILE):
        payload = json.dumps(DEFAULT_GLOSSARY, ensure_ascii=False, indent=4)
        try:
            atomic_write_text(GLOSSARY_FILE, payload)
        except OSError:
            # A read-only install can still translate with the built-in seed.
            pass
        return {k: v for k, v in DEFAULT_GLOSSARY.items() if not k.startswith("_")}
    try:
        with open(GLOSSARY_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {}
        return {
            k: v for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and not k.startswith("_")
        }
    except Exception:
        return {}


# Materialize the editable glossary during application startup just like the
# existing dictionary loader does.  Translation prompts still reload it so
# edits made while the program is open are picked up by the next batch.
load_glossary()


_LEGACY_DEFAULT_PROMPTS = {
    "mods": {
        "Translate the following JSON string values from English to {lang_name}.",
    },
    "books": {
        "Ты локализатор Minecraft. Переведи текст книги/справочника на {lang_name}. Сохраняй игровой лор и литературный стиль.",
    },
    "quests": {
        "Ты локализатор Minecraft. Переведи строки мода/квеста «{context}» на {lang_name}. Сохраняй игровой стиль и лор.",
    },
    "technical": {
        "STRICT RULES:\n1. Do not translate or change JSON keys.\n2. Preserve ALL [#N#] placeholders exactly. If a word is wrapped like [#0#]Word[#1#], wrap the translation like [#0#]Слово[#1#]. DO NOT drop any markers.\n3. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside the JSON strings.\n4. Output ONLY raw valid JSON. No markdown formatting, no explanations, no intro text.",
        "STRICT RULES:\n1. Do not translate or change JSON keys.\n2. Preserve ALL [#N#] placeholders exactly and in the same order. If a word is wrapped like [#0#]Word[#1#], wrap the translation like [#0#]Слово[#1#]. DO NOT drop, repeat or reorder markers.\n3. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside the JSON strings.\n4. Output ONLY raw valid JSON. No markdown formatting, no explanations, no intro text.",
    },
}


def get_default_prompts() -> dict[str, str]:
    return {
        "mods": "Ты локализатор Minecraft. Переведи каждый переданный видимый текстовый узел с английского на {lang_name}. Коды, ссылки, числа, теги и структура уже удалены из запроса и восстанавливаются программой; не добавляй их и не добавляй пояснений.",
        "books": "Ты локализатор книг и справочников Minecraft. Переведи только переданные видимые текстовые узлы на {lang_name}, естественно и с учётом контекста «{context}». Не добавляй разметку, ссылки, теги, цвета, числа или переносы: программа восстанавливает их из оригинала.",
        "quests": "Ты локализатор квестов Minecraft. Переведи каждый переданный видимый текстовый узел названия или описания из «{context}» на {lang_name}. Коды, ссылки, числа и JSON-структура восстанавливаются программой; не добавляй их и не добавляй новые факты. Названия предметов, блоков, существ и наград переводи в именительном падеже (не в винительном). Например: «Кирка» (не «Кирку»), «Железный меч» (не «Железного меча»).",
        "technical": "STRICT RULES:\n1. Do not translate or change JSON keys.\n2. Preserve ALL [#N#] placeholders exactly and in the same order. Placeholders are immutable source fragments, including numbers and game codes. DO NOT drop, repeat, rename or reorder them.\n3. Translate every JSON value independently. Never merge neighboring values or copy one answer into another key.\n4. MUST escape all newlines as \\n. DO NOT output raw/literal newlines inside JSON strings.\n5. Output ONLY one raw valid JSON object with the original keys. No markdown, explanations or introductory text."
    }


def load_prompts() -> dict[str, str]:
    if not os.path.exists(PROMPTS_FILE):
        save_prompts(get_default_prompts())
        return get_default_prompts()
    try:
        with open(PROMPTS_FILE, "r", encoding="utf-8-sig") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            return get_default_prompts()
        defaults = get_default_prompts()
        changed = False
        for key, default in defaults.items():
            current = loaded.get(key)
            if current is None or current in _LEGACY_DEFAULT_PROMPTS.get(key, set()):
                loaded[key] = default
                changed = True
        if changed:
            save_prompts(loaded)
        return loaded
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
    # --- M1: inject glossary for consistent translations ---
    glossary = load_glossary()
    # Only relevant terms (those appearing in the payload)
    payload_text = " ".join(payload.values()).lower()
    relevant_glossary = {
        k: v for k, v in glossary.items()
        if k.lower() in payload_text or k.lower().rstrip("s") in payload_text
    }

    tech_rules = prompts.get("technical", get_default_prompts()["technical"])
    tech_rules += (
        "\nEach JSON key is an independent source row. Never combine text from "
        "different keys and never copy one key's translation into another key."
        "\nTransport keys are opaque IDs; return them exactly and never reproduce "
        "internal file paths or adapter locators in a value."
    )

    # --- ЯВНЫЙ СПИСОК МАРКЕРОВ: что именно нельзя менять ---
    from mineai.text_processing import PLACEHOLDER_PATTERN
    has_markers = any(PLACEHOLDER_PATTERN.search(v) for v in payload.values())

    if not has_markers:
        # Убираем все упоминания о маркерах из промпта
        tech_rules = re.sub(r'(?i)\n?.*\[#N#\].*', '', tech_rules)
        tech_rules = re.sub(r'(?i)\n?.*markers.*', '', tech_rules)
        tech_rules = tech_rules.replace("{markers}", "")
        glossary_block_nm = ""
        if relevant_glossary:
            lines_nm = "\n".join(f"  {k} = {v}" for k, v in list(relevant_glossary.items())[:30])
            glossary_block_nm = f"\nGLOSSARY (use these exact translations for consistency):\n{lines_nm}\n"
        return f"{intro}\n\n{tech_rules.strip()}{glossary_block_nm}\n\nDATA:\n{blob}"

    manifest = build_marker_manifest(payload)
    if "{markers}" in tech_rules:
        # Если вставил {markers} в редакторе промптов — список встанет туда
        tech_rules = tech_rules.replace("{markers}", manifest)
    else:
        # Иначе блок дописывается сразу после тех. правил
        tech_rules = f"{tech_rules}\n\n{manifest}"

    glossary_block = ""
    if relevant_glossary:
        lines = "\n".join(f"  {k} = {v}" for k, v in list(relevant_glossary.items())[:30])
        glossary_block = f"\nGLOSSARY (use these exact translations for consistency):\n{lines}\n"

    return (
        f"{intro}\n\n"
        f"{tech_rules}{glossary_block}\n\n"
        f"DATA:\n{blob}"
    )


def build_clean_translation_prompt(
    payload: list[str],
    lang_name: str,
    *,
    mode: str,
    context: str,
    prompt_type: str = "mods",
    force_translation: bool = False,
) -> str:
    """Build a prompt whose data section contains visible text only.

    Unlike the legacy object transport, this protocol has no adapter keys.  The
    array order is the only association the caller needs; the original template
    is restored locally after the response.  Numeric/roman-level placeholders
    are included only when they are needed to keep one sentence together.
    """

    blob = json.dumps(payload, ensure_ascii=False)
    prompts = load_prompts()
    intro_template = prompts.get(prompt_type, get_default_prompts()["mods"])
    safe_context = sanitize_prompt_context(context)
    intro = intro_template.replace("{lang_name}", lang_name).replace("{context}", safe_context)
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
            "\nEvery array element below requires a real translation. "
            f"Do not copy English prose unchanged; translate it naturally into {lang_name}."
        )

    glossary = load_glossary()
    payload_text = " ".join(payload).lower()
    relevant_glossary = {
        key: value
        for key, value in glossary.items()
        if (key.lower() in payload_text or key.lower().rstrip("s") in payload_text)
        and response_is_clean(key)
        and response_is_clean(value)
    }
    glossary_block = ""
    if relevant_glossary:
        lines = "\n".join(
            f"  {key} = {value}" for key, value in list(relevant_glossary.items())[:30]
        )
        glossary_block = (
            "\nGLOSSARY (use these exact translations for consistency):\n"
            f"{lines}\n"
        )

    has_numeric_placeholders = any(PLACEHOLDER_PATTERN.search(value) for value in payload)
    placeholder_rule = (
        "Some elements contain immutable [#N#] tokens for numeric values or "
        "Roman level markers. Preserve each such token exactly and in the same "
        "order; do not translate, remove, duplicate or move it.\n"
        if has_numeric_placeholders
        else ""
    )
    rules = (
        "STRICT TRANSPORT RULES:\n"
        "1. DATA is an ordered JSON array of visible prose fragments.\n"
        "2. Return exactly one JSON array of strings, with the same length and order.\n"
        "3. Translate only the text in each element. Do not add markup, links, tags, "
        "formatting codes, numbers or explanations.\n"
        f"{placeholder_rule}"
        "4. The application restores all protected syntax, spacing and document structure.\n"
        "5. Never merge, split, reorder, omit or duplicate array elements.\n"
        "6. Output only valid JSON; no Markdown fences or commentary."
    )
    return f"{intro}\n\n{rules}{glossary_block}\n\nDATA:\n{blob}"


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


def parse_llm_array_response(content: str) -> list[str]:
    """Parse the clean transport response and require an array of strings."""

    text = re.sub(
        r"^```(?:json)?\s*\n?|\n?```$",
        "",
        str(content or "").strip(),
        flags=re.IGNORECASE | re.MULTILINE,
    ).strip()
    start_idx = text.find("[")
    if start_idx < 0:
        raise TypeError("LLM response is not a JSON array")

    stack = 0
    in_str = False
    escaped = False
    end_idx = None
    for index in range(start_idx, len(text)):
        char = text[index]
        if in_str:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_str = False
            continue
        if char == '"':
            in_str = True
        elif char == "[":
            stack += 1
        elif char == "]":
            stack -= 1
            if stack == 0:
                end_idx = index + 1
                break
    if end_idx is None:
        raise TypeError("unterminated JSON array")
    trailing = text[end_idx:].strip()
    if trailing and trailing != "```":
        raise ValueError("extra data after JSON array")
    text = text[start_idx:end_idx]

    # A few local models emit literal line breaks inside JSON strings.  Keep the
    # parser as forgiving as the legacy object parser, without accepting objects
    # or arbitrary scalar responses.
    def _fix_newlines_in_strings(match: re.Match) -> str:
        return match.group(0).replace("\n", "\\n").replace("\r", "\\r")

    text = re.sub(r'"(?:[^"\\]|\\.)*"', _fix_newlines_in_strings, text)
    text = re.sub(
        r'\\(["\\/bfnrtu]|u[0-9a-fA-F]{4})|\\',
        lambda match: match.group(0) if match.group(1) else r"\\\\",
        text,
    )
    data = json.loads(text)
    if not isinstance(data, list) or not all(isinstance(value, str) for value in data):
        raise TypeError("LLM response array must contain only strings")
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


def _wire_key_map(keys: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """Hide structured adapter locators from the LLM transport payload.

    FormatKit uses ids such as ``json:/pages/0/title`` to put a translated
    value back into the original document.  Those ids are implementation
    details, not text for the model.  Keep ordinary legacy keys unchanged for
    compatibility, but replace structured JSON locators with opaque per-call
    ids and restore the original ids immediately after parsing the response.
    """
    wire_by_internal: dict[str, str] = {}
    internal_by_wire: dict[str, str] = {}
    used = set(keys)
    next_id = 0
    for key in keys:
        is_structured = key.startswith(
            ("json:", "key:", "line:", "span:", "token:", "oracle-meta:")
        ) or "#json:" in key or "json:/" in key
        if not is_structured:
            wire_key = key
        else:
            while True:
                wire_key = f"unit_{next_id}"
                next_id += 1
                if wire_key not in used and wire_key not in internal_by_wire:
                    break
        wire_by_internal[key] = wire_key
        internal_by_wire[wire_key] = key
    return wire_by_internal, internal_by_wire


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
            # В Beta42 даже строки с большим числом защищённых фрагментов
            # передаются как чистые видимые узлы, поэтому для них безопасны
            # обычные повторы.  Ранее такие строки ошибочно считались
            # «безнадёжными» из-за количества маркеров.
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
        """Translate a batch using only visible text nodes.

        The legacy marker/object implementation remains below as a private
        compatibility reference, but all new requests use this lossless path.
        """

        return self._translate_clean_chunk(
            chunk_keys,
            items,
            target_lang,
            result,
            callbacks,
            force_translation=force_translation,
        )

    def _translate_clean_chunk(
        self,
        chunk_keys: list[str],
        items: dict[str, EngineItem],
        target_lang: dict,
        result: dict[str, str],
        callbacks: EngineCallbacks,
        *,
        force_translation: bool = False,
    ) -> list[str]:
        node_map: dict[str, tuple[VisibleTextNode, ...]] = {
            key: extract_visible_nodes(
                items[key].masked,
                coalesce_numeric=numeric_placeholder_tokens(items[key].mapping),
            )
            for key in chunk_keys
        }
        flat_nodes: list[tuple[str, VisibleTextNode]] = [
            (key, node)
            for key in chunk_keys
            for node in node_map[key]
        ]

        # A value made entirely of protected syntax (for example an item id or
        # a colour-only label) has no translatable payload.  Keep its exact
        # source rather than asking an engine to hallucinate a replacement.
        rebuilt_by_key: dict[str, str] = {}
        for key in chunk_keys:
            if not node_map[key]:
                result[key] = items[key].original

        if not flat_nodes:
            return []

        payload = [node.text for _key, node in flat_nodes]
        prompt = build_clean_translation_prompt(
            payload,
            target_lang["name"],
            mode=self.mode,
            context=self.context,
            prompt_type=self.prompt_type,
            force_translation=force_translation,
        )
        callbacks.on_status(f"⏳ {self.label}: чистый пакет {len(payload)}...")
        try:
            content = self._call_api(prompt, self.max_tokens)
            if not content:
                return list(chunk_keys)
            try:
                translated_nodes = parse_llm_array_response(content)
            except (TypeError, ValueError, json.JSONDecodeError):
                # Older user prompts/custom local servers may still return the
                # pre-Beta42 object protocol.  Accept it as a compatibility
                # path, while all newly generated requests remain arrays of
                # clean text nodes.
                legacy = parse_llm_json_response(content)
                return self._accept_legacy_result(
                    chunk_keys,
                    items,
                    target_lang,
                    result,
                    legacy,
                )
            if len(translated_nodes) != len(payload):
                dump_ai_error(
                    prompt,
                    content,
                    f"Неверное число текстовых узлов: ожидалось {len(payload)}, "
                    f"получено {len(translated_nodes)}",
                )
                return list(chunk_keys)
        except (json.JSONDecodeError, TypeError, ValueError, requests.RequestException) as exc:
            dump_ai_error(prompt, content if "content" in locals() else "Нет ответа", str(exc))
            callbacks.on_log(
                f"❌ {self.label}: неверный ответ чистого транспорта "
                "(сохранен в ai_error_log.txt)",
                "red",
            )
            return list(chunk_keys)

        translated_by_key: dict[str, list[str]] = {key: [] for key in chunk_keys}
        failed: set[str] = set()
        for (key, node), translated in zip(flat_nodes, translated_nodes):
            value = translated.strip()
            can_drop_article = (
                force_translation
                and
                node.text.casefold() in {"a", "an", "the"}
                and target_lang.get("api") != "en"
                and (
                    not value
                    or value.casefold() == node.text.casefold()
                )
            )
            if can_drop_article:
                # Treat both an empty response and an unchanged standalone
                # article as the same safe operation: omit the article while
                # keeping the following protected fragment in its source slot.
                value = ""
            expected_placeholders = frozenset(
                match.group(0) for match in PLACEHOLDER_PATTERN.finditer(node.text)
            )
            if (
                not can_drop_article
                and (
                    not placeholders_match(value, node.text)
                    or not response_is_clean(
                        value,
                        allowed_placeholders=expected_placeholders,
                    )
                )
            ):
                failed.add(key)
                continue
            translated_by_key[key].append(value)

        for key in chunk_keys:
            nodes = node_map[key]
            if not nodes:
                continue
            if key in failed or len(translated_by_key[key]) != len(nodes):
                failed.add(key)
                continue
            item = items[key]
            try:
                rebuilt = rebuild_masked(
                    item.masked,
                    nodes,
                    translated_by_key[key],
                )
            except ValueError:
                failed.add(key)
                continue
            if count_line_breaks(rebuilt) != count_line_breaks(item.masked):
                failed.add(key)
                continue
            if translation_length_issue(item.masked, rebuilt):
                failed.add(key)
                continue
            if has_untranslated_leading_article(item.masked, rebuilt, target_lang):
                failed.add(key)
                continue
            if _is_untranslated_candidate(item, rebuilt, target_lang):
                failed.add(key)
                continue
            rebuilt_by_key[key] = rebuilt

        duplicate_keys = suspicious_duplicate_keys(
            {key: items[key].masked for key in rebuilt_by_key},
            rebuilt_by_key,
        )
        failed.update(duplicate_keys)
        for key, rebuilt in rebuilt_by_key.items():
            if key in failed:
                continue
            item = items[key]
            text = unmask_translation(rebuilt, item.mapping)
            result[key] = polish_translation(text, boundary_source=item.original)

        if failed:
            callbacks.on_log(
                f"❌ {self.label}: не прошли проверку — {len(failed)} строк",
                "red",
            )
        return [key for key in chunk_keys if key in failed]

    def _accept_legacy_result(
        self,
        chunk_keys: list[str],
        items: dict[str, EngineItem],
        target_lang: dict,
        result: dict[str, str],
        translated: dict[str, object],
    ) -> list[str]:
        """Read a legacy object response without exposing its keys to the LLM."""

        by_key: dict[str, object] = {
            key: translated[key] for key in chunk_keys if key in translated
        }
        if not by_key and len(translated) == len(chunk_keys):
            by_key = dict(zip(chunk_keys, translated.values()))
        failed: list[str] = []
        for key in chunk_keys:
            raw = by_key.get(key)
            if not isinstance(raw, str):
                failed.append(key)
                continue
            raw = _fix_marker_typos(raw, items[key].masked)
            raw = collapse_added_line_breaks(items[key].masked, raw)
            if (
                count_line_breaks(raw) != count_line_breaks(items[key].masked)
                or translation_length_issue(items[key].masked, raw)
                or has_untranslated_leading_article(items[key].masked, raw, target_lang)
                or _is_untranslated_candidate(items[key], raw, target_lang)
                or not placeholders_match(raw, items[key].masked)
            ):
                failed.append(key)
                continue
            result[key] = polish_translation(
                unmask_translation(raw, items[key].mapping),
                boundary_source=items[key].original,
            )
        return failed

    def _translate_chunk_legacy(
        self,
        chunk_keys: list[str],
        items: dict[str, EngineItem],
        target_lang: dict,
        result: dict[str, str],
        callbacks: EngineCallbacks,
        *,
        force_translation: bool = False,
    ) -> list[str]:
        PLACEHOLDER_THRESHOLD = 8   # H1: lower from 20 to catch 8+ marker strings
        CHUNK_SIZE = 4
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
                            
                        if raw:
                            raw = polish_translation(
                                raw,
                                boundary_source=sub_text,
                            )
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
                                translated_parts.append(
                                    polish_translation(
                                        fixed,
                                        boundary_source=sub_text,
                                    )
                                )
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
            wire_by_internal, internal_by_wire = _wire_key_map(normal_keys)
            payload = {
                wire_by_internal[key]: items[key].masked
                for key in normal_keys
            }
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
                wire_translated = parse_llm_json_response(content)
                # Accept old cached/model responses, but never expose
                # structured adapter locators in a new request.
                translated = {
                    internal_by_wire.get(key, key): value
                    for key, value in wire_translated.items()
                }
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
