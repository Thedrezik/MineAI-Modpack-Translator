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
from mineai.processors.locale_keys import collect_lang_keys_to_translate, count_translatable_lang_entries
from mineai.runtime.state import JobState
from mineai.text_processing import (
    already_translated,
    apply_smart_glue,
    is_technical_term,
    looks_like_source_language,
)


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
        tr_path = re.sub(r"en_us\.json$", target_file, item.filename, flags=re.IGNORECASE)
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

        pending = collect_lang_keys_to_translate(en_data, tr_data, mode, target_lang["regex"])
        total = count_translatable_lang_entries(en_data)
        if total == 0:
            return False

        if mode == "skip" and (total - len(pending)) >= total * 0.9:
            return self._copy_existing(zin, locale_files, tr_key, tr_path, output_mode, pack_writer, en_data, tr_data, mode)

        merged = en_data.copy()
        for k, v in tr_data.items():
            if k in merged and isinstance(merged[k], str) and v:
                merged[k] = v

        if not pending:
            return self._write_lang_output(merged, tr_path, output_mode, pack_writer, zout, written_inplace, item, en_data)

        self.callbacks.on_log(f"⚡ Перевод {mod_name} [Интерфейс] — {len(pending)} строк", "cyan")
        translated = self.service.translate_dict(pending, target_lang, self.callbacks, context=mod_name)
        for key, value in translated.items():
            merged[key] = value
        return self._write_lang_output(merged, tr_path, output_mode, pack_writer, zout, written_inplace, item, en_data)

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
        tr_path = re.sub(r"/en_us/", f"/{target_lang['file']}/", item.filename, flags=re.IGNORECASE)
        tr_key = tr_path.lower()
        # --- ФИКС ДЛЯ КНИГ PATCHOULI (Защита оригинального перевода) ---
        if mode == "append" and tr_key in locale_files:
            if output_mode == "resourcepack" and pack_writer:
                pack_writer.write(tr_path, zin.read(locale_files[tr_key]))
            elif output_mode == "inplace" and zout:
                zout.writestr(tr_path, zin.read(locale_files[tr_key]))
                written_inplace.add(tr_path)
            return True
        # ---------------------------------------------------------------
        try:
            en_data = load_lenient_json(zin.read(item))
        except (json.JSONDecodeError, OSError):
            return False

        tr_data = {}
        if tr_key in locale_files:
            try:
                tr_data = load_lenient_json(zin.read(locale_files[tr_key]))
            except (json.JSONDecodeError, OSError):
                pass

        en_map = {path_to_key(p): s for p, s in iter_translatable_strings(en_data) if s.strip()}
        tr_map = {path_to_key(p): s for p, s in iter_translatable_strings(tr_data)} if tr_data else {}

        pending: dict[str, str] = {}
        for path_key, source in en_map.items():
            if is_technical_term(source):
                continue
            if not looks_like_source_language(source):
                continue
            existing = tr_map.get(path_key, "")
            if mode == "append" and existing.strip() and existing != source:
                continue
            if mode == "append" and existing.strip() == source:
                pending[path_key] = source
            else:
                pending[path_key] = source

        if not en_map:
            return False

        if mode == "skip" and len(pending) <= len(en_map) * 0.1:
            return self._copy_existing(zin, locale_files, tr_key, tr_path, output_mode, pack_writer, en_data, tr_data, mode)

        if pending:
            self.callbacks.on_log(f"⚡ Перевод {mod_name} [Книга JSON] — {len(pending)} строк", "magenta")
            translated = self.service.translate_dict(pending, target_lang, self.callbacks, context=mod_name)
            apply_translations_by_path(en_data, translated)

        payload = json.dumps(en_data, ensure_ascii=False, indent=2).encode("utf-8")
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
            re.sub(r"/en_us/", f"/{target_lang['file']}/", item.filename, flags=re.IGNORECASE)
            if "/en_us/" in fl
            else item.filename
        )
        tr_key = tr_path.lower()
        target_regex = target_lang["regex"]
        # --- ФИКС ДЛЯ КНИГ MD (Защита оригинального перевода) ---
        if mode == "append" and tr_key in locale_files:
            if output_mode == "resourcepack" and pack_writer:
                pack_writer.write(tr_path, zin.read(locale_files[tr_key]))
            elif output_mode == "inplace" and zout:
                zout.writestr(tr_path, zin.read(locale_files[tr_key]))
                written_inplace.add(tr_path)
            return True
        # --------------------------------------------------------
        try:
            en_text = zin.read(item).decode("utf-8-sig", errors="ignore")
        except OSError:
            return False

        if self.service.config.getboolean("GENERAL", "smart_glue"):
            en_text = apply_smart_glue(en_text)

        tr_text = ""
        if tr_key in locale_files:
            tr_text = zin.read(locale_files[tr_key]).decode("utf-8-sig", errors="ignore")
        tr_lines = tr_text.split("\n") if tr_text else []

        pending: dict[str, str] = {}
        title_meta: dict[str, tuple[str, str]] = {}
        lines_out: list[str] = []
        in_yaml = False

        for idx, line in enumerate(en_text.split("\n")):
            stripped = line.strip()
            if stripped == "---":
                in_yaml = not in_yaml
                lines_out.append(line)
                continue

            if in_yaml:
                if stripped.lower().startswith("title:"):
                    match = re.match(r'^(\s*title\s*:\s*[\'"]?)(.*?)([\'"]?)$', line, re.IGNORECASE)
                    if match and looks_like_source_language(match.group(2)):
                        prefix, title, suffix = match.groups()
                        title_meta[str(idx)] = (prefix, suffix)
                        if mode == "append" and idx < len(tr_lines) and already_translated(tr_lines[idx], target_regex):
                            lines_out.append(tr_lines[idx])
                        else:
                            lines_out.append(line)
                            pending[str(idx)] = title
                    else:
                        lines_out.append(line)
                else:
                    lines_out.append(line)
                continue

            if stripped.startswith("<") or stripped.startswith("!["):
                lines_out.append(line)
                continue
            if not stripped or not looks_like_source_language(line) or is_technical_term(line):
                lines_out.append(line)
                continue

            if mode == "append" and idx < len(tr_lines) and tr_lines[idx].strip() and already_translated(tr_lines[idx], target_regex):
                lines_out.append(tr_lines[idx])
            else:
                lines_out.append(line)
                pending[str(idx)] = line

        # --- ФИКС 2: Режим пропуска (skip) ---
        total_translatable = sum(
            1 for line in en_text.split("\n")
            if line.strip()
            and not line.strip().startswith("<")
            and not line.strip().startswith("![")
            and looks_like_source_language(line)
            and not is_technical_term(line)
        )
        
        if mode == "skip" and total_translatable > 0 and len(pending) <= total_translatable * 0.1:
            if tr_key in locale_files:
                raw = zin.read(locale_files[tr_key])
                if output_mode == "resourcepack" and pack_writer:
                    pack_writer.write(tr_path, raw)
                    return True
                elif zout:
                    zout.writestr(tr_path, raw)
                    written_inplace.add(tr_path)
                    return True
            return False
        # --------------------------------------

        if not pending:
            payload = "\n".join(lines_out).encode("utf-8")
            if output_mode == "resourcepack" and pack_writer:
                pack_writer.write(tr_path, payload)
            elif zout:
                zout.writestr(tr_path, payload)
                written_inplace.add(tr_path)
            return True  # <--- ФИКС 1: Возвращаем True вместо bool(pending)

        self.callbacks.on_log(f"⚡ Перевод {mod_name} [Книга MD] — {len(pending)} строк", "magenta")
        translated = self.service.translate_dict(pending, target_lang, self.callbacks, context=mod_name)
        for idx_s, value in translated.items():
            idx = int(idx_s)
            if idx_s in title_meta:
                prefix, suffix = title_meta[idx_s]
                lines_out[idx] = prefix + value + suffix
            else:
                lines_out[idx] = value

        payload = "\n".join(lines_out).encode("utf-8")
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(tr_path, payload)
            return True
        if zout:
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
        return True
