"""Helpers for validating target-language text without misclassifying Latin scripts."""

import re


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


def translation_needs_repair(
    source: str,
    candidate: str,
    target_lang: dict,
) -> bool:
    """Return True when an existing localized value is empty or incomplete."""
    if not candidate.strip() or candidate.strip() == source.strip():
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
    return (
        has_long_untranslated_english_fragment(candidate, target_lang)
        or has_untranslated_patchouli_tooltip(candidate, target_lang)
    )
