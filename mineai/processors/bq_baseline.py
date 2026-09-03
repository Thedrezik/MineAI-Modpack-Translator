"""Hash-verified BetterQuesting baselines for safe in-place Force mode."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import shutil

from mineai.io_utils import atomic_write_text


_STATE_VERSION = 1
_STATE_SUFFIX = ".bak.mineai"
_TRANSLATABLE_SENTINEL = "<mineai-translatable>"


@dataclass(frozen=True)
class BQForceBaselineDecision:
    source_path: str
    backup_path: str
    refresh_backup: bool
    stale_backup_path: str | None
    reason: str


def _state_path(file_path: str) -> str:
    return file_path + _STATE_SUFFIX


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_state(file_path: str) -> dict[str, str | int] | None:
    try:
        with open(_state_path(file_path), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(state, dict) or state.get("version") != _STATE_VERSION:
        return None
    baseline_hash = state.get("baseline_sha256")
    output_hash = state.get("output_sha256")
    if not isinstance(baseline_hash, str) or not isinstance(output_hash, str):
        return None
    return state


def _load_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _without_bq_translatable_values(data: dict) -> dict:
    """Return a copy where only BQ name/desc payloads are neutralized."""
    normalized = deepcopy(data)
    properties_key = next(
        (key for key in normalized if key.startswith("properties")),
        None,
    )
    if not properties_key or not isinstance(normalized.get(properties_key), dict):
        return normalized

    properties = normalized[properties_key]
    bq_key = next(
        (key for key in properties if key.startswith("betterquesting")),
        None,
    )
    if not bq_key or not isinstance(properties.get(bq_key), dict):
        return normalized

    bq_data = properties[bq_key]
    for key in list(bq_data):
        if key.startswith("name") or key.startswith("desc"):
            bq_data[key] = _TRANSLATABLE_SENTINEL
    return normalized


def _legacy_backup_has_same_structure(file_path: str, backup: str) -> bool:
    """Recognize old MineAI backups when only translated fields differ."""
    current = _load_json(file_path)
    baseline = _load_json(backup)
    if current is None or baseline is None:
        return False
    return (
        _without_bq_translatable_values(current)
        == _without_bq_translatable_values(baseline)
    )


def baseline_tracks_current(file_path: str) -> bool:
    """Return True only when the current BQ file is tied to its backup safely."""
    backup = file_path + ".bak"
    if not os.path.exists(backup):
        return False

    try:
        current_hash = _sha256_file(file_path)
        backup_hash = _sha256_file(backup)
    except OSError:
        return False
    if current_hash == backup_hash:
        return True

    state = _load_state(file_path)
    if (
        state
        and state["baseline_sha256"] == backup_hash
        and state["output_sha256"] == current_hash
    ):
        return True

    # Compatibility for backups created before hash-state existed. Trust them
    # only when all non-translatable JSON structure still matches exactly.
    return state is None and _legacy_backup_has_same_structure(file_path, backup)


def resolve_bq_force_baseline(file_path: str) -> BQForceBaselineDecision:
    """Choose a Force source without blindly trusting an existing ``.bak``."""
    backup = file_path + ".bak"
    if not os.path.exists(backup):
        return BQForceBaselineDecision(
            source_path=file_path,
            backup_path=backup,
            refresh_backup=True,
            stale_backup_path=None,
            reason="backup missing",
        )

    current_hash = _sha256_file(file_path)
    backup_hash = _sha256_file(backup)
    if current_hash == backup_hash:
        return BQForceBaselineDecision(
            source_path=backup,
            backup_path=backup,
            refresh_backup=False,
            stale_backup_path=None,
            reason="backup matches current source",
        )

    state = _load_state(file_path)
    if (
        state
        and state["baseline_sha256"] == backup_hash
        and state["output_sha256"] == current_hash
    ):
        return BQForceBaselineDecision(
            source_path=backup,
            backup_path=backup,
            refresh_backup=False,
            stale_backup_path=None,
            reason="backup matches recorded MineAI baseline",
        )

    if state is None and _legacy_backup_has_same_structure(file_path, backup):
        return BQForceBaselineDecision(
            source_path=backup,
            backup_path=backup,
            refresh_backup=False,
            stale_backup_path=None,
            reason="legacy backup matches current JSON structure",
        )

    return BQForceBaselineDecision(
        source_path=file_path,
        backup_path=backup,
        refresh_backup=True,
        stale_backup_path=f"{backup}.stale-{backup_hash[:12]}",
        reason="existing backup cannot be proven current",
    )


def refresh_bq_force_baseline(
    file_path: str,
    decision: BQForceBaselineDecision,
) -> None:
    """Preserve an untrusted backup, then refresh the active baseline."""
    if not decision.refresh_backup:
        return

    if os.path.exists(decision.backup_path) and decision.stale_backup_path:
        if not os.path.exists(decision.stale_backup_path):
            shutil.copy2(decision.backup_path, decision.stale_backup_path)

    shutil.copy2(file_path, decision.backup_path)
    try:
        os.remove(_state_path(file_path))
    except FileNotFoundError:
        pass


def write_bq_baseline_state(file_path: str) -> None:
    """Record which output was produced from the active ``.bak`` baseline."""
    backup = file_path + ".bak"
    if not os.path.exists(backup):
        raise FileNotFoundError(backup)

    state = {
        "version": _STATE_VERSION,
        "baseline_sha256": _sha256_file(backup),
        "output_sha256": _sha256_file(file_path),
    }
    atomic_write_text(
        _state_path(file_path),
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
    )
