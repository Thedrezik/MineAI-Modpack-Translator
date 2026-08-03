import json
import os
import shutil
import threading
from typing import Callable

from mineai.constants import CACHE_FILE_AI, CACHE_FILE_STD
from mineai.io_utils import atomic_write_text
from mineai.text_processing import polish_translation


class TranslationCache:
    """Thread-safe translation cache with optional auto-save."""

    def __init__(self, filepath: str) -> None:
        self.filepath = filepath
        self._data: dict[str, str] = {}
        self._lock = threading.RLock()
        self._dirty = False
        self.polish_changes = self.load_and_polish()

    def load_and_polish(self) -> int:
        changes = 0
        with self._lock:
            if not os.path.exists(self.filepath):
                self._data = {}
                return 0
            try:
                with open(self.filepath, encoding="utf-8") as f:
                    self._data = json.load(f)
            except json.JSONDecodeError:
                self._backup_corrupt_file()
                self._data = {}
                return 0
            except OSError:
                self._data = {}
                return 0

            for key, value in list(self._data.items()):
                polished = polish_translation(value)
                if polished != value:
                    self._data[key] = polished
                    changes += 1
            if changes:
                self._dirty = True
                self._flush_unlocked()
        return changes

    def make_key(self, api_code: str, source_text: str) -> str:
        return f"{api_code}_{source_text}"

    def get(self, api_code: str, source_text: str) -> str | None:
        key = self.make_key(api_code, source_text)
        with self._lock:
            return self._data.get(key)

    def set(self, api_code: str, source_text: str, translated: str) -> None:
        key = self.make_key(api_code, source_text)
        with self._lock:
            self._data[key] = translated
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
            if self._dirty and len(self._data) % every == 0:
                self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        payload = json.dumps(self._data, ensure_ascii=False, indent=2)
        atomic_write_text(self.filepath, payload)
        self._dirty = False

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
