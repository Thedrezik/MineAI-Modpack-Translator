"""Pure helpers for the Qt presentation layer.

This module deliberately imports no Qt symbols so it can be unit-tested in the
existing test suite even when the optional Qt dependency is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from pathlib import Path

from mineai.gui_qt.i18n_runtime import tr


COMPACT_DASHBOARD_WIDTH = 1420


def dashboard_columns(window_width: int) -> int:
    """Return a safe dashboard column count for the current top-level width."""
    return 2 if int(window_width) < COMPACT_DASHBOARD_WIDTH else 4


def detected_source_roots(mc_dir: str | Path) -> tuple[str, ...]:
    """Return supported top-level source folders that actually exist."""
    root = Path(mc_dir)
    return tuple(name for name in ("mods", "config", "kubejs", "defaultconfigs") if (root / name).is_dir())


ENGINE_OPTIONS = {
    "Google": ("google", "local"),
    "DeepL": ("deepl", "local"),
    "Локальный ИИ": ("ai", "local"),
    "Local AI": ("ai", "local"),
    "OpenRouter": ("ai", "openrouter"),
    "LM Studio": ("ai", "lmstudio"),
}


@dataclass(frozen=True)
class DashboardStats:
    processed: int
    total: int
    successful: int
    failed: int
    percent: float
    success_percent: float
    error_percent: float
    elapsed_seconds: float
    lines_per_minute: float
    eta_text: str
    remaining_lines: int


def compact_runtime_status(text: str) -> str:
    """Remove dashboard metrics from JobState.get_full_status() text.

    The same counters already exist in the KPI cards. Keeping only an engine/status
    fragment prevents the current-task card from duplicating the whole dashboard.
    Ordinary status messages (start/stop/errors) are returned unchanged.
    """
    value = str(text or "").strip()
    if "Осталось:" not in value or " | " not in value:
        return value

    ignored_prefixes = ("Переведено:", "Обработано:", "Ошибки:", "Осталось:")
    compact: list[str] = []
    for part in value.split(" | "):
        part = re.sub(r"^\[[^]]+\]\s*", "", part.strip())
        if not part or part.startswith(ignored_prefixes):
            continue
        compact.append(part)
    return " | ".join(compact)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def stats_from_snapshot(snapshot, *, now: float | None = None, eta_text: str = "—") -> DashboardStats:
    total = max(0, int(snapshot.total_strings))
    processed = max(0, int(snapshot.translated_strings))
    if total:
        processed = min(processed, total)
    successful = max(0, int(snapshot.ok_strings))
    failed = max(0, int(snapshot.failed_strings))
    denominator = processed if processed > 0 else 0
    percent = (processed / total * 100.0) if total else 0.0
    success_percent = (successful / denominator * 100.0) if denominator else 0.0
    error_percent = (failed / denominator * 100.0) if denominator else 0.0
    current_time = time.time() if now is None else now
    if snapshot.start_time:
        elapsed = max(0.0, current_time - snapshot.start_time)
    else:
        elapsed = 0.0
    rate = processed / elapsed * 60.0 if elapsed > 0 and processed > 0 else 0.0
    remaining = max(0, total - processed)
    return DashboardStats(
        processed=processed,
        total=total,
        successful=successful,
        failed=failed,
        percent=min(percent, 100.0),
        success_percent=min(success_percent, 100.0),
        error_percent=min(error_percent, 100.0),
        elapsed_seconds=elapsed,
        lines_per_minute=rate,
        eta_text=eta_text,
        remaining_lines=remaining,
    )


def engine_readiness(config, engine_label: str) -> tuple[bool, str]:
    engine, provider = ENGINE_OPTIONS.get(engine_label, ("", ""))
    if engine == "google":
        return True, tr("ready.google")
    if engine == "deepl":
        return (
            (True, tr("ready.deepl"))
            if config.get("API", "deepl_key").strip()
            else (False, tr("ready.deepl_missing"))
        )
    if engine == "ai" and provider == "openrouter":
        if not config.get("OPENROUTER", "api_key").strip():
            return False, tr("ready.openrouter_key")
        model = config.get("OPENROUTER", "model").strip()
        if not model:
            return False, tr("ready.openrouter_model")
        return True, f"OpenRouter · {model}"
    if engine == "ai" and provider == "lmstudio":
        if not config.get("LMSTUDIO", "base_url").strip():
            return False, tr("ready.lmstudio_url")
        model = config.get("LMSTUDIO", "model").strip()
        if not model:
            return False, tr("ready.lmstudio_model")
        return True, f"LM Studio · {model}"
    if engine == "ai":
        model_path = config.get("AI", "model_path").strip()
        if not model_path:
            return False, tr("ready.local_model")
        if not Path(model_path).is_file():
            return False, tr("ready.local_unavailable")
        return True, f"KoboldCPP · {Path(model_path).name}"
    return False, tr("ready.unknown")
