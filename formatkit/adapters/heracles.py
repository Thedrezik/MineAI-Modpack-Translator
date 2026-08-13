"""Lossless adapters for Heracles / Odyssey Quests configuration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from formatkit.adapters.markdown import _basic_validator, _protect_markdown
from formatkit.adapters.xml_text import _MARKUP, _protect as _protect_markup
from formatkit.adapters.xml_text import _validator as _markup_validator
from formatkit.contracts import TranslationPlan, TranslationUnit, ValidationReport


_QUEST_PATH = re.compile(r"(?:^|/)config/heracles/quests/.+\.json$", re.I)
_GROUPS_PATH = re.compile(r"(?:^|/)config/heracles/groups\.txt$", re.I)
_TUTORIAL_PATH = re.compile(r"(?:^|/)config/heracles/tutorial\.html$", re.I)
_TRANSLATION_KEY = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)+$")


@dataclass(frozen=True)
class _StringToken:
    path: tuple[str | int, ...]
    start: int
    end: int
    value: str
    is_key: bool


class _JsonWalker:
    def __init__(self, text: str) -> None:
        self.text = text
        self.index = 0
        self.tokens: list[_StringToken] = []

    def walk(self) -> list[_StringToken]:
        self._value(())
        self._space()
        if self.index != len(self.text):
            raise ValueError("Trailing JSON content")
        return self.tokens

    def _space(self) -> None:
        while self.index < len(self.text) and self.text[self.index].isspace():
            self.index += 1

    def _string(self) -> tuple[int, int, str]:
        self._space()
        start = self.index
        if self.index >= len(self.text) or self.text[self.index] != '"':
            raise ValueError("Expected JSON string")
        self.index += 1
        escaped = False
        while self.index < len(self.text):
            char = self.text[self.index]
            self.index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                raw = self.text[start:self.index]
                return start, self.index, json.loads(raw)
        raise ValueError("Unterminated JSON string")

    def _value(self, path: tuple[str | int, ...]) -> None:
        self._space()
        if self.index >= len(self.text):
            raise ValueError("Missing JSON value")
        char = self.text[self.index]
        if char == '"':
            start, end, value = self._string()
            self.tokens.append(_StringToken(path, start, end, value, False))
            return
        if char == "{":
            self.index += 1
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "}":
                self.index += 1
                return
            while True:
                start, end, key = self._string()
                child = path + (key,)
                self.tokens.append(_StringToken(child, start, end, key, True))
                self._space()
                if self.index >= len(self.text) or self.text[self.index] != ":":
                    raise ValueError("Expected JSON colon")
                self.index += 1
                self._value(child)
                self._space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "}":
                    self.index += 1
                    return
                raise ValueError("Expected JSON object delimiter")
        if char == "[":
            self.index += 1
            self._space()
            if self.index < len(self.text) and self.text[self.index] == "]":
                self.index += 1
                return
            item = 0
            while True:
                self._value(path + (item,))
                item += 1
                self._space()
                if self.index < len(self.text) and self.text[self.index] == ",":
                    self.index += 1
                    continue
                if self.index < len(self.text) and self.text[self.index] == "]":
                    self.index += 1
                    return
                raise ValueError("Expected JSON array delimiter")
        match = re.match(r"(?:-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|true|false|null)", self.text[self.index:])
        if match is None:
            raise ValueError("Invalid JSON value")
        self.index += len(match.group(0))


def _component_paths(value, path: tuple[str | int, ...]) -> set[tuple[str | int, ...]]:
    if isinstance(value, str):
        return {path}
    if isinstance(value, list):
        return {
            selected
            for index, child in enumerate(value)
            for selected in _component_paths(child, path + (index,))
        }
    if not isinstance(value, dict):
        return set()
    result: set[tuple[str | int, ...]] = set()
    for key in ("text", "fallback"):
        if isinstance(value.get(key), str):
            result.add(path + (key,))
    translation = value.get("translate")
    if (
        isinstance(translation, str)
        and not _TRANSLATION_KEY.fullmatch(translation)
    ):
        result.add(path + ("translate",))
    for key in ("extra", "with"):
        if key in value:
            result.update(_component_paths(value[key], path + (key,)))
    if "separator" in value:
        result.update(_component_paths(value["separator"], path + ("separator",)))
    hover = value.get("hoverEvent")
    if isinstance(hover, dict) and hover.get("action") == "show_text":
        result.update(_component_paths(hover.get("contents"), path + ("hoverEvent", "contents")))
    return result


def _element_paths(value, path: tuple[str | int, ...]) -> set[tuple[str | int, ...]]:
    result: set[tuple[str | int, ...]] = set()
    if isinstance(value, dict):
        if isinstance(value.get("type"), str):
            title = value.get("title")
            if isinstance(title, str) and not _TRANSLATION_KEY.fullmatch(title):
                result.add(path + ("title",))
            if "description" in value:
                description = value["description"]
                if not (
                    isinstance(description, str)
                    and _TRANSLATION_KEY.fullmatch(description)
                ):
                    result.update(
                        _component_paths(
                            description,
                            path + ("description",),
                        )
                    )
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                result.update(_element_paths(child, path + (key,)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(_element_paths(child, path + (index,)))
    return result


def _selected_paths(data) -> set[tuple[str | int, ...]]:
    if not isinstance(data, dict):
        return set()
    result: set[tuple[str | int, ...]] = set()
    display = data.get("display")
    if isinstance(display, dict):
        for key in ("title", "subtitle"):
            if key in display:
                result.update(_component_paths(display[key], ("display", key)))
        description = display.get("description")
        if isinstance(description, str):
            result.add(("display", "description"))
        elif isinstance(description, list):
            result.update(
                ("display", "description", index)
                for index, value in enumerate(description)
                if isinstance(value, str)
            )
        groups = display.get("groups")
        if isinstance(groups, dict):
            result.update(("display", "groups", key) for key in groups)
    for key in ("tasks", "rewards"):
        if key in data:
            result.update(_element_paths(data[key], (key,)))
    return result


def _normalized(value, selected, path: tuple[str | int, ...] = ()):
    if path in selected and isinstance(value, str):
        return "<mineai-translatable>"
    if isinstance(value, list):
        return tuple(_normalized(child, selected, path + (index,)) for index, child in enumerate(value))
    if isinstance(value, dict):
        if path == ("display", "groups"):
            return tuple(
                ("<mineai-group>", _normalized(child, selected, path + ("<group>", index)))
                for index, child in enumerate(value.values())
            )
        return tuple((key, _normalized(child, selected, path + (key,))) for key, child in value.items())
    return value


def _json_fingerprint(value) -> str:
    normalized = _normalized(value, _selected_paths(value))
    return hashlib.sha256(repr(normalized).encode("utf-8")).hexdigest()


def _json_validator(source: str, target: str) -> ValidationReport:
    try:
        source_data = json.loads(source)
        target_data = json.loads(target)
    except json.JSONDecodeError as exc:
        return ValidationReport(False, (f"Invalid Heracles JSON: {exc}",))
    source_fingerprint = _json_fingerprint(source_data)
    target_fingerprint = _json_fingerprint(target_data)
    ok = source_fingerprint == target_fingerprint
    return ValidationReport(
        ok,
        () if ok else ("Heracles non-translatable structure changed",),
        source_fingerprint,
        target_fingerprint,
    )


class HeraclesQuestAdapter:
    adapter_id = "heracles-quest-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        return bool(_QUEST_PATH.search(logical_path.replace("\\", "/")))

    def plan(self, logical_path: str, text: str, target_locale: str, *, target_path_hint: str | None = None) -> TranslationPlan:
        del target_locale
        data = json.loads(text)
        selected = _selected_paths(data)
        tokens = _JsonWalker(text).walk()
        units: list[TranslationUnit] = []
        for token in tokens:
            if token.path not in selected:
                continue
            is_group_key = (
                token.is_key
                and len(token.path) == 3
                and token.path[:2] == ("display", "groups")
            )
            if token.is_key and not is_group_key:
                continue
            payload, anchors = _protect_markdown(token.value)
            units.append(
                TranslationUnit(
                    id=f"{self.adapter_id}:{len(units):05d}",
                    payload=payload,
                    start=token.start,
                    end=token.end,
                    context=payload,
                    anchors=anchors,
                    kind=("heracles-group" if is_group_key else "heracles-text"),
                    encoding="json-string",
                )
            )
        return TranslationPlan(self.adapter_id, logical_path, text, target_path_hint or logical_path, tuple(units), _json_validator)


class HeraclesGroupsAdapter:
    adapter_id = "heracles-groups-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        return bool(_GROUPS_PATH.search(logical_path.replace("\\", "/")))

    def plan(self, logical_path: str, text: str, target_locale: str, *, target_path_hint: str | None = None) -> TranslationPlan:
        del target_locale
        units = []
        for match in re.finditer(r"[^\r\n]+", text):
            value = match.group(0)
            if value.strip():
                payload, anchors = _protect_markdown(value)
                units.append(TranslationUnit(f"{self.adapter_id}:{match.start()}:{match.end()}", payload, match.start(), match.end(), payload, anchors, "heracles-group"))
        return TranslationPlan(self.adapter_id, logical_path, text, target_path_hint or logical_path, tuple(units), _basic_validator)


class HeraclesTutorialAdapter:
    adapter_id = "heracles-tutorial-v1"

    def supports(self, logical_path: str, text: str) -> bool:
        del text
        return bool(_TUTORIAL_PATH.search(logical_path.replace("\\", "/")))

    def plan(self, logical_path: str, text: str, target_locale: str, *, target_path_hint: str | None = None) -> TranslationPlan:
        del target_locale
        units = []
        cursor = 0
        for markup in list(_MARKUP.finditer(text)) + [None]:
            boundary = markup.start() if markup is not None else len(text)
            segment = text[cursor:boundary]
            leading = len(segment) - len(segment.lstrip())
            trailing = len(segment) - len(segment.rstrip())
            start, end = cursor + leading, boundary - trailing
            value = text[start:end]
            if value and re.search(r"[^\W\d_]", value):
                payload, anchors = _protect_markup(value)
                units.append(TranslationUnit(f"{self.adapter_id}:{start}:{end}", payload, start, end, payload, anchors, "heracles-text"))
            cursor = markup.end() if markup is not None else len(text)
        return TranslationPlan(self.adapter_id, logical_path, text, target_path_hint or logical_path, tuple(units), _markup_validator)
