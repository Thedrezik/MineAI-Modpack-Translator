import json
import re
from functools import lru_cache

from mineai.constants import DICT_FILE, IGNORE_TERMS


PLACEHOLDER_PATTERN = re.compile(r"\[\s*#\s*(\d+)\s*#\s*\]")

FORMAT_PATTERN = re.compile(
    r"("
    r"\$\([^)]+\)|"
    r"[&§][0-9a-fk-orlmn]|"
    r"<[^>]+>|"
    r"\{[^\}]+\}|"
    r"\]\([^)]+\)|"
    r"!\[[^\]]*\]|"
    r"\[[a-z0-9_.-]+:[a-z0-9_./-]+\]|"
    r"\([a-z0-9_.-]+:[a-z0-9_./-]+\)|"
    r"\([A-Za-z0-9_./-]+\.md[#a-zA-Z0-9_-]*\)|"
    r"\n|"
    r"%[0-9.,]*\$?[a-zA-Z%]"
    r")",
    flags=re.IGNORECASE,
)

IGNORE_PATTERN = re.compile(
    r"(?<![a-zA-Z])(" + "|".join(re.escape(t) for t in IGNORE_TERMS) + r")(?![a-zA-Z])"
)


def apply_smart_glue(text: str) -> str:
    if not text:
        return text
    return re.sub(
        r"(?<![.!?>\]:])\s*(?:\\n|\r?\n)\s*(?!(?:[\r\n\-*#<]|$|---|[\w\s]+:))",
        " ",
        text,
    )


def load_dictionary() -> dict[str, str]:
    if not __import__("os").path.exists(DICT_FILE):
        default = {"полуслой": "плита", "сыромятная медь": "сырая медь"}
        with open(DICT_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=4)
        return default
    try:
        with open(DICT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


TERMINOLOGY_FIXES = load_dictionary()


def polish_translation(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    # Чиним пробелы внутри самих Minecraft-кодов:
    text = re.sub(r"([&§])\s+([0-9a-fk-or])", r"\1\2", text, flags=re.IGNORECASE)

    # Убираем случайный пробел после цветовых/стилевых кодов (НЕ трогая &r / §r)
    text = re.sub(r"([&§][0-9a-fk-o])\s+", r"\1", text, flags=re.IGNORECASE)

    # Убираем дублирующийся пробел перед reset-кодом
    text = re.sub(r"\s+([&§]r)(?=\s)", r"\1", text, flags=re.IGNORECASE)

    # Добавляем пробел, если reset-код склеился со следующим словом ("уровня&rи" -> "уровня&r и")
    text = re.sub(r"(?<=[^\s&§])([&§]r)(?=[^\W_])", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"\[\s+(%\d*\$?[sd])\s+\]", r"[\1]", text)
    text = re.sub(r"\(\s+(%\d*\$?[sd])\s+\)", r"(\1)", text)
    text = re.sub(r'\"\s+(%\d*\$?[sd])\s+\"', r'"\1"', text)
    text = re.sub(r"%\s+([sd])", r"%\1", text)
    text = re.sub(r"%\s+(\d+)\s*\$\s*([sd])", r"%\1$\2", text)
    text = re.sub(r"%\s*\.\s*(\d+)\s*([fd])", r"%.\1\2", text)
    text = re.sub(r"\]\s+\(", "](", text)
    text = re.sub(r"!\s+\[", "![", text)
    text = re.sub(r"\[\s+", "[", text)
    text = re.sub(r"\s+\]", "]", text)
    text = re.sub(r" {2,}", " ", text)

    for wrong, right in TERMINOLOGY_FIXES.items():

        def repl(match, r=right):
            word = match.group(0)
            if word.istitle():
                return r.capitalize()
            if word.isupper():
                return r.upper()
            return r

        text = re.sub(r"\b" + re.escape(wrong) + r"\b", repl, text, flags=re.IGNORECASE)
    return text


def mask_protected_fragments(text: str) -> tuple[str, dict[str, str]]:
    """Replace format codes and protected terms with collision-free placeholders."""
    mapping: dict[str, str] = {}
    reserved_ids = set(PLACEHOLDER_PATTERN.findall(text))
    next_id = 0

    def replacer(match: re.Match) -> str:
        nonlocal next_id
        while str(next_id) in reserved_ids:
            next_id += 1
        token = f"[#{next_id}#]"
        next_id += 1
        mapping[token] = match.group(0)
        return token

    masked = IGNORE_PATTERN.sub(replacer, FORMAT_PATTERN.sub(replacer, text))
    masked = re.sub(r"\s+", " ", masked).strip()
    return masked, mapping


def unmask_translation(text: str, mapping: dict[str, str]) -> str:
    for token, original in mapping.items():
        idx = token.strip("#[]")
        text = re.sub(rf"\[\s*#\s*{re.escape(idx)}\s*#\s*\]", lambda _m, o=original: o, text)
    return text


@lru_cache(maxsize=10000)
def is_technical_term(text: str) -> bool:
    if not text:
        return True
    lower = text.lower()
    if not re.search(r"[a-z]", lower):
        return True
    if re.match(r"^[a-z0-9_.-]+$", lower) and any(c in lower for c in "._"):
        return True
    prefixes = (
        "glyph_", "ritual_", "familiar_", "source_", "mana_", "spell_",
        "effect_", "rune_", "altar_", "botania_", "create_", "kubejs_",
    )
    return any(lower.startswith(p) for p in prefixes)


def is_translation_key(text: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_-]+[.:][a-zA-Z0-9_.-]+$", text.strip()))


def looks_like_source_language(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", text))


def already_translated(text: str, target_regex: str) -> bool:
    return bool(re.search(target_regex, text))
