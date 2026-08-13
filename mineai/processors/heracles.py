"""Safe in-place processing for Heracles / Odyssey Quests text."""

from __future__ import annotations

import hashlib
import json
import os
import shutil

from formatkit import FormatRegistry, FormatValidationError
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_text
from mineai.processors.selection import skip_threshold_reached
from mineai.runtime.state import JobState


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _state_path(path: str) -> str:
    return path + ".bak.mineai"


def _tracked(path: str, backup: str) -> bool:
    if not os.path.exists(backup):
        return False
    current_hash, backup_hash = _sha256(path), _sha256(backup)
    if current_hash == backup_hash:
        return True
    try:
        with open(_state_path(path), encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    return state.get("baseline_sha256") == backup_hash and state.get("output_sha256") == current_hash


class HeraclesProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks
        self.registry = FormatRegistry.default()

    def process(self, file_path: str, *, target_lang: dict, mode: str) -> str | None:
        backup = file_path + ".bak"
        with open(file_path, encoding="utf-8-sig") as handle:
            current_text = handle.read()
        logical_path = file_path.replace("\\", "/")
        current_plan = self.registry.plan(logical_path, current_text, target_lang["file"], target_path_hint=logical_path)

        source_text = current_text
        source_plan = current_plan
        trusted = _tracked(file_path, backup)
        if os.path.exists(backup) and not trusted:
            try:
                with open(backup, encoding="utf-8-sig") as handle:
                    candidate_text = handle.read()
                candidate_plan = self.registry.plan(logical_path, candidate_text, target_lang["file"], target_path_hint=logical_path)
                candidate_plan.merge_existing(current_plan, target_lang["regex"])
            except (OSError, ValueError, FormatValidationError):
                stale = f"{backup}.stale-{_sha256(backup)[:12]}"
                if not os.path.exists(stale):
                    shutil.copy2(backup, stale)
                shutil.copy2(file_path, backup)
            else:
                trusted = True

        if os.path.exists(backup) and trusted:
            with open(backup, encoding="utf-8-sig") as handle:
                source_text = handle.read()
            source_plan = self.registry.plan(logical_path, source_text, target_lang["file"], target_path_hint=logical_path)
        elif not os.path.exists(backup):
            shutil.copy2(file_path, backup)

        active_plan = source_plan
        pending_ids = {unit.id for unit in source_plan.units}
        if mode != "force" and source_text != current_text:
            active_plan, pending_ids = source_plan.merge_existing(current_plan, target_lang["regex"])
        pending = {
            unit.id: unit.payload
            for unit in active_plan.units
            if mode == "force" or unit.id in pending_ids
        }
        if mode == "skip" and skip_threshold_reached(len(source_plan.units), len(pending)):
            return None
        if not pending:
            return None

        cache_contexts = {}
        for unit in active_plan.units:
            if unit.id not in pending:
                continue
            cache_contexts[unit.id] = (
                f"heracles-group|{unit.payload}"
                if unit.kind == "heracles-group"
                else f"{active_plan.adapter_id}|{logical_path}|{unit.id}"
            )
        validators = {
            unit_id: (lambda candidate, current_id=unit_id: self._reason(active_plan, current_id, candidate))
            for unit_id in pending
        }
        self.callbacks.on_log(
            f"⚡ Перевод Heracles [{os.path.basename(file_path)}] — {len(pending)} блоков",
            "cyan",
        )
        translated = self.service.translate_dict(
            pending,
            target_lang,
            self.callbacks,
            context=f"Heracles | {logical_path}",
            prompt_type="quests",
            cache_contexts=cache_contexts,
            candidate_validators=validators,
        )
        if not self.state.should_run():
            return None
        result, rejected = active_plan.apply_resilient(translated)
        units = {unit.id: unit for unit in active_plan.units}
        for unit_id, reason in rejected.items():
            discard = getattr(self.service, "discard_cached_translation", None)
            if callable(discard):
                discard(target_lang["api"], units[unit_id].payload, cache_contexts.get(unit_id, ""))
            self.callbacks.on_log(f"⚠️ Heracles: блок {unit_id} восстановлен: {reason}", "yellow")
        atomic_write_text(file_path, result.text)
        state = {
            "version": 1,
            "baseline_sha256": _sha256(backup),
            "output_sha256": _sha256(file_path),
        }
        atomic_write_text(_state_path(file_path), json.dumps(state, ensure_ascii=False, indent=2))
        return file_path

    @staticmethod
    def _reason(plan, unit_id: str, candidate: str) -> str | None:
        try:
            plan.apply({unit_id: candidate})
        except FormatValidationError as exc:
            return f"FormatKit: {exc}"
        return None
