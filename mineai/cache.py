import json
import os
import shutil
import threading
import unicodedata
from typing import Callable

from mineai.constants import CACHE_FILE_AI, CACHE_FILE_STD
from mineai.io_utils import atomic_write_text
from mineai.text_processing import polish_translation


_IDENTITY_PREFIX = "__mineai_identity__:"


def _normalize_cache_source(text: str) -> str:
    """Normalize representation without changing case or inner whitespace."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


class TranslationCache:
    """Thread-safe translation cache with optional auto-save and modular imports."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self._data: dict[str, str] = {}
        self._imported_data: dict[str, str] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self._last_saved_count = 0

        self.load_imported_caches()
        self.polish_changes = self.load_and_polish()

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
                        with open(path, "r", encoding="utf-8") as f:
                            loaded = json.load(f)
                            if self._is_valid_payload(loaded):
                                self._imported_data.update(loaded)
                    except (json.JSONDecodeError, OSError):
                        pass

    def load_and_polish(self) -> int:
        changes = 0
        with self._lock:
            if not os.path.exists(self.filepath):
                self._data = {}
                return 0
            try:
                with open(self.filepath, encoding="utf-8") as f:
                    loaded = json.load(f)
                if not self._is_valid_payload(loaded):
                    self._backup_corrupt_file()
                    self._data = {}
                    return 0
                self._data = loaded
                self._last_saved_count = len(self._data)
            except json.JSONDecodeError:
                self._backup_corrupt_file()
                self._data = {}
                return 0
            except OSError:
                self._data = {}
                return 0

            for key, value in list(self._data.items()):
                if key.startswith(_IDENTITY_PREFIX):
                    continue

                api_code, separator, source = key.partition("_")
                if not separator:
                    del self._data[key]
                    changes += 1
                    continue

                if api_code != "en" and value == source:
                    del self._data[key]
                    changes += 1
                    continue

                polished = polish_translation(value)
                if polished != value:
                    self._data[key] = polished
                    changes += 1
            if changes:
                self._dirty = True
                self._flush_unlocked()
        return changes

    def make_key(self, api_code: str, source_text: str) -> str:
        normalized = _normalize_cache_source(source_text)
        return f"{api_code}_{normalized}"

    def make_identity_key(self, api_code: str, source_text: str) -> str:
        return _IDENTITY_PREFIX + self.make_key(api_code, source_text)

    def get(
        self,
        api_code: str,
        source_text: str,
    ) -> tuple[str | None, bool]:
        canonical_key = self.make_key(api_code, source_text)
        legacy_key = f"{api_code}_{source_text}"
        keys = tuple(dict.fromkeys((canonical_key, legacy_key)))
        identity_key = self.make_identity_key(api_code, source_text)

        with self._lock:
            for key in keys:
                if key in self._data:
                    return self._data[key], False

            for key in keys:
                if key in self._imported_data:
                    return self._imported_data[key], True

            if identity_key in self._data:
                return source_text, False

            return None, False

    def set(self, api_code: str, source_text: str, translated: str) -> None:
        canonical_key = self.make_key(api_code, source_text)
        legacy_key = f"{api_code}_{source_text}"
        identity_key = self.make_identity_key(api_code, source_text)

        with self._lock:
            self._data.pop(identity_key, None)
            if legacy_key != canonical_key:
                self._data.pop(legacy_key, None)
            self._data[canonical_key] = translated
            self._dirty = True

    def set_identity(self, api_code: str, source_text: str) -> None:
        """Remember an intentionally unchanged technical token."""
        canonical_key = self.make_key(api_code, source_text)
        legacy_key = f"{api_code}_{source_text}"
        identity_key = self.make_identity_key(api_code, source_text)

        with self._lock:
            self._data.pop(canonical_key, None)
            self._data.pop(legacy_key, None)
            self._data[identity_key] = "1"
            self._dirty = True

    def discard(
        self,
        api_code: str,
        source_text: str,
        *,
        include_imported: bool = False,
    ) -> None:
        canonical_key = self.make_key(api_code, source_text)
        legacy_key = f"{api_code}_{source_text}"
        identity_key = self.make_identity_key(api_code, source_text)

        with self._lock:
            local_removed = False
            for key in dict.fromkeys((canonical_key, legacy_key, identity_key)):
                if key in self._data:
                    del self._data[key]
                    local_removed = True

            if include_imported:
                self._imported_data.pop(canonical_key, None)
                self._imported_data.pop(legacy_key, None)

            if local_removed:
                self._dirty = True

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
    def _is_valid_payload(payload: object) -> bool:
        return isinstance(payload, dict) and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        )

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
