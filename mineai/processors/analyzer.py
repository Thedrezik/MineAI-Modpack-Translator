import json
import os
import re
import zipfile

from formatkit import FormatRegistry, FormatValidationError
from formatkit.adapters.modonomicon import is_modonomicon_path
from mineai.analysis_items import (
    analysis_target_key,
    build_analysis_item,
    loose_file_scope,
)
from mineai.json_utils import load_lenient_json
from mineai.language_validation import uses_same_latin_script
from mineai.mod_names import get_mod_name
from mineai.processors.discovery import (
    discover_heracles_files,
    discover_jar_files,
    discover_loose_lang_files,
)
from mineai.processors.book_paths import (
    MarkdownBookLocator,
    legacy_lang_target_path,
    localized_json_target_path,
    minecraft_lang_json_target_path,
)
from mineai.processors.locale_keys import (
    collect_lang_keys_to_translate,
    count_translatable_lang_entries,
)
from mineai.processors.loose_paths import loose_target_disk_path
from mineai.processors.selection import (
    collect_book_json_selection,
)
from mineai.processors.quest_groups import collect_quest_groups
from mineai.processors.snbt import get_snbt_target_path
from mineai.processors.snbt_extract import merge_snbt_target
from mineai.processors.translation_state import collect_snbt_selection_with_baseline
from mineai.runtime.state import JobState
from mineai.text_processing import (
    already_translated,
    is_technical_term,
    is_translation_key,
    looks_like_source_language,
)


