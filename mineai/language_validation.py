"""Helpers for validating target-language text without misclassifying Latin scripts."""

import re

from mineai.constants import IGNORE_TERMS


_SAME_LATIN_SCRIPT_APIS = frozenset({
    "en",
    "es",
    "de",
    "fr",
    "pt",
    "it",
    "pl",
})
_ENGLISH_CLAUSE_WORDS = frozenset({
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "have",
    "if",
    "in",
    "into",
    "is",
    "it",
    "not",
    "of",
    "on",
    "or",
    "that",
    "the",
    "their",
    "this",
    "to",
    "was",
    "when",
    "which",
    "will",
    "with",
    "you",
    "your",
})
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_PATCHOULI_TOOLTIP = re.compile(r"\$\(t:([^\r\n)]*)\)", re.IGNORECASE)
_MC_FORMAT_CODE = re.compile(
    r"(?:[&§][0-9a-fk-orlmn]|⟦FK\d{4}⟧)",
    re.IGNORECASE,
)
_JSON_COMPONENT_FIELD = re.compile(
    r'\\?"(?:clickEvent|hoverEvent|action|value|color|underlined|italic)"\s*:',
    re.IGNORECASE,
)
_TRANSLATION_ACTION_WORDS = frozenset(
    {
        "activate",
        "bake",
        "build",
        "craft",
        "create",
        "enter",
        "feel",
        "find",
        "get",
        "keep",
        "made",
        "make",
        "no",
        "obtained",
        "place",
        "put",
        "start",
        "use",
        "with",
        "hello",
        "welcome",
    }
)
# These words are also mod/control names when title-cased, but their lowercase
# spelling is ordinary English prose.  Keep the distinction in the validator;
# the masking layer still preserves the original spelling losslessly.
_CASE_SENSITIVE_PROTECTED_TERMS = frozenset({"Create", "Shift"})
_DELIMITER_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSING_DELIMITERS = {closing: opening for opening, closing in _DELIMITER_PAIRS.items()}


def _has_unbalanced_delimiters(text: str) -> bool:
    """Return whether prose delimiters have an unmatched or crossed pair.

    A translation is allowed to add natural punctuation such as ``(full block)``.
    What must never reach a pack is a dangling/crossed delimiter (for example the
    ``] ]`` tail produced by an LLM when it mistakes the surrounding JSON list for
    translatable text).  Escaped delimiters are literal text and are ignored.
    """
    stack: list[str] = []
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in _DELIMITER_PAIRS:
            stack.append(char)
            continue
        opening = _CLOSING_DELIMITERS.get(char)
        if opening is None:
            continue
        if not stack or stack[-1] != opening:
            return True
        stack.pop()
    return bool(stack)


def requires_target_script_marker(target_lang: dict) -> bool:
    """Return True when the target language uses a script distinct from English.

    For same-script Latin targets a regex made only of diacritics is not a reliable
    language detector: valid translations such as ``Espada de hierro`` may contain
    no target-specific accented characters at all. Unknown languages stay strict by
    default so extending the language table cannot silently weaken validation.
    """
    return target_lang.get("api") not in _SAME_LATIN_SCRIPT_APIS


def uses_same_latin_script(target_lang: dict) -> bool:
    return not requires_target_script_marker(target_lang)


def has_untranslated_leading_article(
    source: str,
    candidate: str,
    target_lang: dict,
) -> bool:
    """Detect an English article left before otherwise translated text."""
    if not requires_target_script_marker(target_lang):
        return False
    if _JSON_COMPONENT_FIELD.search(source):
        return False
    target_pattern = target_lang.get("regex")
    if not target_pattern or not re.search(target_pattern, candidate):
        return False
    source_match = re.match(r"^\s*(the|an|a)\b", source, flags=re.IGNORECASE)
    candidate_match = re.match(
        r"^\s*(the|an|a)\b",
        candidate,
        flags=re.IGNORECASE,
    )
    return bool(
        source_match
        and candidate_match
        and source_match.group(1).casefold()
        == candidate_match.group(1).casefold()
    )


