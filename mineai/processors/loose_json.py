import json
import os

from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.io_utils import atomic_write_bytes
from mineai.json_utils import load_lenient_json
from mineai.output.pack_writer import PackWriter
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.selection import skip_threshold_reached
from mineai.runtime.state import JobState


class LooseJsonProcessor:
    def __init__(self, service: TranslationService, state: JobState, callbacks: EngineCallbacks) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(
        self,
        file_path: str,
        mc_dir: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        pack_writer: PackWriter | None,
    ) -> None:
        rel = os.path.relpath(file_path, mc_dir).replace("\\", "/")
        if "assets/" in rel:
            internal = rel[rel.find("assets/") :]
        else:
            internal = "assets/kubejs/lang/" + os.path.basename(file_path)

        tr_internal = internal.replace("en_us.json", f"{target_lang['file']}.json")
        tr_disk = file_path.replace("en_us.json", f"{target_lang['file']}.json")

        with open(file_path, encoding="utf-8") as f:
            en_data = load_lenient_json(f.read().encode("utf-8"))
        tr_data = {}
        if os.path.exists(tr_disk):
            with open(tr_disk, encoding="utf-8") as f:
                tr_data = load_lenient_json(f.read().encode("utf-8"))

        pending = collect_lang_keys_to_translate(en_data, tr_data, mode, target_lang["regex"])
        total = count_translatable_lang_entries(en_data)
        label = "Словарь: " + os.path.basename(os.path.dirname(os.path.dirname(file_path)))

        if mode == "skip" and skip_threshold_reached(total, len(pending)):
            if output_mode == "resourcepack" and pack_writer and os.path.exists(tr_disk):
                with open(tr_disk, "rb") as f:
                    pack_writer.write(tr_internal, f.read())
            return

        merged = en_data.copy()
        for k, v in tr_data.items():
            if k in merged:
                merged[k] = v

        if pending:
            self.callbacks.on_log(f"⚡ Перевод {label} — {len(pending)} строк", "cyan")
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context="Локализация Квестов/Скриптов",
                prompt_type="books",
            )

            if not self.state.should_run():
                return

            merged.update(translated)

        payload = json.dumps(merged, ensure_ascii=False, indent=2).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_internal, payload)
        elif output_mode == "inplace":
            atomic_write_bytes(tr_disk, payload)
