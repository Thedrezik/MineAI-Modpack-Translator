import json
import os
import shutil
import threading
import unicodedata
from mineai.constants import CACHE_FILE_AI, CACHE_FILE_STD, LANGUAGES
from mineai.io_utils import atomic_write_text
from mineai.language_validation import has_long_untranslated_english_fragment
from mineai.text_processing import (
    is_technical_term,
    polish_translation,
    translation_length_issue,
)

_IDENTITY_PREFIX = "__mineai_identity__:"
_CACHE_VERSION_KEY = "__mineai_ai_cache_validation_version__"
_CACHE_VALIDATION_VERSION = "29"
_LANGUAGE_BY_API = {item["api"]: item for item in LANGUAGES.values()}


def _normalize_cache_source(text: str) -> str:
    """Normalize line-endings and Unicode without changing semantics."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


class TranslationCache:
    """Thread-safe translation cache with identity support."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self._data: dict[str, str] = {}
        self._imported_data: dict[str, str] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_saved_count = 0
        self.load_imported_caches()
        self.polish_changes = self.load_and_polish()

    def _is_ai_cache(self) -> bool:
        return os.path.basename(self.filepath).casefold() == "ai_cache.json"

    # ------------------------------------------------------------------
    def load_imported_caches(self) -> None:
        with self._lock:
            self._imported_data.clear()
            cache_name = os.path.basename(self.filepath)
            subfolder = "ai" if "ai" in cache_name else "std"
            import_dir = os.path.join(os.getcwd(), "imported_caches", subfolder)
            if not os.path.exists(import_dir):
                return
            for filename in os.listdir(import_dir):
                if filename.endswith(".json"):
                    path = os.path.join(import_dir, filename)
                    try:
                        with open(path, "r", encoding="utf-8-sig") as f:
                            loaded = self._coerce_payload(json.load(f))
                        if loaded is not None:
                            loaded.pop(_CACHE_VERSION_KEY, None)
                            self._imported_data.update(loaded)
                    except (json.JSONDecodeError, OSError):
                        pass

    # ------------------------------------------------------------------
    def load_and_polish(self) -> int:
        changes = 0
        with self._lock:
            if not os.path.exists(self.filepath):
                self._data = (
                    {_CACHE_VERSION_KEY: _CACHE_VALIDATION_VERSION}
                    if self._is_ai_cache()
                    else {}
                )
                return 0
            try:
                with open(self.filepath, encoding="utf-8-sig") as f:
                    loaded = self._coerce_payload(json.load(f))
                if loaded is None:
                    self._backup_corrupt_file()
                    self._reset_corrupt_ai_cache_unlocked()
                    return 0
                self._data = loaded
                self._last_saved_count = len(self._data)
            except json.JSONDecodeError:
                self._backup_corrupt_file()
                self._reset_corrupt_ai_cache_unlocked()
                return 0
            except OSError:
                self._data = {}
                return 0


            if (
                self._is_ai_cache()
                and self._data.get(_CACHE_VERSION_KEY) != _CACHE_VALIDATION_VERSION
            ):
                self._backup_before_auto_repair()
                self._data[_CACHE_VERSION_KEY] = _CACHE_VALIDATION_VERSION
                changes += 1
                self._dirty = True

            for key, value in list(self._data.items()):
                if key == _CACHE_VERSION_KEY:
                    continue
                if key.startswith(_IDENTITY_PREFIX):
                    continue
                api_code, sep, source = key.partition("_")
                if not sep:
                    self._backup_before_auto_repair()
                    del self._data[key]
                    changes += 1
                    continue
                source_payload = source.rsplit("␟", 1)[-1]
                language = _LANGUAGE_BY_API.get(api_code)
                if not value.strip():
                    self._backup_before_auto_repair()
                    del self._data[key]
                    changes += 1
                    continue
                if api_code != "en" and value.strip() == source_payload.strip():
                    self._backup_before_auto_repair()
                    del self._data[key]
                    if is_technical_term(source_payload):
                        self._data[self.make_identity_key(api_code, source)] = "1"
                    changes += 1
                    continue
                if (
                    language is not None
                    and has_long_untranslated_english_fragment(value, language)
                ):
                    self._backup_before_auto_repair()
                    del self._data[key]
                    changes += 1
                    continue
                if translation_length_issue(source_payload, value):
                    self._backup_before_auto_repair()
                    del self._data[key]
                    changes += 1
                    continue
                polished = polish_translation(
                    value,
                    boundary_source=source_payload,
                )
                if polished != value:
                    self._backup_before_auto_repair()
                    self._data[key] = polished
                    changes += 1
            if changes:
                self._dirty = True
                self._flush_unlocked()
        return changes

    def _reset_corrupt_ai_cache_unlocked(self) -> None:
        self._data = (
            {_CACHE_VERSION_KEY: _CACHE_VALIDATION_VERSION}
            if self._is_ai_cache()
            else {}
        )
        if self._is_ai_cache():
            self._dirty = True
            self._flush_unlocked()

    def _backup_before_auto_repair(self) -> None:
        backup = self.filepath + ".pre-auto-repair"
        if os.path.exists(backup):
            return
        try:
            shutil.copy2(self.filepath, backup)
        except OSError:
            pass

    # ------------------------------------------------------------------
    def make_key(self, api_code: str, source_text: str) -> str:
        return f"{api_code}_{_normalize_cache_source(source_text)}"

    def make_identity_key(self, api_code: str, source_text: str) -> str:
        return _IDENTITY_PREFIX + self.make_key(api_code, source_text)

    def get(self, api_code: str, source_text: str) -> tuple[str | None, bool]:
        canonical = self.make_key(api_code, source_text)
        legacy = f"{api_code}_{source_text}"
        identity = self.make_identity_key(api_code, source_text)
        with self._lock:
            for key in dict.fromkeys((canonical, legacy)):
                if key in self._data:
                    return self._data[key], False
                if key in self._imported_data:
                    return self._imported_data[key], True
            if identity in self._data:
                return source_text, False
            return None, False

    def set(self, api_code: str, source_text: str, translated: str) -> None:
        canonical = self.make_key(api_code, source_text)
        legacy = f"{api_code}_{source_text}"
        identity = self.make_identity_key(api_code, source_text)
        with self._lock:
            self._data.pop(identity, None)
            if legacy != canonical:
                self._data.pop(legacy, None)
            self._data[canonical] = translated
            self._dirty = True

    def set_identity(self, api_code: str, source_text: str) -> None:
        canonical = self.make_key(api_code, source_text)
        legacy = f"{api_code}_{source_text}"
        identity = self.make_identity_key(api_code, source_text)
        with self._lock:
            self._data.pop(canonical, None)
            self._data.pop(legacy, None)
            self._data[identity] = "1"
            self._dirty = True

    def discard(
        self,
        api_code: str,
        source_text: str,
        *,
        include_imported: bool = False,
    ) -> None:
        canonical = self.make_key(api_code, source_text)
        legacy = f"{api_code}_{source_text}"
        identity = self.make_identity_key(api_code, source_text)
        with self._lock:
            removed = False
            for key in dict.fromkeys((canonical, legacy, identity)):
                if key in self._data:
                    del self._data[key]
                    removed = True
            if include_imported:
                self._imported_data.pop(canonical, None)
                self._imported_data.pop(legacy, None)
            if removed:
                self._dirty = True

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def save(self) -> None:
        with self._lock:
            if self._dirty:
                self._flush_unlocked()

    def save_if_threshold(self, every: int = 500) -> None:
        with self._lock:
            if self._dirty and (len(self._data) - self._last_saved_count) >= every:
                self._flush_unlocked()
                self._last_saved_count = len(self._data)

    def _flush_unlocked(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        atomic_write_text(self.filepath, payload)
        self._dirty = False
        self._last_saved_count = len(self._data)

    @staticmethod
    def _coerce_payload(payload: object) -> dict[str, str] | None:
        if not isinstance(payload, dict):
            return None
        result: dict[str, str] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                return None
            if key == _CACHE_VERSION_KEY and isinstance(value, (int, float)):
                value = str(value)
            if not isinstance(value, str):
                return None
            result[key] = value
        return result

    @classmethod
    def _is_valid_payload(cls, payload: object) -> bool:
        return cls._coerce_payload(payload) is not None

    def _backup_corrupt_file(self) -> None:
        backup = self.filepath + ".corrupt"
        counter = 1
        while os.path.exists(backup):
            backup = f"{self.filepath}.corrupt.{counter}"
            counter += 1
        try:
            shutil.copy2(self.filepath, backup)
        except OSError:
            pass


def load_both_caches() -> tuple[TranslationCache, TranslationCache, int]:
    std = TranslationCache(CACHE_FILE_STD)
    ai = TranslationCache(CACHE_FILE_AI)
    return std, ai, std.polish_changes + ai.polish_changes
