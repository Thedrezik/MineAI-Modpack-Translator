import json
import os
import threading
from typing import Callable

from mineai.constants import CACHE_FILE_AI, CACHE_FILE_STD
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
            except (json.JSONDecodeError, OSError):
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

    def make_key(self, api_code: str, source_text: str, variant: str = "") -> str:
        namespace = f"{api_code}@g:{variant}" if variant else api_code
        return f"{namespace}_{source_text}"

    def get(self, api_code: str, source_text: str, variant: str = "") -> str | None:
        key = self.make_key(api_code, source_text, variant)
        with self._lock:
            return self._data.get(key)

    def set(
        self,
        api_code: str,
        source_text: str,
        translated: str,
        variant: str = "",
    ) -> None:
        key = self.make_key(api_code, source_text, variant)
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
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        self._dirty = False


def load_both_caches() -> tuple[TranslationCache, TranslationCache, int]:
    std = TranslationCache(CACHE_FILE_STD)
    ai = TranslationCache(CACHE_FILE_AI)
    return std, ai, std.polish_changes + ai.polish_changes
