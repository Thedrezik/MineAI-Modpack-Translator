"""Helpers for validating target-language text without misclassifying Latin scripts."""


_SAME_LATIN_SCRIPT_APIS = frozenset({
    "en",
    "es",
    "de",
    "fr",
    "pt",
    "it",
    "pl",
})


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
