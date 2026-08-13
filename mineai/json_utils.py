import json
import re
from typing import Any, Iterator

from mineai.constants import KEYS_TO_TRANSLATE
from mineai.formats.document import DocumentPath


_TRANSLATABLE_KEY_TOKEN = re.compile(
    r"^(?:"
    r"names?|titles?\d*|texts?\d*|descriptions?|subtexts?|subtitles?|"
    r"labels?|headers?|headings?|tooltips?|effects?|property|properties|"
    r"translations?"
    r")$",
    re.IGNORECASE,
)


def _is_translatable_json_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    if key in KEYS_TO_TRANSLATE:
        return True
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    tokens = re.split(r"[^A-Za-z0-9]+", snake_case)
    return any(_TRANSLATABLE_KEY_TOKEN.fullmatch(token) for token in tokens)


def _strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments only when they are outside JSON strings."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False

    while i < len(text):
        char = text[i]

        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue

        if char == "/" and i + 1 < len(text):
            next_char = text[i + 1]
            if next_char == "/":
                i += 2
                while i < len(text) and text[i] not in "\r\n":
                    i += 1
                continue
            if next_char == "*":
                i += 2
                while i + 1 < len(text) and text[i : i + 2] != "*/":
                    i += 1
                if i + 1 < len(text):
                    i += 2
                continue

        out.append(char)
        i += 1

    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or } only when outside JSON strings."""
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False

    while i < len(text):
        char = text[i]

        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue

        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue

        if char == ",":
            lookahead = i + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead < len(text) and text[lookahead] in "]}":
                i += 1
                continue

        out.append(char)
        i += 1

    return "".join(out)


def load_lenient_json(raw: bytes | str) -> Any:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="ignore")
    else:
        text = raw
    text = _strip_json_comments(text)
    text = _strip_trailing_commas(text)
    return json.loads(text)


def path_to_key(path: tuple) -> str:
    return DocumentPath(path).encode()


def key_to_path(key: str) -> tuple:
    return DocumentPath.decode(key).parts


def set_at_path(data: Any, path: tuple, value: Any) -> None:
    cur = data
    for part in path[:-1]:
        cur = cur[part]
    cur[path[-1]] = value


def iter_translatable_strings(data: Any, path: tuple = ()) -> Iterator[tuple[tuple, str]]:
    """Yield (json_path, string) for book/guide JSON structures."""
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = path + (key,)
            if _is_translatable_json_key(key):
                if isinstance(value, str):
                    yield child_path, value
                elif isinstance(value, list) and all(isinstance(i, str) for i in value):
                    for idx, item in enumerate(value):
                        yield child_path + (idx,), item
                elif isinstance(value, (dict, list)):
                    yield from iter_translatable_strings(value, child_path)
            elif isinstance(value, (dict, list)):
                yield from iter_translatable_strings(value, child_path)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            yield from iter_translatable_strings(item, path + (idx,))


def apply_translations_by_path(data: Any, translations: dict[str, str]) -> None:
    for key, translated in translations.items():
        set_at_path(data, key_to_path(key), translated)
