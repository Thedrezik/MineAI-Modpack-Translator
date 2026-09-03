import os
import re
import zipfile

from mineai.constants import LOOSE_JSON_SEARCH_DIRS
from mineai.processors.book_paths import (
    MarkdownBookLocator,
    legacy_lang_target_path,
    localized_json_target_path,
    minecraft_lang_json_target_path,
)
from mineai.processors.loose_paths import is_loose_book_source


def _is_loose_locale_source(root: str, name: str) -> bool:
    path = os.path.join(root, name)
    lower_name = name.casefold()
    normalized = path.replace("\\", "/").casefold()
    if is_loose_book_source(path):
        return True
    if lower_name not in {"en_us.json", "en_us.lang"}:
        return False
    return (
        (
            lower_name == "en_us.json"
            and "/assets/" in normalized
        )
        or (
            lower_name == "en_us.json"
            and "/data/" in normalized
            and "/patchouli_books/" in normalized
        )
        or os.path.basename(root).casefold() in {"lang", "langs"}
    )


def _has_translatable_archive_source(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    locator = MarkdownBookLocator(names, "ru_ru")
    return any(
        minecraft_lang_json_target_path(name, "ru_ru") is not None
        or localized_json_target_path(name, "ru_ru") is not None
        or legacy_lang_target_path(name, "ru_ru") is not None
        or locator.target_path(name) is not None
        for name in names
    )


def discover_jar_files(mc_dir: str) -> list[str]:
    mods_dir = os.path.join(mc_dir, "mods")
    result = []
    if os.path.isdir(mods_dir):
        result.extend(
            os.path.join(mods_dir, filename)
            for filename in sorted(os.listdir(mods_dir), key=str.casefold)
            if filename.casefold().endswith(".jar")
        )

    resourcepacks_dir = os.path.join(mc_dir, "resourcepacks")
    if os.path.isdir(resourcepacks_dir):
        for filename in sorted(os.listdir(resourcepacks_dir), key=str.casefold):
            if not filename.casefold().endswith(".zip"):
                continue
            path = os.path.join(resourcepacks_dir, filename)
            if _has_translatable_archive_source(path):
                result.append(path)
    return result


def discover_loose_lang_files(mc_dir: str) -> list[str]:
    found: set[str] = set()
    for rel in LOOSE_JSON_SEARCH_DIRS:
        base = os.path.join(mc_dir, rel.replace("/", os.sep))
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for name in files:
                if _is_loose_locale_source(root, name):
                    found.add(os.path.join(root, name))
    return sorted(found, key=str.casefold)


def discover_snbt_files(mc_dir: str) -> list[str]:
    quests = os.path.join(mc_dir, "config", "ftbquests", "quests")
    if not os.path.isdir(quests):
        return []
    
    result: list[str] = []
    
    for root, _, files in os.walk(quests):
        parts = root.lower().split(os.sep)
        
        # Если мы находимся внутри папки lang
        if "lang" in parts:
            lang_idx = parts.index("lang")
            # Если мы углубились дальше lang/ (например, lang/pt_br/... или lang/en_us/...)
            if len(parts) > lang_idx + 1:
                # Разрешаем искать файлы ТОЛЬКО внутри подпапки en_us, чужие языки пропускаем
                if parts[lang_idx + 1] != "en_us":
                    continue

        for name in files:
            if name.endswith(".snbt"):
                nl = name.lower()
                # Игнорируем монолитные файлы других языков типа ru_ru.snbt, es_es.snbt (кроме en_us.snbt)
                if re.match(r"^[a-z]{2}_[a-z]{2}\.snbt$", nl) and nl != "en_us.snbt":
                    continue
                # Пропускаем монолитный en_us.snbt, если рядом есть папка en_us
                # с разбитыми квестами (иначе квесты считаются дважды: монолит + папка)
                if nl == "en_us.snbt" and os.path.isdir(os.path.join(root, "en_us")):
                    continue
                result.append(os.path.join(root, name))
    return result


def discover_bq_files(mc_dir: str) -> list[str]:
    # Путь к папке BetterQuesting
    quests_dir = os.path.join(mc_dir, "config", "betterquesting", "DefaultQuests")
    if not os.path.isdir(quests_dir):
        return []
        
    result: list[str] = []
    for root, _, files in os.walk(quests_dir):
        for name in files:
            # Нам нужны только файлы .json внутри папок QuestLines и Quests
            if name.endswith(".json") and ("QuestLines" in root or "Quests" in root):
                result.append(os.path.join(root, name))
                
    return result


def discover_heracles_files(mc_dir: str) -> list[str]:
    """Return live Heracles quest text inputs in deterministic order."""
    root = os.path.join(mc_dir, "config", "heracles")
    if not os.path.isdir(root):
        return []
    result: list[str] = []
    groups = os.path.join(root, "groups.txt")
    if os.path.isfile(groups):
        result.append(groups)
    quests = os.path.join(root, "quests")
    if os.path.isdir(quests):
        for current, _, files in os.walk(quests):
            for name in files:
                if name.casefold().endswith(".json"):
                    result.append(os.path.join(current, name))
    tutorial = os.path.join(root, "tutorial.html")
    if os.path.isfile(tutorial):
        result.append(tutorial)
    return sorted(result, key=lambda path: path.casefold())


def discover_puffish_skills_files(mc_dir: str) -> list[str]:
    """Find Puffish Skills JSON resources inside datapack data roots.

    Paxi keeps pack sources below ``config/paxi/datapacks`` while vanilla
    datapacks use ``datapacks``.  The adapter intentionally scans only these
    known data roots (plus the equivalent KubeJS/OpenLoader roots) and never
    walks live world saves or mod archives.
    """
    roots = (
        "config/paxi/datapacks",
        "datapacks",
        "config/openloader/data",
        "kubejs/data",
    )
    found: set[str] = set()
    for relative_root in roots:
        base = os.path.join(mc_dir, relative_root.replace("/", os.sep))
        if not os.path.isdir(base):
            continue
        for current, directories, files in os.walk(base):
            directories[:] = [
                name
                for name in directories
                if name.casefold() not in {"saves", "mods", "resourcepacks"}
            ]
            normalized = current.replace("\\", "/")
            parts = [part.casefold() for part in normalized.split("/")]
            try:
                data_index = parts.index("data")
            except ValueError:
                continue
            if "puffish_skills" not in parts[data_index + 1 :]:
                continue
            for name in files:
                if not name.casefold().endswith(".json"):
                    continue
                path = os.path.join(current, name)
                if os.path.isfile(path):
                    found.add(path)
    return sorted(found, key=str.casefold)
