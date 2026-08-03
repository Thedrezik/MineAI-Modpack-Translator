import time

import requests

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.text_processing import polish_translation, unmask_translation


class DeepLEngine(TranslationEngine):
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()
        self.url = (
            "https://api-free.deepl.com/v2/translate"
            if self.api_key.endswith(":fx")
            else "https://api.deepl.com/v2/translate"
        )

    def translate_batch(
        self,
        items: dict[str, EngineItem],
        target_lang: dict,
        callbacks: EngineCallbacks,
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        keys = list(items.keys())
        for i in range(0, len(keys), 40):
            callbacks.wait_if_paused()
            if not callbacks.should_run():
                break
            chunk_keys = keys[i : i + 40]
            try:
                response = requests.post(
                    self.url,
                    headers={"Authorization": f"DeepL-Auth-Key {self.api_key}"},
                    json={
                        "text": [items[k].masked for k in chunk_keys],
                        "target_lang": target_lang["deepl"],
                    },
                    timeout=60,
                )
                response.raise_for_status()
                translations = response.json()["translations"]
                for idx, key in enumerate(chunk_keys):
                    raw = translations[idx]["text"]
                    raw = unmask_translation(raw, items[key].mapping)
                    result[key] = polish_translation(raw)
            except (requests.RequestException, KeyError, IndexError) as exc:
                callbacks.on_log(f"❌ Ошибка DeepL: {exc}", "red")
            time.sleep(0.5)
        return result
