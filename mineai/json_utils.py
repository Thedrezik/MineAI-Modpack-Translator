import json
import re
from typing import Any, Iterator

from mineai.constants import KEYS_TO_TRANSLATE


def load_lenient_json(raw: bytes | str) -> Any:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="ignore")
    else:
        text = raw
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^\s*//.*$", "", text)
    text = re.sub(r",\s*([\]}])", r"\1", text)
    return json.loads(text)


def path_to_key(path: tuple) -> str:
    return "/".join(str(p) for p in path)


def key_to_path(key: str) -> tuple:
    parts: list = []
    for part in key.split("/"):
        if part.isdigit():
            parts.append(int(part))
        else:
            parts.append(part)
    return tuple(parts)



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
            if key in KEYS_TO_TRANSLATE:
                if isinstance(value, str):
                    yield child_path, value
                elif isinstance(value, list) and all(isinstance(i, str) for i in value):
                    for idx, item in enumerate(value):
                        yield child_path + (idx,), item
            elif isinstance(value, (dict, list)):
                yield from iter_translatable_strings(value, child_path)
    elif isinstance(data, list):
        for idx, item in enumerate(data):
            yield from iter_translatable_strings(item, path + (idx,))


def apply_translations_by_path(data: Any, translations: dict[str, str]) -> None:
    for key, translated in translations.items():
        set_at_path(data, key_to_path(key), translated)
