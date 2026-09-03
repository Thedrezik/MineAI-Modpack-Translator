from __future__ import annotations

import re

from .core import ProtectedFragment
from .minecraft_lang import MinecraftLangJsonAdapter


_COLLAPSIBLE_GROUPS_PATH_RE = re.compile(
    r"(^|/)assets/collapsible_groups/group_lang/en_us\.json$",
    re.IGNORECASE,
)
_CRASH_ASSISTANT_PATH_RE = re.compile(
    r"(^|/)crash_assistant_localization/en_us\.json$",
    re.IGNORECASE,
)

# CrashAssistant uses these as runtime syntax inside otherwise-normal locale strings.
_CRASH_MACRO_RE = re.compile(r"\$[A-Za-z0-9_.:-]+\$")
_HTML_TAG_RE = re.compile(r"<[^>\r\n]+>")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)

# Keep this protection set aligned with the base Minecraft locale adapter without
# changing that adapter's behavior in this corpus-expansion PR.
_FORMAT_RE = re.compile(
    r"%(?!\s)(?:(?:\d+\$)?[-+#0 ,(<]*\d*(?:\.\d+)?"
    r"(?:[bBhHsScCdoxXeEfgGaA]|[tT][HIklMSLNpzZsQBbhAaCYyjmdeRTrDFc])|[%n])"
)
_MINECRAFT_FORMAT_RE = re.compile(r"§[0-9A-FK-ORa-fk-or]")
_MESSAGE_FORMAT_RE = re.compile(r"\{\d+(?:,[^{}]+)?\}")
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


class CollapsibleGroupsLangJsonAdapter(MinecraftLangJsonAdapter):
    """Locale adapter for Collapsible Groups' ``group_lang`` runtime catalog."""

    name = "collapsible-groups-lang-json"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_COLLAPSIBLE_GROUPS_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if not self.matches(slash):
            raise ValueError(f"Unsupported Collapsible Groups locale source path: {path}")
        return re.sub(r"en_us\.json$", f"{target_code}.json", slash, flags=re.IGNORECASE)


class CrashAssistantLocalizationAdapter(MinecraftLangJsonAdapter):
    """Flat CrashAssistant locale catalog with runtime syntax protection."""

    name = "crash-assistant-localization"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        return bool(_CRASH_ASSISTANT_PATH_RE.search(slash))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        if not self.matches(slash):
            raise ValueError(f"Unsupported CrashAssistant locale source path: {path}")
        return re.sub(r"en_us\.json$", f"{target_code}.json", slash, flags=re.IGNORECASE)

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        spans: list[tuple[int, int]] = []
        literal_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(text)]
        spans.extend((match.start(), match.end()) for match in _PLACEHOLDER_RE.finditer(text))
        for regex in (
            _FORMAT_RE,
            _MINECRAFT_FORMAT_RE,
            _MESSAGE_FORMAT_RE,
            _LINE_BREAK_RE,
            _CRASH_MACRO_RE,
            _HTML_TAG_RE,
            _URL_RE,
        ):
            spans.extend((match.start(), match.end()) for match in regex.finditer(text))

        merged = self._merge_spans(spans)
        protected: list[ProtectedFragment] = []
        out: list[str] = []
        cursor = 0
        base_id = (max(literal_ids) + 1) if literal_ids else 0
        for offset, (start, end) in enumerate(merged):
            out.append(text[cursor:start])
            placeholder = f"[#{base_id + offset}#]"
            out.append(placeholder)
            protected.append(ProtectedFragment(placeholder, text[start:end]))
            cursor = end
        out.append(text[cursor:])
        return "".join(out), tuple(protected)
