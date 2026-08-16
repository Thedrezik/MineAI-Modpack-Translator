from __future__ import annotations

import json
import re
import zipfile

from mineai import formatkit_books_bridge as _books
from mineai import formatkit_bridge as _locale_bridge
from mineai.constants import BOOK_PATH_MARKERS, MD_PATH_MARKERS, RESEARCH_PATH_MARKERS
from mineai.json_utils import iter_translatable_strings, load_lenient_json
from mineai.processors.analyzer import ModpackAnalyzer as LegacyModpackAnalyzer
from mineai.processors.formatkit_pilot import (
    FormatKitJarProcessor,
    FormatKitStringEstimator,
)
from mineai.processors.selection import skip_threshold_reached
from mineai.text_processing import already_translated, is_technical_term, looks_like_source_language
from mineai_formatkit import ModonomiconBookJsonAdapter


class FormatKitBooksJarProcessor(FormatKitJarProcessor):
    """Pilot v3.3: FormatKit-owned locale/books plus Modonomicon dual-path output."""

    def process(
        self,
        jar_path: str,
        *,
        target_lang: dict,
        mode: str,
        output_mode: str,
        translate_mods: bool,
        translate_books: bool,
        pack_writer,
    ) -> None:
        # Preserve the proven v3.2 path first. Modonomicon's language-neutral
        # data JSON is an additional datapack overlay and is deliberately kept
        # out of the legacy /en_us/ book heuristics.
        super().process(
            jar_path,
            target_lang=target_lang,
            mode=mode,
            output_mode=output_mode,
            translate_mods=translate_mods,
            translate_books=translate_books,
            pack_writer=pack_writer,
        )
        if (
            not translate_books
            or not self.state.should_run()
            or output_mode != "resourcepack"
            or pack_writer is None
        ):
            return

        modono_adapter = ModonomiconBookJsonAdapter()
        try:
            with zipfile.ZipFile(jar_path, "r") as zin:
                modono_items = [item for item in zin.infolist() if modono_adapter.matches(item.filename)]
                if not modono_items:
                    return
                target_file = f"{target_lang['file']}.json"
                locale_files = {
                    item.filename.lower(): item
                    for item in zin.infolist()
                    if target_file in item.filename.lower()
                    or f"/{target_lang['file']}/" in item.filename.lower()
                }

                # Books-only mode must still translate Modonomicon's book.*
                # locale text even though normal UI translation is disabled.
                if not translate_mods:
                    for item in zin.infolist():
                        if item.filename.lower().endswith("/lang/en_us.json"):
                            self._process_modonomicon_book_locale(
                                zin, item, locale_files, target_lang, mode, pack_writer, jar_path
                            )

                for item in modono_items:
                    if not self.state.should_run():
                        break
                    self.state.wait_if_paused()
                    self._process_formatkit_book(
                        zin,
                        None,
                        item,
                        locale_files,
                        target_lang,
                        mode,
                        output_mode,
                        pack_writer,
                        jar_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1],
                        set(),
                    )
        except (OSError, zipfile.BadZipFile) as exc:
            self.callbacks.on_log(f"⚠ Modonomicon JAR пропущен {jar_path}: {exc}", "yellow")

    def _process_modonomicon_book_locale(
        self, zin, item, locale_files, target_lang, mode, pack_writer, mod_name
    ) -> bool:
        try:
            raw = zin.read(item)
            source_bom = raw.startswith(b"\xef\xbb\xbf")
            source_text = raw.decode("utf-8-sig")
            adapter = _locale_bridge.modonomicon_locale_adapter()
            preliminary = _locale_bridge.plan_locale_work(
                item.filename, source_text, target_lang["file"], None, mode,
                adapter=adapter, key_filter=lambda key: key.startswith("book."),
            )
            assert preliminary is not None
            target_text = None
            target_key = preliminary.target_path.lower()
            if mode != "force" and target_key in locale_files:
                target_text = zin.read(locale_files[target_key]).decode("utf-8-sig")
            work = _locale_bridge.plan_locale_work(
                item.filename, source_text, target_lang["file"], target_text, mode,
                adapter=adapter, key_filter=lambda key: key.startswith("book."),
            )
            assert work is not None
        except (OSError, UnicodeError, ValueError) as exc:
            self.callbacks.on_log(f"⚠ Modonomicon locale пропущена {item.filename}: {exc}", "yellow")
            return False

        if work.total_translatable == 0:
            return False
        if mode == "skip" and skip_threshold_reached(work.total_translatable, len(work.pending)):
            return False
        translated: dict[str, str] = {}
        if work.pending:
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [Modonomicon Locale/FormatKit] — {len(work.pending)} строк",
                "magenta",
            )
            translated = self.service.translate_dict(
                dict(work.pending),
                target_lang,
                self.callbacks,
                context=mod_name,
                candidate_validator=lambda unit_id, candidate: _locale_bridge.validate_locale_candidate(
                    work, unit_id, candidate
                ),
                preserve_source_structure=True,
            )
        if not self.state.should_run():
            return False
        try:
            output = _locale_bridge.build_locale_output(work, translated)
        except ValueError as exc:
            self.callbacks.on_log(f"❌ FormatKit отклонил Modonomicon locale {item.filename}: {exc}", "red")
            return False
        payload = output.encode("utf-8")
        if source_bom:
            payload = b"\xef\xbb\xbf" + payload
        pack_writer.write(work.target_path, payload)
        return True

    def _process_book_json(
        self, zin, zout, item, locale_files, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        if not _books.is_formatkit_book_path(item.filename):
            return super()._process_book_json(
                zin, zout, item, locale_files, target_lang, mode,
                output_mode, pack_writer, mod_name, written_inplace,
            )
        return self._process_formatkit_book(
            zin, zout, item, locale_files, target_lang, mode,
            output_mode, pack_writer, mod_name, written_inplace,
        )

    def _process_book_md(
        self, zin, zout, item, locale_files, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        if not _books.is_formatkit_book_path(item.filename):
            return super()._process_book_md(
                zin, zout, item, locale_files, target_lang, mode,
                output_mode, pack_writer, mod_name, written_inplace,
            )
        return self._process_formatkit_book(
            zin, zout, item, locale_files, target_lang, mode,
            output_mode, pack_writer, mod_name, written_inplace,
        )

    def _process_formatkit_book(
        self, zin, zout, item, locale_files, target_lang, mode,
        output_mode, pack_writer, mod_name, written_inplace,
    ) -> bool:
        try:
            raw_source = zin.read(item)
            source_bom = raw_source.startswith(b"\xef\xbb\xbf")
            source_text = raw_source.decode("utf-8-sig")
            tr_path = _books.target_path_for_book(item.filename, target_lang["file"])
            assert tr_path is not None
            tr_key = tr_path.lower()
            target_text = None
            if mode != "force" and tr_key in locale_files:
                try:
                    target_text = zin.read(locale_files[tr_key]).decode("utf-8-sig")
                except (OSError, UnicodeError):
                    target_text = None
            work = _books.plan_book_work(
                item.filename,
                source_text,
                target_lang["file"],
                target_lang["regex"],
                target_text,
                mode,
            )
            assert work is not None
        except (OSError, UnicodeError, ValueError) as exc:
            self.callbacks.on_log(f"⚠ FormatKit книга пропущена {item.filename}: {exc}", "yellow")
            return False

        if work.target_parse_error:
            self.callbacks.on_log(
                "⚠ FormatKit отбросил небезопасную существующую книгу "
                f"{work.target_path}: {work.target_parse_error}",
                "yellow",
            )
        emit_structural_copy = work.adapter_name == "patchouli-template-json"
        if work.total_translatable == 0 and not emit_structural_copy:
            return False

        skip_file = mode == "skip" and work.total_translatable > 0 and skip_threshold_reached(
            work.total_translatable, len(work.pending)
        )
        translated: dict[str, str] = {}
        if work.pending and not skip_file:
            if work.adapter_name == "patchouli-book-json":
                label = "Patchouli/FormatKit"
            elif work.adapter_name == "patchouli-template-json":
                label = "Patchouli Template/FormatKit"
            elif work.adapter_name == "modonomicon-book-json":
                label = "Modonomicon/FormatKit"
            else:
                label = "IE Manual/FormatKit"
            self.callbacks.on_log(
                f"⚡ Перевод {mod_name} [{label}] — {len(work.pending)} строк",
                "magenta",
            )
            translated = self.service.translate_dict(
                dict(work.pending),
                target_lang,
                self.callbacks,
                context=mod_name,
                candidate_validator=lambda unit_id, candidate: _books.validate_book_candidate(
                    work, unit_id, candidate
                ),
                preserve_source_structure=True,
            )

        if not self.state.should_run():
            return False
        try:
            output_text = _books.build_book_output(work, translated)
        except ValueError as exc:
            self.callbacks.on_log(
                f"❌ FormatKit отклонил реконструкцию книги {item.filename}: {exc}",
                "red",
            )
            return False

        payload = output_text.encode("utf-8")
        if source_bom:
            payload = b"\xef\xbb\xbf" + payload
        if output_mode == "resourcepack" and pack_writer:
            pack_writer.write(work.target_path, payload)
            return True
        if zout:
            zout.writestr(work.target_path, payload)
            written_inplace.add(work.target_path)
            return True
        return False


class FormatKitBooksStringEstimator(FormatKitStringEstimator):
    def _estimate_jar(
        self, path, target_file, target_lang, mode, translate_mods, translate_books, smart_glue
    ) -> int:
        count = super()._estimate_jar(
            path, target_file, target_lang, mode, translate_mods, translate_books, smart_glue
        )
        if not translate_books or not self.state.should_run():
            return count
        adapter = ModonomiconBookJsonAdapter()
        try:
            with zipfile.ZipFile(path, "r") as archive:
                items = [item for item in archive.infolist() if adapter.matches(item.filename)]
                if not items:
                    return count
                locale = {
                    item.filename.lower(): item
                    for item in archive.infolist()
                    if target_file in item.filename.lower()
                    or f"/{target_lang['file']}/" in item.filename.lower()
                }
                if not translate_mods:
                    for item in archive.infolist():
                        if not item.filename.lower().endswith("/lang/en_us.json"):
                            continue
                        try:
                            source = archive.read(item).decode("utf-8-sig")
                            modono_locale = _locale_bridge.modonomicon_locale_adapter()
                            preliminary = _locale_bridge.plan_locale_work(
                                item.filename, source, target_lang["file"], None, mode,
                                adapter=modono_locale, key_filter=lambda key: key.startswith("book."),
                            )
                            assert preliminary is not None
                            target_text = None
                            if mode != "force" and preliminary.target_path.lower() in locale:
                                target_text = archive.read(locale[preliminary.target_path.lower()]).decode("utf-8-sig")
                            work = _locale_bridge.plan_locale_work(
                                item.filename, source, target_lang["file"], target_text, mode,
                                adapter=modono_locale, key_filter=lambda key: key.startswith("book."),
                            )
                            assert work is not None
                            if not (mode == "skip" and skip_threshold_reached(work.total_translatable, len(work.pending))):
                                count += len(work.pending)
                        except (OSError, UnicodeError, ValueError):
                            pass
                for item in items:
                    count += self._count_formatkit_book(archive, item, locale, target_lang, mode)
        except (OSError, zipfile.BadZipFile):
            pass
        return count

    def _count_book_json(self, archive, item, locale, target_lang, mode) -> int:
        if not _books.is_formatkit_book_path(item.filename):
            return super()._count_book_json(archive, item, locale, target_lang, mode)
        return self._count_formatkit_book(archive, item, locale, target_lang, mode)

    def _count_book_md(self, archive, item, locale, target_lang, mode, smart_glue) -> int:
        if not _books.is_formatkit_book_path(item.filename):
            return super()._count_book_md(archive, item, locale, target_lang, mode, smart_glue)
        return self._count_formatkit_book(archive, item, locale, target_lang, mode)

    def _count_formatkit_book(self, archive, item, locale, target_lang, mode) -> int:
        try:
            source_text = archive.read(item).decode("utf-8-sig")
            tr_path = _books.target_path_for_book(item.filename, target_lang["file"])
            assert tr_path is not None
            target_text = None
            if (
                mode != "force"
                and not item.filename.lower().startswith("data/")
                and tr_path.lower() in locale
            ):
                try:
                    target_text = archive.read(locale[tr_path.lower()]).decode("utf-8-sig")
                except (OSError, UnicodeError):
                    target_text = None
            work = _books.plan_book_work(
                item.filename, source_text, target_lang["file"], target_lang["regex"], target_text, mode
            )
            assert work is not None
        except (OSError, UnicodeError, ValueError):
            return 0
        if mode == "skip" and skip_threshold_reached(work.total_translatable, len(work.pending)):
            return 0
        return len(work.pending)


class FormatKitModpackAnalyzer(LegacyModpackAnalyzer):
    """Analyzer counterpart using the same v3.3 plans as processor/estimator."""

    def _analyze_mods_ui(self, zin, locale, target_file, mod_name, on_row):
        en_c = tr_c = 0
        target_code = target_file[:-5]
        has_modono = any(ModonomiconBookJsonAdapter().matches(i.filename) for i in zin.infolist())
        for item in zin.infolist():
            fl = item.filename.lower()
            if not fl.endswith("en_us.json") or any(x in fl for x in BOOK_PATH_MARKERS):
                continue
            try:
                if _locale_bridge.is_formatkit_locale_path(item.filename):
                    source_text = zin.read(item).decode("utf-8-sig")
                    adapter = _locale_bridge.modonomicon_locale_adapter() if has_modono else None
                    preliminary = _locale_bridge.plan_locale_work(
                        item.filename, source_text, target_code, None, "append", adapter=adapter
                    )
                    assert preliminary is not None
                    target_text = None
                    if preliminary.target_path.lower() in locale:
                        target_text = zin.read(locale[preliminary.target_path.lower()]).decode("utf-8-sig")
                    work = _locale_bridge.plan_locale_work(
                        item.filename, source_text, target_code, target_text, "append", adapter=adapter
                    )
                    assert work is not None
                    en_c += work.total_translatable
                    tr_c += work.total_translatable - len(work.pending)
                    continue
                en = load_lenient_json(zin.read(item))
                tr_key = fl.replace("en_us.json", target_file)
                tr = load_lenient_json(zin.read(locale[tr_key])) if tr_key in locale else {}
                for key, value in en.items():
                    if not isinstance(value, str) or not looks_like_source_language(value) or is_technical_term(value):
                        continue
                    en_c += 1
                    existing = str(tr.get(key, ""))
                    if existing.strip() and existing != value:
                        tr_c += 1
            except (json.JSONDecodeError, UnicodeError, OSError, ValueError):
                continue
        if en_c:
            on_row("📦", mod_name, "Интерфейс/FormatKit", tr_c, en_c, int(tr_c / en_c * 100))
        return en_c, tr_c

    def _analyze_jar(self, path, target_file, target_regex, translate_mods, translate_books, on_row, mod_name):
        total_en, total_tr = super()._analyze_jar(
            path, target_file, target_regex, translate_mods, translate_books, on_row, mod_name
        )
        if not translate_books or translate_mods:
            return total_en, total_tr
        try:
            with zipfile.ZipFile(path, "r") as zin:
                if not any(ModonomiconBookJsonAdapter().matches(i.filename) for i in zin.infolist()):
                    return total_en, total_tr
                locale = {
                    i.filename.lower(): i
                    for i in zin.infolist()
                    if target_file in i.filename.lower() or f"/{target_file[:-5]}/" in i.filename.lower()
                }
                book_en = book_tr = 0
                for item in zin.infolist():
                    if not item.filename.lower().endswith("/lang/en_us.json"):
                        continue
                    source = zin.read(item).decode("utf-8-sig")
                    adapter = _locale_bridge.modonomicon_locale_adapter()
                    preliminary = _locale_bridge.plan_locale_work(
                        item.filename, source, target_file[:-5], None, "append",
                        adapter=adapter, key_filter=lambda key: key.startswith("book."),
                    )
                    assert preliminary is not None
                    target_text = None
                    if preliminary.target_path.lower() in locale:
                        target_text = zin.read(locale[preliminary.target_path.lower()]).decode("utf-8-sig")
                    work = _locale_bridge.plan_locale_work(
                        item.filename, source, target_file[:-5], target_text, "append",
                        adapter=adapter, key_filter=lambda key: key.startswith("book."),
                    )
                    assert work is not None
                    book_en += work.total_translatable
                    book_tr += work.total_translatable - len(work.pending)
                if book_en:
                    on_row("📘", mod_name, "Modonomicon Locale/FormatKit", book_tr, book_en, int(book_tr / book_en * 100))
                    total_en += book_en
                    total_tr += book_tr
        except (OSError, UnicodeError, zipfile.BadZipFile, ValueError):
            pass
        return total_en, total_tr

    def _analyze_books(self, zin, locale, target_file, target_regex, mod_name, on_row):
        b_en = b_tr = m_en = m_tr = 0
        target_code = target_file[:-5]
        for item in zin.infolist():
            fl = item.filename.lower()
            is_jb = (
                fl.endswith(".json")
                and "/en_us/" in fl
                and (
                    any(x in fl for x in BOOK_PATH_MARKERS)
                    or any(x in fl for x in RESEARCH_PATH_MARKERS)
                )
            )
            is_mb = (
                (fl.endswith(".md") or fl.endswith(".txt"))
                and "/en_us/" in fl
                and any(x in fl for x in MD_PATH_MARKERS)
            )
            if (is_jb or is_mb) and _books.is_formatkit_book_path(item.filename):
                try:
                    source_text = zin.read(item).decode("utf-8-sig")
                    target_path = _books.target_path_for_book(item.filename, target_code)
                    assert target_path is not None
                    target_text = None
                    if target_path.lower() in locale:
                        target_text = zin.read(locale[target_path.lower()]).decode("utf-8-sig")
                    work = _books.plan_book_work(
                        item.filename, source_text, target_code, target_regex, target_text, "append"
                    )
                    assert work is not None
                    translated = work.total_translatable - len(work.pending)
                    if work.adapter_name.startswith("patchouli-"):
                        b_en += work.total_translatable
                        b_tr += translated
                    else:
                        m_en += work.total_translatable
                        m_tr += translated
                    continue
                except (UnicodeError, OSError, ValueError):
                    continue

            if is_jb:
                try:
                    en = load_lenient_json(zin.read(item))
                    tr_path = fl.replace("/en_us/", f"/{target_code}/")
                    tr = load_lenient_json(zin.read(locale[tr_path])) if tr_path in locale else {}
                    en_s = [
                        s for _p, s in iter_translatable_strings(en)
                        if s.strip() and looks_like_source_language(s)
                    ]
                    tr_s = [s for _p, s in iter_translatable_strings(tr)] if tr else []
                    b_en += len(en_s)
                    for idx, s in enumerate(en_s):
                        if idx < len(tr_s) and tr_s[idx] != s and tr_s[idx].strip():
                            b_tr += 1
                except (json.JSONDecodeError, OSError):
                    pass
            elif is_mb:
                try:
                    en_t = zin.read(item).decode("utf-8-sig", errors="ignore")
                    tr_path = fl.replace("/en_us/", f"/{target_code}/") if "/en_us/" in fl else fl
                    tr_t = zin.read(locale[tr_path]).decode("utf-8-sig", errors="ignore") if tr_path in locale else ""
                    tr_lines = tr_t.split("\n")
                    in_yaml = False
                    for idx, line in enumerate(en_t.split("\n")):
                        if line.strip() == "---":
                            in_yaml = not in_yaml
                            continue
                        if in_yaml:
                            match = re.match(r'^(\s*title\s*:\s*[\'"]?)(.*?)([\'"]?)$', line, re.IGNORECASE)
                            if match and looks_like_source_language(match.group(2)):
                                m_en += 1
                                if idx < len(tr_lines) and already_translated(tr_lines[idx], target_regex):
                                    m_tr += 1
                            continue
                        if line.strip().startswith("<") or line.strip().startswith("!["):
                            continue
                        if line.strip() and looks_like_source_language(line) and not is_technical_term(line):
                            m_en += 1
                            if idx < len(tr_lines) and already_translated(tr_lines[idx], target_regex):
                                m_tr += 1
                except OSError:
                    pass

        modono_en = 0
        for item in zin.infolist():
            if not ModonomiconBookJsonAdapter().matches(item.filename):
                continue
            try:
                source_text = zin.read(item).decode("utf-8-sig")
                work = _books.plan_book_work(
                    item.filename, source_text, target_code, target_regex, None, "append"
                )
                if work is not None:
                    modono_en += work.total_translatable
            except (OSError, UnicodeError, ValueError):
                continue
        if modono_en:
            b_en += modono_en
            on_row("📘", mod_name, "Modonomicon/FormatKit", 0, modono_en, 0)

        if b_en:
            on_row("📖", mod_name, "Книга(JSON)", b_tr, b_en, int(b_tr / b_en * 100))
        if m_en:
            on_row("📝", mod_name, "Книга(MD)", m_tr, m_en, int(m_tr / m_en * 100))
        return b_en + m_en, b_tr + m_tr


__all__ = [
    "FormatKitBooksJarProcessor",
    "FormatKitBooksStringEstimator",
    "FormatKitModpackAnalyzer",
]