def has_long_untranslated_english_fragment(
    candidate: str,
    target_lang: dict,
) -> bool:
    """Detect a copied English clause inside a non-Latin translation."""
    if not requires_target_script_marker(target_lang):
        return False
    target_pattern = target_lang.get("regex")
    if not target_pattern or not re.search(target_pattern, candidate):
        return False

    for segment in re.split(r"[^\x00-\x7f]+", candidate):
        words = [word.casefold() for word in _LATIN_WORD.findall(segment)]
        if len(words) < 4:
            continue
        if sum(word in _ENGLISH_CLAUSE_WORDS for word in words) >= 1:
            return True
    return False


def has_untranslated_patchouli_tooltip(
    candidate: str,
    target_lang: dict,
) -> bool:
    """Detect English prose hidden inside a Patchouli tooltip tag."""
    if not requires_target_script_marker(target_lang):
        return False
    target_pattern = target_lang.get("regex")
    if not target_pattern:
        return False
    for tooltip in _PATCHOULI_TOOLTIP.findall(candidate):
        words = [word.casefold() for word in _LATIN_WORD.findall(tooltip)]
        if re.search(target_pattern, tooltip) or len(words) < 2:
            continue
        common_count = sum(word in _ENGLISH_CLAUSE_WORDS for word in words)
        if common_count or len(words) >= 4:
            return True
    return False


