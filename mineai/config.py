import configparser
import io
from mineai.constants import SETTINGS_FILE
from mineai.io_utils import atomic_write_text


class ConfigManager:
    """Persistent application settings (settings.ini)."""

    _DEFAULTS = {
        "GENERAL": {
            "mc_dir": "",
            "theme": "Dark",
            "ui_language": "ru",
            "minecraft_version": "1.20.1",
            "target_language": "Русский",
            "translation_engine": "Google",
            "color": "blue",
            "smart_glue": "True",
            "google_workers": "5",
            "cache_recovery_mode": "False",
        },
        "AI": {
            "exe_path": "koboldcpp.exe",
            "model_path": "",
            "gpu_layers": "99",
            "ai_provider": "local",
            "ai_retries": "3",
            "fallback_google": "False",
            "ai_batch": "20",
        },
        "API": {
            "deepl_key": "",
        },
        "OPENROUTER": {
            "api_url": "https://openrouter.ai/api/v1/chat/completions",
            "api_key": "",
            "model": "google/gemma-2-9b-it:free",
            "site_url": "",
            "app_name": "MineAI Translator",
        },
        "LMSTUDIO": {
            "base_url": "http://localhost:1234/v1",
            "api_key": "",
            "model": "",
        },
        "OLLAMA": {
            "base_url": "http://localhost:11434/api",
            "api_key": "",
            "model": "",
        },
        "LLAMA": {
            "base_url": "http://127.0.0.1:8080/v1",
            "api_key": "",
            "model": "",
        },
    }

    def __init__(self) -> None:
        self._config = configparser.ConfigParser()
        self.load()

    def load(self) -> None:
        self._config.read(SETTINGS_FILE, encoding="utf-8")
        changed = False
        for section, keys in self._DEFAULTS.items():
            if not self._config.has_section(section):
                self._config.add_section(section)
                changed = True
            for key, value in keys.items():
                if not self._config.has_option(section, key):
                    self._config.set(section, key, str(value))
                    changed = True
        if changed:
            self.save()

    def save(self) -> None:
        buffer = io.StringIO()
        self._config.write(buffer)
        atomic_write_text(SETTINGS_FILE, buffer.getvalue())

    def get(self, section: str, key: str) -> str:
        return self._config.get(section, key)

    def set(self, section: str, key: str, value) -> None:
        self._config.set(section, key, str(value))
        self.save()

    def set_many(self, section: str, values: dict[str, object]) -> None:
        """Persist related values with one atomic settings write."""
        if not self._config.has_section(section):
            self._config.add_section(section)
        for key, value in values.items():
            self._config.set(section, key, str(value))
        self.save()

    def getboolean(self, section: str, key: str) -> bool:
        return self._config.getboolean(section, key)

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        raw = self.get(section, key)
        return int(raw) if raw.isdigit() else fallback


# Shared singleton for GUI and jobs
settings = ConfigManager()
