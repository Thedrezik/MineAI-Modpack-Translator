import json
import re
from functools import lru_cache

from formatkit.tokenizer import MODONOMICON_STYLE_SOURCE
from mineai.constants import DICT_FILE, IGNORE_TERMS
from mineai.io_utils import atomic_write_text


_IGNORE_TERMS_CASEFOLD = frozenset(
    term.casefold() for term in IGNORE_TERMS
)


PLACEHOLDER_PATTERN = re.compile(r"\[\s*#\s*(\d+)\s*#\s*\]")

MARKDOWN_LINK_PATTERN = re.compile(
    r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)"
)

MARKDOWN_IMAGE_PATTERN = re.compile(
    r"!\[([^\]\n]*)\]\(([^)\n]+)\)"
)

MODONOMICON_STYLE_PATTERN = re.compile(
    MODONOMICON_STYLE_SOURCE,
    flags=re.IGNORECASE,
)

MARKDOWN_INLINE_CODE_PATTERN = re.compile(
    r"(?P<ticks>`+)(?P<body>[^`\r\n]+)(?P=ticks)"
)

MARKDOWN_BOLD_PATTERN = re.compile(
    r"(?<![\\*])\*\*(?=\S)(?P<body>.+?)(?<=\S)\*\*(?!\*)"
)

MARKDOWN_ITALIC_PATTERN = re.compile(
    r"(?<![\\*])\*(?=\S)(?P<body>[^*\r\n]+?)(?<=\S)\*(?!\*)"
)

MARKDOWN_BOLD_ITALIC_PATTERN = re.compile(
    r"(?<![\\*])\*\*\*(?=\S)(?P<body>.+?)(?<=\S)\*\*\*(?!\*)"
)

COMPOUND_TECHNICAL_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9]*[A-Z])"
    r"(?=[A-Za-z0-9]*\d)"
    r"[A-Za-z][A-Za-z0-9]{2,}"
    r"(?![A-Za-z0-9])"
)

JSON_TEXT_VALUE_PATTERN = re.compile(
    r'(?P<prefix>\\?"text\\?"\s*:\s*\\?")'
    r'(?P<value>.*?)'
    r'(?P<suffix>\\?")(?=\s*[,}\]])'
)

FORMAT_PATTERN = re.compile(
    r"("
    + MODONOMICON_STYLE_SOURCE
    + r"|"
    r"⟦FK\d{4}⟧|"
    r"#[A-Za-z_][A-Za-z0-9_.:/-]*#|"
    r"\$\([^)]*\)|"
    r"[&§][0-9a-fk-orlmn]|"
    r"<[^>]+>|"
    r"\{[^\}]+\}|"
    r"\[[a-z0-9_.-]+:[a-z0-9_./-]+\]|"
    r"\([a-z0-9_.-]+:[a-z0-9_./-]+\)|"
    r"\([A-Za-z0-9_./-]+\.md[#a-zA-Z0-9_-]*\)|"
    r"\\[*_~]|"
    r"\\[nrt]|"
    r"\n|"
    r"\\+(?![\"])|"
    r"%[0-9.,]*\$?[a-zA-Z%]"
    r")",
    flags=re.IGNORECASE,
)

STRUCTURAL_FRAGMENT_PATTERN = re.compile(
    r"("
    + MODONOMICON_STYLE_SOURCE
    + r"|"
    r"⟦FK\d{4}⟧|"
    r"#[A-Za-z_][A-Za-z0-9_.:/-]*#|"
    r"\$\([^\r\n)]*\)|"
    r"[&§][0-9a-fk-orlmn]|"
    r"<[^>\r\n]+>|"
    r"\{[^}\r\n]+\}|"
    r"\[[a-z0-9_.-]+:[a-z0-9_./-]+\]|"
    r"\([a-z0-9_.-]+:[a-z0-9_./-]+\)|"
    r"\([A-Za-z0-9_./-]+\.md[#a-zA-Z0-9_-]*\)|"
    r"\\[*_~nrt]|"
    r"%[0-9.,]*\$?[a-zA-Z%]"
    r")",
    flags=re.IGNORECASE,
)

