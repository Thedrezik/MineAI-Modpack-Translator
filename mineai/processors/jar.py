import json
import os
import re
import zipfile

from mineai.constants import BOOK_PATH_MARKERS, MD_PATH_MARKERS, RESEARCH_PATH_MARKERS
from mineai.engines.base import EngineCallbacks
from mineai.engines.service import TranslationService
from mineai.json_utils import (
    apply_translations_by_path,
    iter_translatable_strings,
    load_lenient_json,
    path_to_key,
)
from mineai.mod_names import get_mod_name
from mineai.output.pack_writer import PackWriter
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.selection import (
    build_book_json_output,
    collect_book_json_selection,
    collect_book_markdown_selection,
    skip_threshold_reached,
)
from mineai.runtime.state import JobState
from mineai.text_processing import is_technical_term, looks_like_source_language


class JarProcessor:
    def __init__(
        self,
        service: TranslationService,
        state: JobState,
        callbacks: EngineCallbacks,
    ) -> None:
        self.service = service
        self.state = state
        self.callbacks = callbacks

    def process(
        self,
        jar_path: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        translate_mods: bool,
        translate_books: bool,
        pack_writer: PackWriter | None,
    ) -> None:
        if not translate_mods and not translate_books:
            return

        mod_name = get_mod_name(jar_path)
        target_file = f"{target_lang['file']}.json"
        temp_path = jar_path + ".temp"
        modified = False

        try:
            with zipfile.ZipFile(jar_path, "r") as zin:
                zout = (
                    zipfile.ZipFile(
                        temp_path,
                        "w",
                        compression=zipfile.ZIP_DEFLATED,
                    )
                    if output_mode == "inplace"
                    else None
                )
                written_inplace: set[str] = set()
                locale_files = {
                    item.filename.lower(): item
                    for item in zin.infolist()
                    if target_file in item.filename.lower()
                    or f"/{target_lang['file']}/" in item.filename.lower()
                }

                try:
                    for item in zin.infolist():
                        if not self.state.should_run():
                            break
                        self.state.wait_if_paused()
                        if not self.state.should_run():
                            break
                        fl = item.filename.lower()

                        if output_mode == "inplace" and zout:
                            if (
                                target_file not in fl
                                and f"/{target_lang['file']}/" not in fl
                            ):
                                zout.writestr(item, zin.read(item))

                        is_book_json = (
                            fl.endswith(".json")
                            and "/en_us/" in fl
                            and (
                                any(m in fl for m in BOOK_PATH_MARKERS)
                                or any(m in fl for m in RESEARCH_PATH_MARKERS)
                            )
                        )
                        is_book_md = (
                            (fl.endswith(".md") or fl.endswith(".txt"))
                            and "/en_us/" in fl
                            and any(m in fl for m in MD_PATH_MARKERS)
                        )
                        is_lang = fl.endswith("en_us.json") and not is_book_json

                        if translate_mods and is_lang:
                            modified |= self._process_lang_entry(
                                zin,
                                zout,
                                item,
                                locale_files,
                                target_file,
                                target_lang,
                                mode,
                                output_mode,
                                pack_writer,
                                mod_name,
                                written_inplace,
                            )
                        elif translate_books and is_book_json:
                            modified |= self._process_book_json(
                                zin,
                                zout,
                                item,
                                locale_files,
                                target_lang,
                                mode,
                                output_mode,
                                pack_writer,
                                mod_name,
                                written_inplace,
                            )
                        elif translate_books and is_book_md:
                            modified |= self._process_book_md(
                                zin,
                                zout,
                                item,
                                locale_files,
                                target_lang,
                                mode,
                                output_mode,
                                pack_writer,
                                mod_name,
                                written_inplace,
                            )

                    if output_mode == "inplace" and zout:
                        for item in zin.infolist():
                            fl = item.filename.lower()
                            is_target = (
                                target_file in fl
                                or f"/{target_lang['file']}/" in fl
                            )
                            if is_target and item.filename not in written_inplace:
                                zout.writestr(item, zin.read(item))
                finally:
                    if zout:
                        zout.close()

            if output_mode == "inplace" and modified and self.state.should_run():
                self._validate_inplace_archive(temp_path)
                original_mode = os.stat(jar_path).st_mode
                os.chmod(temp_path, original_mode)
                os.replace(temp_path, jar_path)

        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @staticmethod
    def _validate_inplace_archive(path: str) -> None:
        with zipfile.ZipFile(path, "r") as archive:
            bad_entry = archive.testzip()
            if bad_entry is not None:
                raise zipfile.BadZipFile(
                    f"CRC check failed for {bad_entry} in {path}"
                )

    def _process_lang_entry(
        self, zin, zout, item, locale_files, target_file, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        tr_path = re.sub(
            r"en_us\.json$",
            target_file,
            item.filename,
            flags=re.IGNORECASE,
        )
        tr_key = tr_path.lower()
        try:
            en_data = load_lenient_json(zin.read(item))
        except (json.JSONDecodeError, OSError):
            return False

        tr_data = {}
        if tr_key in locale_files:
            try:
                tr_data = load_lenient_json(zin.read(locale_files[tr_key]))
            except (json.JSONDecodeError, OSError):
                tr_data = {}

        pending = collect_lang_keys_to_translate(
            en_data,
            tr_data,
            mode,
            target_lang["regex"],
        )
        total_translatable = count_translatable_lang_entries(en_data)
        if total_translatable == 0:
            return False

        if mode == "skip" and skip_threshold_reached(
            total_translatable,
            len(pending),
        ):
            return self._copy_existing(
                zin,
                locale_files,
                tr_key,
                tr_path,
                output_mode,
                pack_writer,
                en_data,
                tr_data,
                mode,
            )

        merged = en_data.copy()
        for key, value in tr_data.items():
            if key in merged and isinstance(merged[key], str) and value:
                merged[key] = value

        if pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Интерфейс] — {len(pending)} строк",
                "cyan",
            )
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context=mod_name,
            )
            for key, value in translated.items():
                merged[key] = value

        return self._write_lang_output(
            merged,
            tr_path,
            output_mode,
            pack_writer,
            zout,
            written_inplace,
            item,
            en_data,
        )

    def _write_lang_output(self, data, tr_path, output_mode, pack_writer, zout, written_inplace, item, en_data) -> bool:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, payload)
            return True
        if zout:
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
            return True
        return False

    def _copy_existing(self, zin, locale_files, tr_key, tr_path, output_mode, pack_writer, en_data, tr_data, mode) -> bool:
        if tr_key not in locale_files:
            return False
        raw = zin.read(locale_files[tr_key])
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, raw)
            return True
        return False

    def _process_book_json(
        self, zin, zout, item, locale_files, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        tr_path = re.sub(
            r"/en_us/",
            f"/{target_lang['file']}/",
            item.filename,
            flags=re.IGNORECASE,
        )
        tr_key = tr_path.lower()
        try:
            en_data = load_lenient_json(zin.read(item))
        except (json.JSONDecodeError, OSError):
            return False

        tr_data = {}
        if mode != "force" and tr_key in locale_files:
            try:
                tr_data = load_lenient_json(zin.read(locale_files[tr_key]))
            except (json.JSONDecodeError, OSError):
                tr_data = {}

        source_map, preserved, pending = collect_book_json_selection(
            en_data,
            tr_data,
            mode,
        )
        total_translatable = len(source_map)
        if total_translatable == 0:
            return False

        if mode == "skip" and skip_threshold_reached(
            total_translatable,
            len(pending),
        ):
            return self._copy_existing(
                zin,
                locale_files,
                tr_key,
                tr_path,
                output_mode,
                pack_writer,
                en_data,
                tr_data,
                mode,
            )

        translated: dict[str, str] = {}
        if pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Книга JSON] — {len(pending)} строк",
                "magenta",
            )
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context=mod_name,
            )

        output_data = build_book_json_output(en_data, preserved, translated)
        payload = json.dumps(
            output_data,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, payload)
            return True
        if zout:
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
            return True
        return False

    def _process_book_md(
        self, zin, zout, item, locale_files, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        fl = item.filename.lower()
        tr_path = (
            re.sub(
                r"/en_us/",
                f"/{target_lang['file']}/",
                item.filename,
                flags=re.IGNORECASE,
            )
            if "/en_us/" in fl
            else item.filename
        )
        tr_key = tr_path.lower()
        try:
            en_text = zin.read(item).decode("utf-8-sig", errors="ignore")
        except OSError:
            return False

        tr_text = ""
        if mode != "force" and tr_key in locale_files:
            try:
                tr_text = zin.read(locale_files[tr_key]).decode(
                    "utf-8-sig",
                    errors="ignore",
                )
            except OSError:
                tr_text = ""

        selection = collect_book_markdown_selection(
            en_text,
            tr_text,
            mode,
            smart_glue=self.service.config.getboolean(
                "GENERAL",
                "smart_glue",
            ),
        )
        if selection.total_translatable == 0:
            if tr_key in locale_files and mode != "force":
                return self._copy_existing(
                    zin,
                    locale_files,
                    tr_key,
                    tr_path,
                    output_mode,
                    pack_writer,
                    {},
                    {},
                    mode,
                )
            return False

        if mode == "skip" and skip_threshold_reached(
            selection.total_translatable,
            len(selection.pending),
        ):
            return self._copy_existing(
                zin,
                locale_files,
                tr_key,
                tr_path,
                output_mode,
                pack_writer,
                {},
                {},
                mode,
            )

        if selection.pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Книга MD] — "
                f"{len(selection.pending)} строк",
                "magenta",
            )
            translated = self.service.translate_dict(
                selection.pending,
                target_lang,
                self.callbacks,
                context=mod_name,
            )
            for index_text, value in translated.items():
                index = int(index_text)
                if index_text in selection.title_meta:
                    prefix, suffix = selection.title_meta[index_text]
                    selection.lines_out[index] = prefix + value + suffix
                else:
                    selection.lines_out[index] = value

        payload = "\n".join(selection.lines_out).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, payload)
            return True
        if zout:
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
            return True
        return False

