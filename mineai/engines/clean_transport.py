"""Lossless transport of visible text nodes to translation engines.

The format adapters and validators keep the original document as the source of
truth.  This module only builds the small, ordered payload sent to an LLM and
puts the returned text back into the already-masked template.  Protected game
syntax therefore never needs to be interpreted by the model.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from mineai.text_processing import (
    NUMERIC_FRAGMENT_PATTERN,
    PLACEHOLDER_PATTERN,
    mask_protected_fragments,
)


# Bare URLs are not necessarily inside a Markdown link.  Treat them as
# protected transport spans as well; the original value remains in the template.
URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", flags=re.IGNORECASE)
TRANSPORT_NUMERIC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?\d+(?:[.,]\d+)?\s*[x×](?![A-Za-z0-9_])"
    r"|(?<![A-Za-z0-9_])[-+]?\d+(?:[\u00a0 ,._:/-]\d+)*(?:\s?%)?"
    r"(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
ALPHANUMERIC_NUMERIC_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z_]*\d[A-Za-z0-9_.:/+_-]*(?![A-Za-z0-9_])"
)


@dataclass(frozen=True)
class VisibleTextNode:
    """A visible fragment and its exact location in the masked template."""

    start: int
    end: int
    leading: str
    trailing: str
    text: str


def _has_letter(value: str) -> bool:
    # ``[^\Wd_]`` means a Unicode letter in Python's default Unicode mode.
    return bool(re.search(r"[^\W\d_]", value, flags=re.UNICODE))


def _add_node(nodes: list[VisibleTextNode], value: str, start: int, end: int) -> None:
    if not value or not value.strip():
        return
    leading = value[: len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()) :]
    text = value.strip()
    # Punctuation-only and whitespace-only fragments are already complete in
    # the template.  In particular, a standalone ``.`` must never be sent as
    # a translation request.
    if not text or not _has_letter(text):
        return
    nodes.append(
        VisibleTextNode(
            start=start,
            end=end,
            leading=leading,
            trailing=trailing,
            text=text,
        )
    )


def _is_numeric_protected_fragment(value: str) -> bool:
    """Return whether a protected token is a number or a Roman level marker."""

    value = value.strip()
    return bool(
        NUMERIC_FRAGMENT_PATTERN.fullmatch(value)
        or re.fullmatch(r"(?i)[IVXLCDM]{1,8}", value)
    )


def numeric_placeholder_tokens(mapping: dict[str, str]) -> frozenset[str]:
    """Return placeholders that represent values kept out of the LLM payload."""

    return frozenset(
        token
        for token, fragment in mapping.items()
        if _is_numeric_protected_fragment(fragment)
    )


def _coalesce_numeric_nodes(
    masked: str,
    nodes: list[VisibleTextNode],
    numeric_tokens: frozenset[str],
) -> list[VisibleTextNode]:
    """Join prose split only by numeric/level placeholders.

    A sentence such as ``Haste II for 2 minutes`` would otherwise become three
    independent requests (``Haste``, ``for``, ``minutes``).  Translating those
    pieces independently changes word order.  We keep the numeric placeholders
    in one unit, so the model sees the sentence context while the original
    values are still restored byte-for-byte afterwards.
    """

    if len(nodes) < 2 or not numeric_tokens:
        return nodes

    groups: list[list[VisibleTextNode]] = []
    current: list[VisibleTextNode] = [nodes[0]]
    protected_count = 0

    def flush() -> None:
        nonlocal current, protected_count
        if protected_count >= 2:
            first, last = current[0], current[-1]
            groups.append(
                [
                    VisibleTextNode(
                        start=first.start,
                        end=last.end,
                        leading=first.leading,
                        trailing=last.trailing,
                        text=masked[first.start:last.end].strip(),
                    )
                ]
            )
        else:
            groups.append(current)
        current = []
        protected_count = 0

    for node in nodes[1:]:
        previous = current[-1]
        gap = masked[previous.end : node.start]
        placeholders = list(PLACEHOLDER_PATTERN.finditer(gap))
        gap_without_tokens = PLACEHOLDER_PATTERN.sub("", gap)
        only_numeric_tokens = bool(placeholders) and all(
            match.group(0) in numeric_tokens for match in placeholders
        ) and not gap_without_tokens.strip()
        if only_numeric_tokens:
            current.append(node)
            protected_count += len(placeholders)
            continue
        flush()
        current = [node]

    if current:
        flush()

    flattened = [node for group in groups for node in group]
    return flattened


def extract_visible_nodes(
    masked: str,
    *,
    coalesce_numeric: frozenset[str] | None = None,
) -> tuple[VisibleTextNode, ...]:
    """Return visible prose spans while leaving protected syntax in place.

    The spans include placeholders produced by ``mask_protected_fragments``,
    bare URLs and numeric fragments.  Only fragments containing at least one
    letter are returned, so a number or punctuation mark cannot be translated
    accidentally.
    """

    protected = list(PLACEHOLDER_PATTERN.finditer(masked))
    protected.extend(URL_PATTERN.finditer(masked))
    protected.extend(NUMERIC_FRAGMENT_PATTERN.finditer(masked))
    protected.extend(TRANSPORT_NUMERIC_PATTERN.finditer(masked))
    protected.extend(ALPHANUMERIC_NUMERIC_PATTERN.finditer(masked))
    protected.sort(key=lambda match: (match.start(), match.end()))

    nodes: list[VisibleTextNode] = []
    cursor = 0
    for match in protected:
        start, end = match.span()
        if start < cursor:
            # A numeric span can be inside a URL or an existing placeholder.
            continue
        _add_node(nodes, masked[cursor:start], cursor, start)
        cursor = end
    _add_node(nodes, masked[cursor:], cursor, len(masked))
    if coalesce_numeric:
        nodes = _coalesce_numeric_nodes(masked, nodes, coalesce_numeric)
    return tuple(nodes)


def sanitize_prompt_context(context: str) -> str:
    """Keep only human-readable context words used to guide an LLM.

    Context is instructional metadata, not translation payload.  Removing
    paths, locators, URLs, numeric fragments and formatting here prevents a
    file name or adapter ID from re-entering a request through ``{context}``.
    """

    masked, _mapping = mask_protected_fragments(str(context or ""))
    visible = " ".join(node.text for node in extract_visible_nodes(masked))
    words = re.findall(r"[^\W\d_]+", visible, flags=re.UNICODE)
    return " ".join(words)


def rebuild_masked(
    masked: str,
    nodes: tuple[VisibleTextNode, ...],
    translations: list[str],
) -> str:
    """Replace node payloads in reverse order without moving any structure."""

    if len(nodes) != len(translations):
        raise ValueError(
            f"visible node count mismatch: expected {len(nodes)}, "
            f"received {len(translations)}"
        )
    result = masked
    for node, translated in reversed(tuple(zip(nodes, translations))):
        value = str(translated).strip()
        result = (
            result[: node.start]
            + node.leading
            + value
            + node.trailing
            + result[node.end :]
        )
    return result


_TRANSPORT_SYNTAX_PATTERN = re.compile(
    r"(?:\[\s*#\s*\d+\s*#\s*\]|"
    r"https?://|"
    r"\$\([^)]*\)|"
    r"[&§][0-9a-fk-orlmn]|"
    r"<[^>\r\n]+>|"
    r"!?(?:\[[^\]\r\n]*\]\([^)]*\))|"
    r"`{1,3}|"
    r"[-+]?\d+(?:[.,]\d+)?"
    r")",
    flags=re.IGNORECASE,
)


def response_is_clean(
    value: str,
    *,
    allowed_placeholders: frozenset[str] | set[str] | None = None,
) -> bool:
    """Reject a model response that tries to reintroduce protected syntax."""

    if not value:
        return False
    check = value
    for placeholder in allowed_placeholders or ():
        check = check.replace(placeholder, "")
    return not _TRANSPORT_SYNTAX_PATTERN.search(check)