def _format_code_boundaries(text: str) -> tuple[tuple[str, bool, bool], ...]:
    """Return each format code and whether it touches whitespace on either side.

    Minecraft formatting codes are zero-width control tokens.  A translator may
    change the words around a token, but it must not turn ``&6Extras`` into
    ``&6 Extras`` (or merge ``&r`` with the preceding word).  Keeping the
    boundary flags separate from the visible text lets us detect this without
    comparing the translated wording.
    """
    result: list[tuple[str, bool, bool]] = []
    for match in _MC_FORMAT_CODE.finditer(text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        result.append((match.group(0).casefold(), before.isspace(), after.isspace()))
    return tuple(result)


def formatting_boundaries_need_repair(source: str, candidate: str) -> bool:
    """Return True when colour/style codes have different adjacent spacing."""
    source_codes = _format_code_boundaries(source)
    candidate_codes = _format_code_boundaries(candidate)
    if source_codes != candidate_codes and len(source_codes) == len(candidate_codes):
        # The full code sequence is validated separately.  This branch only
        # reports a useful reason when the code text itself already matches but
        # one of its whitespace boundaries was changed.
        source_tokens = tuple(code for code, _before, _after in source_codes)
        candidate_tokens = tuple(code for code, _before, _after in candidate_codes)
        if source_tokens == candidate_tokens:
            for source_code, candidate_code in zip(
                source_codes,
                candidate_codes,
                strict=True,
            ):
                # A leading English article can be omitted, so a code may
                # legitimately move to offset zero.  The after-code boundary
                # remains lossless: `&6Name` and `&6 Name` render differently.
                if source_code[2] != candidate_code[2]:
                    return True
                if (
                    source_code[1] != candidate_code[1]
                    and candidate_code[1]
                    and source_code[1]
                ):
                    return True
            return False
    return False


def delimiter_counts_need_repair(source: str, candidate: str) -> bool:
    """Return True when translation leaves prose delimiters unbalanced.

    FTB Quests descriptions are stored as list elements, while older model
    prompts sometimes returned fragments such as ``] ]`` from the surrounding
    JSON/SNBT list.  The adapter owns the list brackets, so a text node may add
    balanced punctuation but may not leave a dangling or crossed delimiter.
    """
    del source  # Kept in the public signature for source/candidate validators.
    return _has_unbalanced_delimiters(candidate)


def has_untranslated_source_words(
    source: str,
    candidate: str,
    target_lang: dict,
) -> bool:
    """Detect meaningful source words copied into a distinct-script result.

    Product/mod names are removed using the same protected-term list as the
    translator.  A remaining word is considered a defect when it occurs in a
    real sentence (or when the source is an imperative such as ``Activate``),
    while a standalone proper name is left alone.
    """
    if not requires_target_script_marker(target_lang):
        return False
    target_pattern = target_lang.get("regex")
    if not target_pattern or not re.search(target_pattern, candidate):
        return False

    def visible_words(value: str) -> list[tuple[str, str]]:
        text = _MC_FORMAT_CODE.sub(" ", value)
        for term in IGNORE_TERMS:
            flags = (
                0
                if term in _CASE_SENSITIVE_PROTECTED_TERMS
                else re.IGNORECASE
            )
            text = re.sub(
                r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])",
                " ",
                text,
                flags=flags,
            )
        return [
            (word.casefold(), word)
            for word in _LATIN_WORD.findall(text)
            if len(word) > 1
        ]

    source_words = visible_words(source)
    candidate_words = {word for word, _original in visible_words(candidate)}
    if not source_words:
        return False
    residual = [
        (word, original)
        for word, original in source_words
        if word in candidate_words
    ]
    if not residual:
        return False

    # A lowercase action word is prose even when it is surrounded by a valid
    # Russian sentence.  Keep this check on the residual words themselves so a
    # protected/technical term elsewhere in the source cannot trigger a false
    # rejection.
    residual_words = {word for word, _original in residual}
    if residual_words & (_ENGLISH_CLAUSE_WORDS | _TRANSLATION_ACTION_WORDS):
        return True

    def looks_like_name(original: str) -> bool:
        # All-caps abbreviations (ME, UI), internal capitals (AdvancedAE,
        # GitHub), and ordinary title-case words are common in mod names.  A
        # single simple title-case word remains conservative below so ``Blaze``
        # in a copied sentence is still retried.
        return (
            original.isupper()
            or any(char.isupper() for char in original[1:])
            or original[:1].isupper()
        )

    if all(looks_like_name(original) for _word, original in residual):
        if len(residual) >= 2 or any(
            any(char.isupper() for char in original[1:])
            or original.isupper()
            for _word, original in residual
        ):
            return False

    # Preserve the strict single-word guard used for residual names such as
    # ``Blaze`` while allowing ordinary translated text around them.
    return (
        len(source_words) >= 2
        and len(residual) >= 1
    )


def translation_needs_repair(
    source: str,
    candidate: str,
    target_lang: dict,
) -> bool:
    """Return True when an existing localized value is empty or incomplete."""
    if not candidate.strip() or candidate.strip() == source.strip():
        return True
    # Cache entries must satisfy the same lossless invariants as fresh engine
    # responses.  This catches old cross-row answers, dropped links and changed
    # numeric values before they can be reused as a completed translation.
    from mineai.text_processing import (
        count_line_breaks,
        numeric_fragments,
        structural_fragments,
    )

    if count_line_breaks(source) != count_line_breaks(candidate):
        return True
    if numeric_fragments(source) != numeric_fragments(candidate):
        return True
    if structural_fragments(source) != structural_fragments(candidate):
        return True
    if delimiter_counts_need_repair(source, candidate):
        return True
    if formatting_boundaries_need_repair(source, candidate):
        return True
    if not requires_target_script_marker(target_lang):
        return False
    target_pattern = target_lang.get("regex")
    if (
        re.search(r"[A-Za-z]", source)
        and target_pattern
        and not re.search(target_pattern, candidate)
    ):
        return True
    if has_untranslated_source_words(source, candidate, target_lang):
        return True
    return (
        has_long_untranslated_english_fragment(candidate, target_lang)
        or has_untranslated_patchouli_tooltip(candidate, target_lang)
    )