class ModpackAnalyzer:
    def __init__(self, state: JobState) -> None:
        self.state = state
        self.format_registry = FormatRegistry.default()

    def analyze(
        self,
        mc_dir: str,
        *,
        target_lang: dict,
        translate_mods: bool,
        translate_books: bool,
        translate_quests: bool,
        on_row,
        on_item=None,
        on_log,
        on_status,
    ) -> tuple[int, int]:
        target_file = f"{target_lang['file']}.json"
        target_regex = target_lang["regex"]
        quests_dir = os.path.join(mc_dir, "config", "ftbquests", "quests")

        total_en = 0
        total_tr = 0
        jars = (
            discover_jar_files(mc_dir)
            if translate_mods or translate_books
            else []
        )
        shared_book_locator = MarkdownBookLocator.from_archives(
            jars,
            target_lang["file"],
        )

        for index, path in enumerate(jars):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            mod_name = get_mod_name(path)
            on_status(f"Анализ: {mod_name}...", index / max(len(jars), 1))
            en, tr = self._analyze_jar(
                path,
                target_file,
                target_regex,
                translate_mods,
                translate_books,
                on_row,
                mod_name,
                on_item,
                shared_book_locator,
            )
            total_en += en
            total_tr += tr

        loose_files = []
        if translate_mods or translate_books or translate_quests:
            loose_files = [
                path
                for path in discover_loose_lang_files(mc_dir)
                if (
                    loose_file_scope(path) == "mods"
                    and translate_mods
                )
                or (
                    loose_file_scope(path) == "quests"
                    and translate_quests
                )
                or (
                    loose_file_scope(path) == "books"
                    and translate_books
                )
            ]

        for index, path in enumerate(loose_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(
                f"Анализ: {os.path.relpath(path, mc_dir)}...",
                (len(jars) + index)
                / max(len(jars) + len(loose_files), 1),
            )
            en, tr = self._analyze_loose(
                path,
                mc_dir,
                target_file,
                target_regex,
                on_row,
                on_item,
            )
            total_en += en
            total_tr += tr

        snbt_files: list[str] = []
        if os.path.isdir(quests_dir) and translate_quests:
            for root, _, files in os.walk(quests_dir):
                # Отсекаем папки других локализаций (es_es, pt_br и т.д.)
                parts = root.lower().split(os.sep)
                if "lang" in parts:
                    lang_idx = parts.index("lang")
                    if len(parts) > lang_idx + 1 and parts[lang_idx + 1] != "en_us":
                        continue

                for name in files:
                    if name.endswith(".snbt"):
                        nl = name.lower()
                        # Отсекаем файлы других локализаций в корне (ru_ru, es_es и т.д.)
                        if re.match(r"^[a-z]{2}_[a-z]{2}\.snbt$", nl) and nl != "en_us.snbt":
                            continue
                        
                        # Отсекаем огромный резервный en_us.snbt, если рядом есть папка en_us
                        if nl == "en_us.snbt" and os.path.isdir(os.path.join(root, "en_us")):
                            continue

                        snbt_files.append(os.path.join(root, name))
        
        # ПОИСК BQ
        bq_dir = os.path.join(mc_dir, "config", "betterquesting", "DefaultQuests")
        bq_files: list[str] = []
        if os.path.isdir(bq_dir) and translate_quests:
            for root, _, files in os.walk(bq_dir):
                for name in files:
                    if name.endswith(".json") and ("QuestLines" in root or "Quests" in root):
                        bq_files.append(os.path.join(root, name))
                        
        for index, path in enumerate(snbt_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(f"Анализ: {os.path.basename(path)}...", (len(jars) + index) / max(len(jars) + len(snbt_files), 1))
            en, tr = self._analyze_snbt(
                path,
                target_regex,
                on_row,
                on_item,
                target_code=target_lang["file"],
                target_lang=target_lang,
            )
            total_en += en
            total_tr += tr
        # АНАЛИЗ BQ
        for index, path in enumerate(bq_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(f"Анализ: BQ {os.path.basename(path)}...", (len(jars) + len(snbt_files) + index) / max(len(jars) + len(snbt_files) + len(bq_files), 1))
            en, tr = self._analyze_bq(
                path,
                target_regex,
                on_row,
                on_item,
            )
            total_en += en
            total_tr += tr
        heracles_files = (
            discover_heracles_files(mc_dir) if translate_quests else []
        )
        for index, path in enumerate(heracles_files):
            if not self.state.should_run():
                break
            self.state.wait_if_paused()
            on_status(
                f"Анализ: Heracles {os.path.basename(path)}...",
                index / max(len(heracles_files), 1),
            )
            en, tr = self._analyze_heracles(
                path,
                target_lang,
                on_row,
                on_item,
            )
            total_en += en
            total_tr += tr
        return total_en, total_tr

    @staticmethod
    def _emit_result(
        on_row,
        on_item,
        *,
        path,
        scope,
        icon,
        name,
        kind,
        translated,
        total,
        percent,
        parent_key=None,
        is_group=False,
    ) -> None:
        on_row(icon, name, kind, translated, total, percent)
        if on_item is not None:
            on_item(
                build_analysis_item(
                    path,
                    scope,
                    icon,
                    name,
                    kind,
                    translated,
                    total,
                    percent,
                    parent_key=parent_key,
                    is_group=is_group,
                )
            )

    def _analyze_jar(
        self,
        path,
        target_file,
        target_regex,
        translate_mods,
        translate_books,
        on_row,
        mod_name,
        on_item=None,
        book_locator=None,
    ):
        total_en = 0
        total_tr = 0
        try:
            with zipfile.ZipFile(path, "r") as zin:
                archive_items = zin.infolist()
                locale = {
                    i.filename.lower(): i
                    for i in archive_items
                }
                book_locator = book_locator or MarkdownBookLocator(
                    [item.filename for item in archive_items],
                    target_file.removesuffix(".json"),
                )
                if translate_mods:
                    en, tr = self._analyze_mods_ui(
                        zin,
                        locale,
                        target_file,
                        target_regex,
                        mod_name,
                        on_row,
                        path,
                        on_item,
                    )
                    total_en += en
                    total_tr += tr
                if translate_books:
                    en, tr = self._analyze_books(
                        zin,
                        locale,
                        target_file,
                        target_regex,
                        mod_name,
                        on_row,
                        path,
                        on_item,
                        book_locator,
                        include_companion_lang=not translate_mods,
                    )
                    total_en += en
                    total_tr += tr
        except (OSError, zipfile.BadZipFile):
            pass
        return total_en, total_tr

    def _analyze_mods_ui(
        self,
        zin,
        locale,
        target_file,
        target_regex,
        mod_name,
        on_row,
        path,
        on_item=None,
    ):
        en_c = tr_c = 0
        for item in zin.infolist():
            fl = item.filename.lower()
            legacy_target = legacy_lang_target_path(
                item.filename,
                target_file.removesuffix(".json"),
            )
            if legacy_target:
                try:
                    source_text = zin.read(item).decode(
                        "utf-8-sig",
                        errors="ignore",
                    )
                    plan = self.format_registry.plan(
                        item.filename,
                        source_text,
                        target_file.removesuffix(".json"),
                        target_path_hint=legacy_target,
                    )
                    en_c += len(plan.units)
                    target_key = legacy_target.casefold()
                    if target_key in locale:
                        target_text = zin.read(locale[target_key]).decode(
                            "utf-8-sig",
                            errors="ignore",
                        )
                        target_plan = self.format_registry.plan(
                            legacy_target,
                            target_text,
                            target_file.removesuffix(".json"),
                            target_path_hint=legacy_target,
                        )
                        _merged, pending = plan.merge_existing(
                            target_plan,
                            target_regex,
                        )
                        tr_c += len(plan.units) - len(pending)
                except (OSError, ValueError, FormatValidationError):
                    pass
                continue
            if minecraft_lang_json_target_path(
                item.filename,
                target_file.removesuffix(".json"),
            ) is None:
                continue
            try:
                en = load_lenient_json(zin.read(item))
            except (json.JSONDecodeError, OSError):
                continue
            tr_key = fl.replace("en_us.json", target_file)
            tr = {}
            if tr_key in locale:
                try:
                    tr = load_lenient_json(zin.read(locale[tr_key]))
                except (json.JSONDecodeError, OSError):
                    tr = {}
            for key, value in en.items():
                if not isinstance(value, str) or not looks_like_source_language(value) or is_technical_term(value):
                    continue
                en_c += 1
                existing = str(tr.get(key, ""))
                if existing.strip() and existing != value:
                    tr_c += 1
        if en_c:
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="mods",
                icon="📦",
                name=mod_name,
                kind="Интерфейс",
                translated=tr_c,
                total=en_c,
                percent=int(tr_c / en_c * 100),
            )
        return en_c, tr_c

    def _analyze_books(
        self,
        zin,
        locale,
        target_file,
        target_regex,
        mod_name,
        on_row,
        path,
        on_item=None,
        book_locator=None,
        include_companion_lang=True,
    ):
        b_en = b_tr = m_en = m_tr = 0
        companion_prefixes = self.format_registry.companion_lang_prefixes(
            [item.filename for item in zin.infolist()]
        )
        companion_documents: list[tuple[str, str]] = []
        for document_item in zin.infolist():
            if not is_modonomicon_path(document_item.filename):
                continue
            try:
                companion_documents.append((
                    document_item.filename,
                    zin.read(document_item).decode("utf-8-sig", errors="ignore"),
                ))
            except OSError:
                continue
        companion_keys = self.format_registry.companion_lang_keys(
            companion_documents
        )
        for item in zin.infolist():
            fl = item.filename.lower()
            is_jb = localized_json_target_path(
                item.filename,
                target_file.removesuffix(".json"),
            ) is not None
            markdown_target = (
                book_locator.target_path(item.filename)
                if book_locator is not None
                else None
            )
            is_mb = markdown_target is not None
            is_modonomicon = is_modonomicon_path(item.filename)
            upstream_adapter_id = self.format_registry.upstream_adapter_id(
                item.filename
            )
            upstream_target = self.format_registry.upstream_target_path(
                item.filename,
                target_file.removesuffix(".json"),
            )
            is_companion_lang = (
                include_companion_lang
                and (companion_prefixes or companion_keys)
                and fl.endswith("en_us.json")
                and not is_jb
            )
            if is_companion_lang:
                try:
                    source_data = load_lenient_json(zin.read(item))
                    tr_path = fl.replace("en_us.json", target_file)
                    target_data = (
                        load_lenient_json(zin.read(locale[tr_path]))
                        if tr_path in locale
                        else {}
                    )
                    source = {
                        key: value
                        for key, value in source_data.items()
                        if isinstance(key, str)
                        and isinstance(value, str)
                        and (
                            key.startswith(companion_prefixes)
                            or key in companion_keys
                        )
                    }
                    pending = collect_lang_keys_to_translate(
                        source,
                        {
                            key: value
                            for key, value in target_data.items()
                            if key in source
                        },
                        "append",
                        target_regex,
                    )
                    m_en += len(source)
                    m_tr += len(source) - len(pending)
                except (json.JSONDecodeError, OSError):
                    pass
            elif is_modonomicon:
                try:
                    source_text = zin.read(item).decode(
                        "utf-8-sig",
                        errors="ignore",
                    )
                    plan = self.format_registry.plan(
                        item.filename,
                        source_text,
                        target_file.removesuffix(".json"),
                        target_path_hint=item.filename,
                    )
                    b_en += len(plan.units)
                except (OSError, ValueError):
                    pass
            elif (
                upstream_adapter_id
                in {
                    "patchouli-book-json",
                    "oracle-index-mdx",
                    "oracle-index-meta-json",
                }
                and upstream_target
            ):
                try:
                    source_text = zin.read(item).decode(
                        "utf-8-sig",
                        errors="ignore",
                    )
                    plan = self.format_registry.plan(
                        item.filename,
                        source_text,
                        target_file.removesuffix(".json"),
                        target_path_hint=upstream_target,
                    )
                    b_en += len(plan.units)
                    target_key = upstream_target.casefold()
                    if target_key in locale:
                        target_text = zin.read(locale[target_key]).decode(
                            "utf-8-sig",
                            errors="ignore",
                        )
                        target_plan = self.format_registry.plan(
                            upstream_target,
                            target_text,
                            target_file.removesuffix(".json"),
                            target_path_hint=upstream_target,
                        )
                        _merged, pending = plan.merge_existing(
                            target_plan,
                            target_regex,
                        )
                        b_tr += len(plan.units) - len(pending)
                except (OSError, ValueError, FormatValidationError):
                    if upstream_adapter_id == "patchouli-book-json" and is_jb:
                        try:
                            en = load_lenient_json(zin.read(item))
                            tr_path = fl.replace(
                                "/en_us/",
                                f"/{target_file.replace('.json','')}/",
                            )
                            tr = (
                                load_lenient_json(zin.read(locale[tr_path]))
                                if tr_path in locale
                                else {}
                            )
                            source_map, _preserved, pending = (
                                collect_book_json_selection(
                                    en,
                                    tr,
                                    "append",
                                    {
                                        "api": target_file.split("_", 1)[0],
                                        "file": target_file.removesuffix(".json"),
                                        "regex": target_regex,
                                    },
                                )
                            )
                            b_en += len(source_map)
                            b_tr += max(0, len(source_map) - len(pending))
                        except (json.JSONDecodeError, OSError):
                            pass
            elif is_jb:
                try:
                    en = load_lenient_json(zin.read(item))
                    tr_path = fl.replace("/en_us/", f"/{target_file.replace('.json','')}/")
                    tr = load_lenient_json(zin.read(locale[tr_path])) if tr_path in locale else {}
                    source_map, _preserved, pending = collect_book_json_selection(
                        en,
                        tr,
                        "append",
                        {
                            "api": target_file.split("_", 1)[0],
                            "file": target_file.removesuffix(".json"),
                            "regex": target_regex,
                        },
                    )
                    b_en += len(source_map)
                    b_tr += max(0, len(source_map) - len(pending))
                except (json.JSONDecodeError, OSError):
                    pass
            elif is_mb:
                try:
                    en_t = zin.read(item).decode("utf-8-sig", errors="ignore")
                    plan = self.format_registry.plan(
                        item.filename,
                        en_t,
                        target_file.removesuffix(".json"),
                        target_path_hint=markdown_target,
                    )
                    tr_path = (plan.target_path or markdown_target).lower()
                    tr_t = zin.read(locale[tr_path]).decode("utf-8-sig", errors="ignore") if tr_path in locale else ""
                    m_en += len(plan.units)
                    if tr_t:
                        target_plan = self.format_registry.plan(
                            plan.target_path or markdown_target,
                            tr_t,
                            target_file.removesuffix(".json"),
                            target_path_hint=plan.target_path or markdown_target,
                        )
                        _merged, pending = plan.merge_existing(
                            target_plan,
                            target_regex,
                        )
                        m_tr += len(plan.units) - len(pending)
                except (OSError, ValueError, FormatValidationError):
                    pass
        total_en = b_en + m_en
        total_tr = b_tr + m_tr
        if total_en:
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="books",
                icon="📚",
                name=mod_name,
                kind="Книги",
                translated=total_tr,
                total=total_en,
                percent=int(total_tr / total_en * 100),
            )
        return total_en, total_tr

    def _analyze_loose(
        self,
        path,
        mc_dir,
        target_file,
        target_regex,
        on_row,
        on_item=None,
    ):
        if not path.casefold().endswith(".json"):
            return self._analyze_loose_document(
                path,
                mc_dir,
                target_file,
                target_regex,
                on_row,
                on_item,
            )
        try:
            with open(path, encoding="utf-8") as source_file:
                source = load_lenient_json(
                    source_file.read().encode("utf-8")
                )
            scope = loose_file_scope(path)
            target_path = loose_target_disk_path(
                path,
                target_file.removesuffix(".json"),
            )
            target = {}
            if os.path.exists(target_path):
                with open(target_path, encoding="utf-8") as target_handle:
                    target = load_lenient_json(
                        target_handle.read().encode("utf-8")
                    )
        except (json.JSONDecodeError, OSError):
            return 0, 0

        if scope == "books":
            source_map, _preserved, pending = collect_book_json_selection(
                source,
                target,
                "append",
                {
                    "api": target_file.split("_", 1)[0],
                    "file": target_file.removesuffix(".json"),
                    "regex": target_regex,
                },
            )
            total = len(source_map)
        else:
            total = count_translatable_lang_entries(source)
            pending = collect_lang_keys_to_translate(
                source,
                target,
                "append",
                target_regex,
            )
        translated = max(0, total - len(pending))
        if total:
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope=scope,
                icon=(
                    "📚" if scope == "books"
                    else "📜" if scope == "quests"
                    else "📦"
                ),
                name=os.path.relpath(path, mc_dir),
                kind=(
                    "Книги" if scope == "books"
                    else "Квесты" if scope == "quests"
                    else "Интерфейс"
                ),
                translated=translated,
                total=total,
                percent=int(translated / total * 100),
            )
        return total, translated

    def _analyze_loose_document(
        self,
        path,
        mc_dir,
        target_file,
        target_regex,
        on_row,
        on_item=None,
    ):
        target_code = target_file.removesuffix(".json")
        try:
            with open(path, encoding="utf-8-sig") as source_handle:
                source_text = source_handle.read()
            target_path = loose_target_disk_path(path, target_code)
            plan = self.format_registry.plan(
                path,
                source_text,
                target_code,
                target_path_hint=target_path,
            )
            pending = {unit.id for unit in plan.units}
            if os.path.exists(target_path):
                with open(target_path, encoding="utf-8-sig") as target_handle:
                    target_text = target_handle.read()
                target_plan = self.format_registry.plan(
                    target_path,
                    target_text,
                    target_code,
                    target_path_hint=target_path,
                )
                _merged, pending = plan.merge_existing(
                    target_plan,
                    target_regex,
                )
        except (OSError, ValueError, FormatValidationError):
            return 0, 0

        total = len(plan.units)
        translated = max(0, total - len(pending))
        if total:
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="books",
                icon="📚",
                name=os.path.relpath(path, mc_dir),
                kind="Книги",
                translated=translated,
                total=total,
                percent=int(translated / total * 100),
            )
        return total, translated

    def _analyze_snbt(
        self,
        path,
        target_regex,
        on_row,
        on_item=None,
        *,
        target_code="ru_ru",
        target_lang=None,
    ):
        target_path = get_snbt_target_path(path, target_code)
        separate_target = target_path != path
        source_path = path
        if not separate_target and os.path.exists(path + ".bak"):
            source_path = path + ".bak"
        try:
            with open(source_path, encoding="utf-8") as source_file:
                source_content = source_file.read()
            with open(path, encoding="utf-8") as current_file:
                current_content = current_file.read()
            if separate_target and os.path.exists(target_path):
                with open(target_path, encoding="utf-8") as target_file:
                    current_content = merge_snbt_target(
                        source_content,
                        target_file.read(),
                    )
            elif separate_target:
                current_content = source_content
        except OSError:
            return 0, 0
        language = target_lang or {
            "api": str(target_code).split("_", 1)[0],
            "file": target_code,
            "regex": target_regex,
        }
        selection = collect_snbt_selection_with_baseline(
            source_content,
            current_content,
            "append",
            target_regex,
            same_latin_script=uses_same_latin_script(language),
        )
        en_c = selection.total_translatable
        tr_c = max(0, en_c - len(selection.pending))
        if en_c:
            groups = collect_quest_groups(path, source_content)
            is_reward_table = (
                os.path.basename(os.path.dirname(path)).casefold()
                == "reward_tables"
            )
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="quests",
                icon="🎁" if is_reward_table else "📜",
                name=os.path.basename(path),
                kind="Таблица наград FTB" if is_reward_table else "Квесты",
                translated=tr_c,
                total=en_c,
                percent=int(tr_c / en_c * 100),
                is_group=bool(groups),
            )
            if groups and on_item is not None:
                parent_key = analysis_target_key(path, "quests")
                for group in groups:
                    group_nodes = tuple(
                        node
                        for node in selection.document.nodes
                        if node.metadata.get("entry_id") in group.entry_ids
                    )
                    group_sources = tuple(
                        dict.fromkeys(
                            node.source
                            for node in group_nodes
                            if node.translatable
                        )
                    )
                    group_pending = selection.document.pending_source_values(
                        "append",
                        target_regex,
                        same_latin_script=uses_same_latin_script(language),
                        nodes=group_nodes,
                    )
                    group_translated = max(0, len(group_sources) - len(group_pending))
                    on_item(
                        build_analysis_item(
                            path,
                            "quests",
                            "📘",
                            group.name,
                            "Глава FTB",
                            group_translated,
                            len(group_sources),
                            int(
                                group_translated
                                / len(group_sources)
                                * 100
                            )
                            if group_sources
                            else 0,
                            parent_key=parent_key,
                            segment=group.group_id,
                        )
                    )
        return en_c, tr_c
    
    
    def _analyze_bq(
        self,
        path: str,
        target_regex: str,
        on_row,
        on_item=None,
    ) -> tuple[int, int]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return 0, 0

        en_c = 0
        tr_c = 0
        props_key = next((k for k in data if k.startswith("properties")), None)
        if props_key and isinstance(data[props_key], dict):
            bq_key = next((k for k in data[props_key] if k.startswith("betterquesting")), None)
            if bq_key and isinstance(data[props_key][bq_key], dict):
                bq_data = data[props_key][bq_key]
                for key_prefix in ["name", "desc"]:
                    actual_key = next((k for k in bq_data if k.startswith(key_prefix)), None)
                    if actual_key and isinstance(bq_data[actual_key], str):
                        text = bq_data[actual_key].strip()
                        if text:
                            en_c += 1
                            if already_translated(text, target_regex):
                                tr_c += 1
                                
        if en_c:
            folder = os.path.basename(os.path.dirname(path))
            name = f"{folder}/{os.path.basename(path)}"
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="quests",
                icon="📜",
                name=name,
                kind="BetterQuesting",
                translated=tr_c,
                total=en_c,
                percent=int(tr_c / en_c * 100),
            )
            
        return en_c, tr_c

    def _analyze_heracles(
        self,
        path: str,
        target_lang: dict,
        on_row,
        on_item=None,
    ) -> tuple[int, int]:
        try:
            with open(path, encoding="utf-8-sig") as handle:
                text = handle.read()
            plan = self.format_registry.plan(
                path,
                text,
                target_lang["file"],
                target_path_hint=path,
            )
        except (OSError, ValueError, FormatValidationError):
            return 0, 0
        total = len(plan.units)
        translated = sum(
            1
            for unit in plan.units
            if already_translated(unit.payload, target_lang["regex"])
        )
        if total:
            normalized = path.replace("\\", "/")
            marker = "/config/heracles/"
            marker_index = normalized.casefold().find(marker)
            name = (
                "Heracles/" + normalized[marker_index + len(marker) :]
                if marker_index >= 0
                else os.path.basename(path)
            )
            self._emit_result(
                on_row,
                on_item,
                path=path,
                scope="quests",
                icon="🏛️",
                name=name,
                kind="Heracles / Odyssey",
                translated=translated,
                total=total,
                percent=int(translated / total * 100),
            )
        return total, translated
