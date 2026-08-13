from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class EngineItem:
    key: str
    original: str
    masked: str
    mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class EngineCallbacks:
    should_run: Callable[[], bool]
    wait_if_paused: Callable[[], None]
    on_log: Callable[[str, str], None]  # message, color_tag
    on_status: Callable[[str], None]
    on_progress: Callable[[int], None] | None = None  # +N готовых строк
    on_metric: Callable[[str, int], None] | None = None  # (имя метрики, +N)


class TranslationEngine(ABC):
    @abstractmethod
    def translate_batch(
        self,
        items: dict[str, EngineItem],
        target_lang: dict,
        callbacks: EngineCallbacks,
    ) -> dict[str, str]:
        """Return key -> translated text (may omit keys on failure)."""
