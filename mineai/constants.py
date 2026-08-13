SETTINGS_FILE = "settings.ini"
CACHE_FILE_STD = "cache.json"
CACHE_FILE_AI = "ai_cache.json"
DICT_FILE = "dictionary.json"
KOBOLD_API = "http://localhost:5001/v1/chat/completions"
KOBOLD_MODELS_URL = "http://localhost:5001/v1/models"
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_OPENROUTER_MODEL = "google/gemma-2-9b-it:free"
LMSTUDIO_BASE_URL = "http://localhost:1234/v1"

KEYS_TO_TRANSLATE = frozenset({
    "name", "title", "text", "description", "subtitle", "label", "hover_text", "link_text",
})

BOOK_PATH_MARKERS = ("patchouli", "lexicon", "guide")
MD_PATH_MARKERS = ("/en_us/", "/ae2guide/", "/guide/", "/manual/", "/lexicon/")
RESEARCH_PATH_MARKERS = ("/research/", "/researches/", "/quests/")

LOOSE_JSON_SEARCH_DIRS = (
    "kubejs",
    "defaultconfigs",
    "config",
    "resourcepacks",
    "packmenu",
    "patchouli_books",
)

IGNORE_TERMS = [
    "RF", "FE", "EU", "J", "mB", "mB/t", "RF/t", "FE/t", "AE", "kW", "kRF", "mB/tick",
    "ticks", "GUI", "UI", "HUD", "JEI", "REI", "EMI", "API", "JSON", "NBT", "FPS", "TPS",
    "HP", "XP", "MP", "XP/t", "XYZ", "RGB", "ID", "II", "III", "IV", "VI", "VII", "VIII",
    "IX", "XI", "XII",
]
IGNORE_TERMS.sort(key=len, reverse=True)

LANGUAGES = {
    "Русский": {"file": "ru_ru", "api": "ru", "deepl": "RU", "name": "Russian", "regex": r"[А-Яа-яЁё]"},
    "English (UK)": {"file": "en_gb", "api": "en", "deepl": "EN-GB", "name": "English", "regex": r"[a-zA-Z]"},
    "Español": {"file": "es_es", "api": "es", "deepl": "ES", "name": "Spanish", "regex": r"[áéíóúüñÁÉÍÓÚÜÑ]"},
    "Deutsch": {"file": "de_de", "api": "de", "deepl": "DE", "name": "German", "regex": r"[äöüßÄÖÜẞ]"},
    "Français": {"file": "fr_fr", "api": "fr", "deepl": "FR", "name": "French", "regex": r"[àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ]"},
    "中文 (Упрощ.)": {"file": "zh_cn", "api": "zh-CN", "deepl": "ZH", "name": "Simplified Chinese", "regex": r"[\u4e00-\u9fff]"},
    "日本語": {"file": "ja_jp", "api": "ja", "deepl": "JA", "name": "Japanese", "regex": r"[\u3040-\u30ff\u4e00-\u9fff]"},
    "한국어": {"file": "ko_kr", "api": "ko", "deepl": "KO", "name": "Korean", "regex": r"[\uac00-\ud7af]"},
    "Português": {"file": "pt_br", "api": "pt", "deepl": "PT-BR", "name": "Portuguese", "regex": r"[ãõáéíóúâêôÃÕÁÉÍÓÚÂÊÔ]"},
    "Italiano": {"file": "it_it", "api": "it", "deepl": "IT", "name": "Italian", "regex": r"[àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ]"},
    "Polski": {"file": "pl_pl", "api": "pl", "deepl": "PL", "name": "Polish", "regex": r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]"},
}

PACK_FORMATS = {
    "1.12.2": {"rp": 3, "dp": 1},
    "1.16.5": {"rp": 6, "dp": 6},
    "1.18.2": {"rp": 8, "dp": 9},
    "1.19.2": {"rp": 9, "dp": 10},
    "1.19.4": {"rp": 13, "dp": 12},
    "1.20.1": {"rp": 15, "dp": 15},
    "1.20.4": {"rp": 22, "dp": 26},
    "1.20.6": {"rp": 32, "dp": 41},
    "1.21.1": {"rp": 34, "dp": 48},
}

MC_VERSIONS = list(PACK_FORMATS.keys())
