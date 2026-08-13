import json
import os
from formatkit import FormatRegistry, FormatValidationError

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.analysis_items import loose_file_scope
from mineai.io_utils import atomic_write_bytes
from mineai.json_utils import load_lenient_json
from mineai.output.pack_writer import PackWriter
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.locale_paths import ensure_distinct_paths
from mineai.processors.loose_paths import (
    loose_pack_target_path,
    loose_target_disk_path,
)
from mineai.processors.selection import (
    build_book_json_output,
    collect_book_json_selection,
    skip_threshold_reached,
)
from mineai.runtime.state import JobState


class LooseJsonProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks
        self.format_registry = FormatRegistry.default()

    def process(
        self,
        file_path: str,
        mc_dir: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        pack_writer: PackWriter | None,
    ) -> str | None:
        if not file_path.casefold().endswith(".json"):
            return self._process_document(
                file_path,
                mc_dir,
                target_lang=target_lang,
                mode=mode,
                output_mode=output_mode,
                pack_writer=pack_writer,
            )

        tr_internal = loose_pack_target_path(
            file_path,
            mc_dir,
            target_lang["file"],
        )
        tr_disk = loose_target_disk_path(file_path, target_lang["file"])
        is_book = loose_file_scope(file_path) == "books"
        ensure_distinct_paths(file_path, tr_disk)

        with open(file_path, encoding="utf-8") as f:
            en_data = load_lenient_json(f.read().encode("utf-8"))
        tr_data = {}
        if os.path.exists(tr_disk):
            with open(tr_disk, encoding="utf-8") as f:
                tr_data = load_lenient_json(f.read().encode("utf-8"))

        if is_book:
            source_map, preserved, pending = collect_book_json_selection(
                en_data,
                tr_data,
                mode,
                target_lang,
            )
            total = len(source_map)
        else:
            pending = collect_lang_keys_to_translate(
                en_data,
                tr_data,
                mode,
                target_lang["regex"],
            )
            total = count_translatable_lang_entries(en_data)
        label = "Словарь: " + os.path.basename(os.path.dirname(os.path.dirname(file_path)))

        if mode == "skip" and skip_threshold_reached(total, len(pending)):
            if (
                output_mode == "resourcepack"
                and pack_writer
                and tr_internal
                and os.path.exists(tr_disk)
            ):
                with open(tr_disk, "rb") as f:
                    pack_writer.write(tr_internal, f.read())
            return

        if is_book:
            merged = build_book_json_output(en_data, preserved, {})
        else:
            merged = en_data.copy()
            for k, v in tr_data.items():
                if k in merged:
                    merged[k] = v

        if pending:
            self.callbacks.on_log(f"⚡ Перевод {label} — {len(pending)} строк", "cyan")
            translate = (
                getattr(self.service, "translate_formatted_dict", None)
                if is_book
                else None
            ) or self.service.translate_dict
            translated = translate(
                pending,
                target_lang,
                self.callbacks,
                context="Локализация Квестов/Скриптов",
                prompt_type="books",
            )

            if not self.state.should_run():
                return

            if is_book:
                merged = build_book_json_output(en_data, preserved, translated)
            else:
                merged.update(translated)

        payload = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer and tr_internal:
            pack_writer.write(tr_internal, payload)
        else:
            atomic_write_bytes(tr_disk, payload)
            return tr_disk
        return None

    def _process_document(
        self,
        file_path: str,
        mc_dir: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        pack_writer: PackWriter | None,
    ) -> str | None:
        with open(file_path, encoding="utf-8-sig") as source_handle:
            source_text = source_handle.read()

        tr_disk = loose_target_disk_path(file_path, target_lang["file"])
        ensure_distinct_paths(file_path, tr_disk)
        tr_internal = loose_pack_target_path(
            file_path,
            mc_dir,
            target_lang["file"],
        )
        logical_path = os.path.relpath(file_path, mc_dir).replace("\\", "/")
        target_hint = tr_internal or os.path.relpath(
            tr_disk,
            mc_dir,
        ).replace("\\", "/")
        plan = self.format_registry.plan(
            logical_path,
            source_text,
            target_lang["file"],
            target_path_hint=target_hint,
        )

        active_plan = plan
        pending_ids = {unit.id for unit in plan.units}
        if mode != "force" and os.path.exists(tr_disk):
            try:
                with open(tr_disk, encoding="utf-8-sig") as target_handle:
                    target_text = target_handle.read()
                target_plan = self.format_registry.plan(
                    target_hint,
                    target_text,
                    target_lang["file"],
                    target_path_hint=target_hint,
                )
                active_plan, pending_ids = plan.merge_existing(
                    target_plan,
                    target_lang["regex"],
                )
            except (OSError, ValueError, FormatValidationError):
                self.callbacks.on_log(
                    f"⚠️ Существующий перевод {tr_disk} имеет другую "
                    "структуру и будет перестроен из английского оригинала",
                    "yellow",
                )

        pending = {
            unit.id: unit.payload
            for unit in active_plan.units
            if mode == "force" or unit.id in pending_ids
        }
        if mode == "skip" and skip_threshold_reached(
            len(plan.units),
            len(pending),
        ):
            pending = {}

        cache_contexts = {
            unit_id: f"{plan.adapter_id}|{logical_path}|{unit_id}"
            for unit_id in pending
        }
        validators = {
            unit_id: (
                lambda candidate, current_id=unit_id: self._formatkit_reason(
                    active_plan,
                    current_id,
                    candidate,
                )
            )
            for unit_id in pending
        }
        translated: dict[str, str] = {}
        if pending:
            self.callbacks.on_log(
                f"⚡ Перевод {logical_path} — {len(pending)} смысловых блоков",
                "cyan",
            )
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context=f"{logical_path} | {plan.adapter_id}",
                prompt_type="books",
                cache_contexts=cache_contexts,
                candidate_validators=validators,
            )
        if not self.state.should_run():
            return None

        result, rejected = active_plan.apply_resilient(translated)
        if rejected:
            discard = getattr(self.service, "discard_cached_translation", None)
            units = {unit.id: unit for unit in active_plan.units}
            for unit_id, reason in rejected.items():
                if callable(discard):
                    discard(
                        target_lang["api"],
                        units[unit_id].payload,
                        cache_contexts.get(unit_id, ""),
                    )
                self.callbacks.on_log(
                    f"⚠️ {logical_path}: блок {unit_id} восстановлен из "
                    f"оригинала: {reason}",
                    "yellow",
                )

        payload = result.text.encode("utf-8")
        if output_mode == "resourcepack" and pack_writer and tr_internal:
            pack_writer.write(tr_internal, payload)
            return None
        atomic_write_bytes(tr_disk, payload)
        return tr_disk

    @staticmethod
    def _formatkit_reason(plan, unit_id: str, candidate: str) -> str | None:
        try:
            plan.apply({unit_id: candidate})
        except FormatValidationError as exc:
            return f"FormatKit: {exc}"
        return None