IGNORE_PATTERN = re.compile(
    r"(?<![a-zA-Z])(" + "|".join(re.escape(t) for t in IGNORE_TERMS) + r")(?![a-zA-Z])",
    flags=re.IGNORECASE,
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
        atomic_write_text(
            DICT_FILE,
            json.dumps(default, ensure_ascii=False, indent=4),
        )
        return default
    try:
        with open(DICT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


TERMINOLOGY_FIXES = load_dictionary()


def polish_translation(
    text: str,
    *,
    boundary_source: str | None = None,
) -> str:
    if not isinstance(text, str) or not text:
        return text
    if boundary_source is not None and not boundary_source.strip():
        return boundary_source
    leading = ""
    trailing = ""
    if boundary_source is not None:
        leading = re.match(r"\s*", boundary_source).group(0)
        trailing = re.search(r"\s*$", boundary_source).group(0)
    text = text.strip()
    angle_tags: dict[str, str] = {}

    def protect_angle_tag(match: re.Match) -> str:
        token = f"\ue000{len(angle_tags)}\ue001"
        angle_tags[token] = match.group(0)
        return token

    text = re.sub(r"<[^>\r\n]+>", protect_angle_tag, text)
    # Чиним пробелы внутри самих Minecraft-кодов:
    text = re.sub(r"([&§])\s+([0-9a-fk-or])", r"\1\2", text, flags=re.IGNORECASE)

    # Убираем случайный пробел после цветовых/стилевых кодов (НЕ трогая &r / §r)
    text = re.sub(r"([&§][0-9a-fk-o])\s+", r"\1", text, flags=re.IGNORECASE)

    # Убираем дублирующийся пробел перед reset-кодом
    text = re.sub(r"\s+([&§]r)(?=\s)", r"\1", text, flags=re.IGNORECASE)

    # Добавляем пробел, если reset-код склеился со следующим словом ("уровня&rи" -> "уровня&r и")
    text = re.sub(r"(?<=[^\s&§])([&§]r)(?=[^\W_])", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"([&§][0-9a-fk-or])(?=[A-Za-zА-Яа-яЁё])", r"\1 ", text, flags=re.IGNORECASE)
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
    text = re.sub(r"([&§][0-9a-fk-or])(?=[A-Za-zА-Яа-яЁё])", r"\1 ", text, flags=re.IGNORECASE)

    for wrong, right in TERMINOLOGY_FIXES.items():

        def repl(match, r=right):
            word = match.group(0)
            if word.istitle():
                return r.capitalize()
            if word.isupper():
                return r.upper()
            return r

        text = re.sub(r"\b" + re.escape(wrong) + r"\b", repl, text, flags=re.IGNORECASE)
    for token, angle_tag in angle_tags.items():
        text = text.replace(token, angle_tag)
    return leading + text + trailing


def count_line_breaks(text: str) -> int:
    return len(re.findall(r"\r\n|\r|\n", text))


def collapse_added_line_breaks(source: str, candidate: str) -> str:
    """Turn model-added line breaks into spaces for a single-line source."""
    if count_line_breaks(source) == 0 and count_line_breaks(candidate) > 0:
        return re.sub(r"[ \t]*(?:\r\n|\r|\n)[ \t]*", " ", candidate)
    return candidate


def mask_protected_fragments(text: str) -> tuple[str, dict[str, str]]:
    """Replace format codes and protected terms with collision-free placeholders."""
    mapping: dict[str, str] = {}
    internal_tokens: dict[str, str] = {}
    reserved_ids = set(PLACEHOLDER_PATTERN.findall(text))
    next_id = 0

    def reserve(fragment: str) -> str:
        nonlocal next_id
        while str(next_id) in reserved_ids:
            next_id += 1
        public_token = f"[#{next_id}#]"
        token = f"\ue100{next_id}\ue101"
        next_id += 1
        mapping[public_token] = fragment
        internal_tokens[token] = public_token
        return token

    def protect_markdown_image(match: re.Match) -> str:
        opening = reserve("![")
        closing = reserve(f"]({match.group(2)})")
        return opening + match.group(1) + closing

    def protect_markdown_link(match: re.Match) -> str:
        opening = reserve("[")
        closing = reserve(f"]({match.group(2)})")
        return opening + match.group(1) + closing

    def protect_inline_code(match: re.Match) -> str:
        ticks = match.group("ticks")
        return reserve(ticks) + match.group("body") + reserve(ticks)

    def protect_bold(match: re.Match) -> str:
        return reserve("**") + match.group("body") + reserve("**")

    def protect_italic(match: re.Match) -> str:
        return reserve("*") + match.group("body") + reserve("*")

    def protect_bold_italic(match: re.Match) -> str:
        return reserve("***") + match.group("body") + reserve("***")

    def replacer(match: re.Match) -> str:
        return reserve(match.group(0))

    json_text_matches = list(JSON_TEXT_VALUE_PATTERN.finditer(text))
    if json_text_matches:
        parts: list[str] = []
        cursor = 0
        for match in json_text_matches:
            structure = text[cursor : match.start("value")]
            if structure:
                parts.append(reserve(structure))
            parts.append(match.group("value"))
            cursor = match.end("value")
        if cursor < len(text):
            parts.append(reserve(text[cursor:]))
        text = "".join(parts)

    text = MODONOMICON_STYLE_PATTERN.sub(replacer, text)
    text = MARKDOWN_IMAGE_PATTERN.sub(protect_markdown_image, text)
    text = MARKDOWN_LINK_PATTERN.sub(protect_markdown_link, text)
    text = MARKDOWN_INLINE_CODE_PATTERN.sub(protect_inline_code, text)
    text = MARKDOWN_BOLD_ITALIC_PATTERN.sub(protect_bold_italic, text)
    text = MARKDOWN_BOLD_PATTERN.sub(protect_bold, text)
    text = MARKDOWN_ITALIC_PATTERN.sub(protect_italic, text)
    text = FORMAT_PATTERN.sub(replacer, text)
    text = COMPOUND_TECHNICAL_TOKEN_PATTERN.sub(replacer, text)
    masked = IGNORE_PATTERN.sub(replacer, text)
    masked = re.sub(r"\s+", " ", masked).strip()
    for internal, public in internal_tokens.items():
        masked = masked.replace(internal, public)
    return masked, mapping


def structural_fragments(text: str) -> tuple[str, ...]:
    """Return ordered game/markup codes that translation must not move or invent."""
    return tuple(match.group(0) for match in STRUCTURAL_FRAGMENT_PATTERN.finditer(text))


def translation_length_issue(source: str, candidate: str) -> str | None:
    """Reject only extreme truncation/expansion typical of batched row smearing."""
    source_length = len(re.sub(r"\s+", " ", source).strip())
    candidate_length = len(re.sub(r"\s+", " ", candidate).strip())
    source_words = re.findall(r"[A-Za-zА-Яа-яЁё]+", source)
    candidate_words = re.findall(r"[A-Za-zА-Яа-яЁё]+", candidate)
    if (
        source_length >= 18
        and len(source_words) >= 3
        and len(candidate_words) <= 1
        and candidate_length < int(source_length * 0.40)
    ):
        return (
            f"подозрительно короткий перевод ({candidate_length} при оригинале "
            f"{source_length})"
        )
    if source_length >= 40 and candidate_length < max(12, int(source_length * 0.30)):
        return (
            f"подозрительно короткий перевод ({candidate_length} при оригинале "
            f"{source_length})"
        )
    if source_length >= 20 and candidate_length > max(70, int(source_length * 3.0)):
        return (
            f"подозрительно длинный перевод ({candidate_length} при оригинале "
            f"{source_length})"
        )
    return None


def suspicious_duplicate_keys(
    sources: dict[str, str],
    candidates: dict[str, object],
) -> set[str]:
    """Find likely row-smearing where distinct inputs receive one long answer."""
    groups: dict[str, list[str]] = {}
    for key, candidate in candidates.items():
        if key not in sources or not isinstance(candidate, str):
            continue
        normalized = re.sub(r"\s+", " ", candidate).strip().casefold()
        if len(normalized) < 32:
            continue
        groups.setdefault(normalized, []).append(key)

    suspicious: set[str] = set()
    for grouped_keys in groups.values():
        if len(grouped_keys) < 2:
            continue
        distinct_sources = {
            re.sub(r"\s+", " ", sources[key]).strip().casefold()
            for key in grouped_keys
        }
        if len(distinct_sources) > 1:
            suspicious.update(grouped_keys)
    return suspicious


def unmask_translation(text: str, mapping: dict[str, str]) -> str:
    # A bounded extra pass also repairs placeholders nested by older masking code,
    # preserving the translated text around an exactly positioned format code.
    for _ in range(len(mapping) + 1):
        previous = text
        for token, original in mapping.items():
            idx = token.strip("#[]")
            text = re.sub(
                rf"\[\s*#\s*{re.escape(idx)}\s*#\s*\]",
                lambda _m, o=original: o,
                text,
            )
        if text == previous:
            break
    return text


@lru_cache(maxsize=10000)
def is_technical_term(text: str) -> bool:
    if not text:
        return True

    stripped = text.strip()
    if not stripped:
        return True

    if stripped.casefold() in _IGNORE_TERMS_CASEFOLD:
        return True

    if re.fullmatch(r"[’'](?:s|t|re|ve|ll|d|m)", stripped, re.IGNORECASE):
        return True

    if re.fullmatch(r"\d+(?:[.,]\d+)?x", stripped, re.IGNORECASE):
        return True

    if re.fullmatch(
        r"(?:⟦FK\d{4}⟧)?[A-Z][a-z]{3,} [a-z]{4,}\]",
        stripped,
    ):
        return True

    if re.fullmatch(r"#[A-Za-z0-9_.:/-]+#?", stripped):
        return True

    if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*\([^()\r\n]*\)", stripped):
        return True

    words = re.findall(r"[A-Za-z][A-Za-z0-9/+.-]*", stripped)
    if len(words) > 1 and all(
        word.casefold() in _IGNORE_TERMS_CASEFOLD for word in words
    ):
        return True

    if (
        " " not in stripped
        and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", stripped)
        and any(char.islower() for char in stripped)
        and sum(char.isupper() for char in stripped) >= 2
    ):
        return True

    lower = stripped.lower()

    if not re.search(r"[a-z]", lower):
        return True

    if re.fullmatch(r"[a-z0-9_-]+(?:[._][a-z0-9_-]+)+", lower):
        return True

    prefixes = (
        "glyph_",
        "ritual_",
        "familiar_",
        "source_",
        "mana_",
        "spell_",
        "effect_",
        "rune_",
        "altar_",
        "botania_",
        "create_",
        "kubejs_",
    )
    return any(lower.startswith(prefix) for prefix in prefixes)


def is_article_removed_technical_translation(
    source: str,
    candidate: str,
    target_api: str,
) -> bool:
    """Allow dropping only an English article before an immutable technical label."""
    if target_api == "en":
        return False
    match = re.fullmatch(
        r"\s*(?:a|an|the)\s+(.+?)\s*",
        source,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match is None:
        return False
    remainder = match.group(1).strip()
    return candidate.strip() == remainder and is_technical_term(remainder)


def is_translation_key(text: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9_-]+[.:][a-zA-Z0-9_.-]+$", text.strip()))


def looks_like_source_language(text: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", text))


def already_translated(text: str, target_regex: str) -> bool:
    return bool(re.search(target_regex, text))
