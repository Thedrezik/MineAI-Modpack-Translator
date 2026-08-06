from mineai.text_processing import is_technical_term, looks_like_source_language


def collect_lang_keys_to_translate(
    en_data: dict,
    tr_data: dict,
    mode: str,
    target_regex: str,
) -> dict[str, str]:
    """Return filtered locale keys that need translation for the selected mode."""
    _ = target_regex  # Kept in the public signature for call-site compatibility.

    pending: dict[str, str] = {}
    for key, value in en_data.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if is_technical_term(value) or not looks_like_source_language(value):
            continue

        existing_value = tr_data.get(key)
        existing = existing_value if isinstance(existing_value, str) else ""
        if mode == "force" or not existing.strip() or existing == value:
            pending[key] = value
    return pending


def count_translatable_lang_entries(en_data: dict) -> int:
    return sum(
        1
        for value in en_data.values()
        if isinstance(value, str)
        and value.strip()
        and looks_like_source_language(value)
        and not is_technical_term(value)
    )
