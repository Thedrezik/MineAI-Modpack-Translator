import json
import os
import re
import zipfile

from formatkit import (
    FormatRegistry,
    FormatValidationError,
    relocated_dependencies,
)
from formatkit.adapters.modonomicon import (
    build_localized_overlay,
    is_modonomicon_path,
)
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
from mineai.processors.book_paths import (
    MarkdownBookLocator,
    legacy_lang_target_path,
    localized_json_target_path,
    minecraft_lang_json_target_path,
)
from mineai.language_validation import translation_needs_repair
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.selection import (
    build_book_json_output,
    collect_book_json_selection,
    skip_threshold_reached,
)
from mineai.runtime.state import JobState
from mineai.text_processing import (
    is_article_removed_technical_translation,
    is_technical_term,
    looks_like_source_language,
)


class JarProcessor:
    def __init__(
        self,
        service: TranslationService,
        state: JobState,
        callbacks: EngineCallbacks,
    ) -> str | None:
        self.service = service
        self.state = state
        self.callbacks = callbacks
        self.format_registry = FormatRegistry.default()

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
        book_locator: MarkdownBookLocator | None = None,
        selected_units: dict[str, frozenset[str]] | None = None,
        retranslate_selected: bool = False,
    ) -> None:
        if not translate_mods and not translate_books:
            return

        mod_name = get_mod_name(jar_path)
        target_file = f"{target_lang['file']}.json"
        temp_path = jar_path + ".temp"
        modified = False

        try:
            with zipfile.ZipFile(jar_path, "r") as zin:
                archive_items = zin.infolist()
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
                    for item in archive_items
                }
                book_locator = book_locator or MarkdownBookLocator(
                    [item.filename for item in archive_items],
                    target_lang["file"],
                )
                companion_documents = []
                if translate_books:
                    for document_item in archive_items:
                        if not is_modonomicon_path(document_item.filename):
                            continue
                        try:
                            document_text = zin.read(document_item).decode(
                                "utf-8-sig",
                                errors="ignore",
                            )
                        except OSError:
                            continue
                        companion_documents.append(
                            (document_item.filename, document_text)
                        )
                companion_lang_keys = self.format_registry.companion_lang_keys(
                    companion_documents
                )
                companion_lang_prefixes = (
                    self.format_registry.companion_lang_prefixes(
                        [item.filename for item in archive_items]
                    )
                    if translate_books
                    else ()
                )

                try:
                    for item in archive_items:
                        if not self.state.should_run():
                            break
                        self.state.wait_if_paused()
                        if not self.state.should_run():
                            break
                        fl = item.filename.lower()
                        selected_unit_ids = _selected_unit_ids(
                            selected_units,
                            item.filename,
                        )
                        if selected_units is not None and selected_unit_ids is None:
                            continue

                        if output_mode == "inplace" and zout:
                            if (
                                target_file not in fl
                                and f"/{target_lang['file']}/" not in fl
                                and f"/_{target_lang['file']}/" not in fl
                                and not fl.endswith(
                                    f"/{target_lang['file']}.lang"
                                )
                            ):
                                zout.writestr(item, zin.read(item))

                        is_book_json = localized_json_target_path(
                            item.filename,
                            target_lang["file"],
                        ) is not None
                        markdown_target = book_locator.target_path(item.filename)
                        is_book_md = markdown_target is not None
                        is_modonomicon = is_modonomicon_path(item.filename)
                        upstream_adapter_id = (
                            self.format_registry.upstream_adapter_id(
                                item.filename
                            )
                        )
                        upstream_target = (
                            self.format_registry.upstream_target_path(
                                item.filename,
                                target_lang["file"],
                            )
                        )
                        legacy_lang_target = legacy_lang_target_path(
                            item.filename,
                            target_lang["file"],
                        )
                        is_lang = (
                            minecraft_lang_json_target_path(
                                item.filename,
                                target_lang["file"],
                            ) is not None
                            and not is_book_json
                        )

                        if translate_mods and legacy_lang_target:
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
                                legacy_lang_target,
                                prompt_type="mods",
                                content_label="Интерфейс LANG",
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
                            )
                        elif translate_mods and is_lang:
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
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
                            )
                        elif (
                            companion_lang_prefixes or companion_lang_keys
                        ) and is_lang:
                            modified |= self._process_book_lang_metadata(
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
                            companion_lang_prefixes,
                            companion_lang_keys,
                            allowed_unit_ids=selected_unit_ids,
                            retranslate_selected=retranslate_selected,
                        )
                        elif translate_books and is_modonomicon:
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
                                item.filename,
                                content_label="Книга Modonomicon",
                                copy_when_empty=False,
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
                            )
                        elif (
                            translate_books
                            and upstream_adapter_id == "patchouli-book-json"
                            and upstream_target
                            and self._formatkit_ready(
                                zin,
                                item,
                                target_lang,
                                upstream_target,
                            )
                        ):
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
                                upstream_target,
                                content_label="Книга Patchouli",
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
                            )
                        elif (
                            translate_books
                            and upstream_adapter_id
                            in {
                                "oracle-index-mdx",
                                "oracle-index-meta-json",
                            }
                            and upstream_target
                        ):
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
                                upstream_target,
                                content_label="Книга Oracle Index",
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
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
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
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
                                markdown_target,
                                allowed_unit_ids=selected_unit_ids,
                                retranslate_selected=retranslate_selected,
                            )

                    if output_mode == "inplace" and zout:
                        for item in archive_items:
                            fl = item.filename.lower()
                            is_target = (
                                target_file in fl
                                or f"/{target_lang['file']}/" in fl
                                or f"/_{target_lang['file']}/" in fl
                                or fl.endswith(
                                    f"/{target_lang['file']}.lang"
                                )
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
                return jar_path

        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        return None
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
        *, allowed_unit_ids=None, retranslate_selected=False,
    ) -> bool:
        tr_path = minecraft_lang_json_target_path(
            item.filename,
            target_lang["file"],
        )
        if tr_path is None:
            return False
        try:
            source_text = zin.read(item).decode("utf-8-sig", errors="ignore")
            self.format_registry.plan(
                item.filename,
                source_text,
                target_lang["file"],
                target_path_hint=tr_path,
            )
        except (OSError, ValueError, FormatValidationError):
            return self._process_lang_entry_legacy(
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
                allowed_unit_ids=allowed_unit_ids,
                retranslate_selected=retranslate_selected,
            )
        return self._process_book_md(
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
            tr_path,
            prompt_type="mods",
            content_label="Интерфейс JSON",
            allowed_unit_ids=allowed_unit_ids,
            retranslate_selected=retranslate_selected,
        )

    def _process_lang_entry_legacy(
        self, zin, zout, item, locale_files, target_file, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
        *, allowed_unit_ids=None, retranslate_selected=False,
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
        if allowed_unit_ids is not None:
            pending = {
                key: value
                for key, value in pending.items()
                if key in allowed_unit_ids
            }
            if retranslate_selected:
                pending.update(
                    {
                        key: value
                        for key, value in en_data.items()
                        if key in allowed_unit_ids and isinstance(value, str)
                    }
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
                f"⚡ Перевод {mod_name} [Интерфейс fallback] — "
                f"{len(pending)} строк",
                "cyan",
            )
            merged.update(
                self.service.translate_dict(
                    pending,
                    target_lang,
                    self.callbacks,
                    context=mod_name,
                )
            )
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

    def _formatkit_ready(
        self,
        archive,
        item,
        target_lang,
        target_path,
    ) -> bool:
        try:
            source_text = archive.read(item).decode(
                "utf-8-sig",
                errors="ignore",
            )
            self.format_registry.plan(
                item.filename,
                source_text,
                target_lang["file"],
                target_path_hint=target_path,
            )
        except (OSError, ValueError, FormatValidationError):
            return False
        return True

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
        *, allowed_unit_ids=None, retranslate_selected=False,
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
            target_lang,
        )
        total_translatable = len(source_map)
        if total_translatable == 0:
            return False
        if allowed_unit_ids is not None:
            pending = {
                key: value
                for key, value in pending.items()
                if key in allowed_unit_ids
            }
            if retranslate_selected:
                pending.update(
                    {
                        key: value
                        for key, value in source_map.items()
                        if key in allowed_unit_ids
                    }
                )

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
            translate_book = getattr(
                self.service,
                "translate_formatted_dict",
                self.service.translate_dict,
            )
            translated = translate_book(
                pending,
                target_lang,
                self.callbacks,
                context=f"{mod_name} | {item.filename}",
                prompt_type="books",
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
        output_mode, pack_writer, mod_name, written_inplace, target_path,
        *, prompt_type="books", content_label="Книга MD",
        copy_when_empty=True, allowed_unit_ids=None,
        retranslate_selected=False,
    ) -> bool:
        try:
            en_text = zin.read(item).decode("utf-8-sig", errors="ignore")
        except OSError:
            return False

        plan = self.format_registry.plan(
            item.filename,
            en_text,
            target_lang["file"],
            target_path_hint=target_path,
        )
        tr_path = plan.target_path or target_path
        tr_key = tr_path.lower()

        tr_text = ""
        if mode != "force" and tr_key in locale_files:
            try:
                tr_text = zin.read(locale_files[tr_key]).decode(
                    "utf-8-sig",
                    errors="ignore",
                )
            except OSError:
                tr_text = ""

        if not plan.units:
            if copy_when_empty and tr_key in locale_files and mode != "force":
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

        active_plan = plan
        pending_ids = {unit.id for unit in plan.units}
        if tr_text and mode != "force":
            merged = self._merge_existing_formatkit_plan(
                plan,
                tr_path,
                tr_text,
                target_lang,
            )
            if merged is None:
                self.callbacks.on_log(
                    f"⚠️ {mod_name}: существующий перевод {tr_path} "
                    "от другой структуры страницы; он проигнорирован",
                    "yellow",
                )
            else:
                active_plan, pending_ids = merged

        if allowed_unit_ids is not None:
            pending_ids = set(pending_ids).intersection(allowed_unit_ids)
            if retranslate_selected:
                pending_ids.update(
                    unit.id
                    for unit in active_plan.units
                    if unit.id in allowed_unit_ids
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

        translated: dict[str, str] = {}
        cache_contexts = {
            unit_id: f"{plan.adapter_id}|{item.filename}|{unit_id}"
            for unit_id in pending
        }
        candidate_validators = {
            unit_id: (
                lambda candidate, current_id=unit_id: self._formatkit_reason(
                    active_plan,
                    current_id,
                    candidate,
                    target_lang,
                )
            )
            for unit_id in pending
        }
        if pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [{content_label}] — "
                f"{len(pending)} смысловых блоков",
                "magenta",
            )
            translated = self.service.translate_dict(
                pending,
                target_lang,
                self.callbacks,
                context=(
                    f"{mod_name} | {plan.adapter_id} | {item.filename}"
                ),
                prompt_type=prompt_type,
                cache_contexts=cache_contexts,
                candidate_validators=candidate_validators,
            )

        result, rejected = active_plan.apply_resilient(translated)
        output_text = result.text
        if rejected:
            units_by_id = {unit.id: unit for unit in active_plan.units}
            discard = getattr(
                self.service,
                "discard_cached_translation",
                None,
            )
            for unit_id, reason in rejected.items():
                if callable(discard):
                    discard(
                        target_lang["api"],
                        units_by_id[unit_id].payload,
                        cache_contexts.get(unit_id, ""),
                    )
                self.callbacks.on_log(
                    f"⚠️ {mod_name}: блок {unit_id} восстановлен из "
                    f"оригинала: {reason}",
                    "yellow",
                )

        if output_mode == "resourcepack" and pack_writer:
            if is_modonomicon_path(item.filename):
                localized = build_localized_overlay(
                    item.filename,
                    en_text,
                    output_text,
                    target_lang["file"],
                )
                output_text = localized.data_text
                pack_writer.write(
                    localized.source_lang_path,
                    json.dumps(
                        localized.source_entries,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8"),
                )
                pack_writer.write(
                    localized.target_lang_path,
                    json.dumps(
                        localized.target_entries,
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8"),
                )
            payload = output_text.encode("utf-8")
            pack_writer.write(tr_path, payload)
            self._copy_relocated_dependencies(
                zin,
                item.filename,
                tr_path,
                output_text,
                locale_files,
                output_mode,
                pack_writer,
                zout,
                written_inplace,
            )
            return True
        if zout:
            payload = output_text.encode("utf-8")
            zout.writestr(tr_path, payload)
            written_inplace.add(tr_path)
            self._copy_relocated_dependencies(
                zin,
                item.filename,
                tr_path,
                output_text,
                locale_files,
                output_mode,
                pack_writer,
                zout,
                written_inplace,
            )
            return True
        return False

    @staticmethod
    def _copy_relocated_dependencies(
        zin,
        source_path,
        target_path,
        text,
        archive_items,
        output_mode,
        pack_writer,
        zout,
        written_inplace,
    ) -> None:
        for dependency_source, dependency_target in relocated_dependencies(
            source_path,
            target_path,
            text,
            (item.filename for item in archive_items.values()),
        ):
            target_key = dependency_target.casefold()
            if target_key in archive_items:
                continue
            source_item = archive_items.get(dependency_source.casefold())
            if source_item is None:
                continue
            payload = zin.read(source_item)
            if output_mode == "resourcepack" and pack_writer:
                pack_writer.write(dependency_target, payload)
            elif zout and dependency_target not in written_inplace:
                zout.writestr(dependency_target, payload)
                written_inplace.add(dependency_target)

    def _process_book_lang_metadata(
        self, zin, zout, item, locale_files, target_file, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace, prefixes,
        exact_keys=frozenset(), *, allowed_unit_ids=None,
        retranslate_selected=False,
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

        source = {
            key: value
            for key, value in en_data.items()
            if isinstance(key, str)
            and isinstance(value, str)
            and (key.startswith(prefixes) or key in exact_keys)
        }
        if not source:
            return False

        existing = {}
        if tr_key in locale_files:
            try:
                existing = load_lenient_json(zin.read(locale_files[tr_key]))
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing_for_source = {
            key: value for key, value in existing.items() if key in source
        }
        pending = collect_lang_keys_to_translate(
            source,
            existing_for_source,
            mode,
            target_lang["regex"],
        )
        if allowed_unit_ids is not None:
            pending = {
                key: value
                for key, value in pending.items()
                if key in allowed_unit_ids
            }
            if retranslate_selected:
                pending.update(
                    {
                        key: value
                        for key, value in source.items()
                        if key in allowed_unit_ids
                    }
                )
        merged = dict(existing)
        for key, value in existing_for_source.items():
            if value:
                merged[key] = value
        if pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Метаданные книги] — "
                f"{len(pending)} строк",
                "magenta",
            )
            merged.update(
                self.service.translate_dict(
                    pending,
                    target_lang,
                    self.callbacks,
                    context=f"{mod_name} | manual metadata",
                    prompt_type="books",
                )
            )
        if not merged:
            return False
        return self._write_lang_output(
            merged,
            tr_path,
            output_mode,
            pack_writer,
            zout,
            written_inplace,
            item,
            source,
        )

    @staticmethod
    def _formatkit_reason(
        plan,
        unit_id: str,
        candidate: str,
        target_lang: dict,
    ) -> str | None:
        def validate_visible(source: str, translated: str) -> str | None:
            if not re.search(r"[A-Za-z]", source):
                return None
            if is_technical_term(source.strip()):
                return None
            if is_article_removed_technical_translation(
                source,
                translated,
                target_lang.get("api", ""),
            ):
                return None
            if translation_needs_repair(source, translated, target_lang):
                return f"Visible text segment was not fully translated: {source!r}"
            return None

        reason = plan.candidate_error(unit_id, candidate, validate_visible)
        return f"FormatKit: {reason}" if reason else None

    def _merge_existing_formatkit_plan(
        self,
        source_plan,
        target_path: str,
        target_text: str,
        target_lang: dict,
    ):
        try:
            target_plan = self.format_registry.plan(
                target_path,
                target_text,
                target_lang["file"],
                target_path_hint=target_path,
            )
        except ValueError:
            return None
        try:
            return source_plan.merge_existing(
                target_plan,
                target_lang["regex"],
            )
        except FormatValidationError:
            return None


def _selected_unit_ids(
    selected_units: dict[str, frozenset[str]] | None,
    logical_path: str,
) -> frozenset[str] | None:
    if selected_units is None:
        return None
    normalized = logical_path.replace("\\", "/")
    for path, unit_ids in selected_units.items():
        if path.replace("\\", "/").casefold() == normalized.casefold():
            return frozenset(unit_ids)
    return frozenset()

