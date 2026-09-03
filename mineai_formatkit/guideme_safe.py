from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .core import ProtectedFragment
from .guideme import GuideMeMarkdownAdapter as _BaseGuideMeMarkdownAdapter

# Only paired star emphasis is enabled here. The live corpus proved these two
# forms. Underscore emphasis is intentionally not guessed because resource IDs
# and JSX attributes in the same guide contain many underscores.
_STRONG_STAR_RE = re.compile(r"(?<!\\)\*\*(?=\S)[^\n*]+?(?<=\S)(?<!\\)\*\*")
_EM_STAR_RE = re.compile(r"(?<![\\*])\*(?!\*)(?=\S)[^\n*]+?(?<=\S)(?<!\\)\*(?!\*)")
_PLACEHOLDER_RE = re.compile(r"\[#(\d+)#\]")
_DATA_DRIVEN_GUIDE_RE = re.compile(
    r"(^|/)(assets/[^/]+/guides/[^/]+/[^/]+/)(.+\.md)$",
    re.IGNORECASE,
)
_LOCALE_PREFIX_RE = re.compile(r"^_[a-z]{2}_[a-z]{2}/", re.IGNORECASE)


@dataclass(frozen=True)
class GuideMeSafeFingerprint:
    base: object
    star_emphasis_tokens: tuple[tuple[str, int], ...]


class GuideMeMarkdownAdapter(_BaseGuideMeMarkdownAdapter):
    """GuideME parser with paired star-emphasis structural protection."""

    name = "guideme-markdown"

    def _protect(self, text: str) -> tuple[str, tuple[ProtectedFragment, ...]]:
        masked, protected = super()._protect(text)
        spans = self._star_emphasis_spans(masked)
        if not spans:
            return masked, protected

        placeholder_ids = [int(match.group(1)) for match in _PLACEHOLDER_RE.finditer(masked)]
        next_id = max(placeholder_ids) + 1 if placeholder_ids else 0
        out: list[str] = []
        extra: list[ProtectedFragment] = []
        cursor = 0
        for offset, (start, end) in enumerate(spans):
            out.append(masked[cursor:start])
            placeholder = f"[#{next_id + offset}#]"
            out.append(placeholder)
            extra.append(ProtectedFragment(placeholder, masked[start:end]))
            cursor = end
        out.append(masked[cursor:])
        return "".join(out), protected + tuple(extra)

    def fingerprint(self, text: str) -> GuideMeSafeFingerprint:
        base = _BaseGuideMeMarkdownAdapter.fingerprint(self, text)
        # Validation is deliberately stricter than extraction: paired star
        # markers anywhere in the immutable source skeleton are counted.
        # Technical fragments are already span-protected by the base adapter,
        # so they cannot be legitimately rewritten during apply().
        markers = [text[start:end] for start, end in self._star_emphasis_spans(text)]
        return GuideMeSafeFingerprint(
            base=base,
            star_emphasis_tokens=tuple(sorted(Counter(markers).items())),
        )

    @staticmethod
    def _star_emphasis_spans(text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        occupied: list[tuple[int, int]] = []
        for regex, width in ((_STRONG_STAR_RE, 2), (_EM_STAR_RE, 1)):
            for match in regex.finditer(text):
                if any(match.start() < end and match.end() > start for start, end in occupied):
                    continue
                occupied.append((match.start(), match.end()))
                spans.append((match.start(), match.start() + width))
                spans.append((match.end() - width, match.end()))
        return _BaseGuideMeMarkdownAdapter._merge_spans(spans)


class DataDrivenGuideMeMarkdownAdapter(GuideMeMarkdownAdapter):
    """Safe GuideME Markdown for data-driven ``guides/<id>/`` trees."""

    name = "guideme-data-driven-markdown"

    def matches(self, path: str) -> bool:
        slash = "/" + path.replace("\\", "/").lstrip("/")
        match = _DATA_DRIVEN_GUIDE_RE.search(slash)
        if not match:
            return False
        return not _LOCALE_PREFIX_RE.match(match.group(3))

    def target_path(self, path: str, target_code: str) -> str:
        slash = path.replace("\\", "/")
        normalized = "/" + slash.lstrip("/")
        match = _DATA_DRIVEN_GUIDE_RE.search(normalized)
        if not match or _LOCALE_PREFIX_RE.match(match.group(3)):
            raise ValueError(f"Unsupported data-driven GuideME source path: {path}")
        root = match.group(2)
        suffix = match.group(3)
        prefix_len = 1 if not slash.startswith("/") else 0
        root_start = match.start(2) - prefix_len
        return slash[:root_start] + root + f"_{target_code}/" + suffix
