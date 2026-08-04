import logging

import requests

from mineai.constants import KOBOLD_API
from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.http_retry import request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


logger = logging.getLogger(__name__)


class KoboldEngine(BatchLlmEngine):
    def __init__(
        self,
        mode: str = "safe",
        context: str = "",
        prompt_type: str = "mods",
        retries: int = 3,
    ) -> None:
        self._on_log = None
        super().__init__(
            mode=mode,
            context=context,
            prompt_type=prompt_type,
            call_api=self._request,
            label="KoboldCPP",
            retries=retries,
        )

    def translate_batch(self, items: dict[str, EngineItem], target_lang: dict, callbacks: EngineCallbacks) -> dict[str, str]:
        self._on_log = callbacks.on_log
        try:
            return super().translate_batch(items, target_lang, callbacks)
        finally:
            self._on_log = None

    def _notify(self, message: str, tag: str) -> None:
        if self._on_log is not None:
            self._on_log(message, tag)

    def _request(self, prompt: str, max_tokens: int) -> str | None:
        response = request_with_retry(
            lambda: requests.post(
                KOBOLD_API,
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=300,
            ),
            operation="KoboldCPP request",
            on_retry=lambda message: self._notify(f"⚠️ {message}", "yellow"),
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("KoboldCPP returned an invalid response: %s", exc)
            self._notify(f"❌ KoboldCPP: некорректный ответ API ({exc})", "red")
            return None
        if not isinstance(content, str) or not content.strip():
            logger.warning("KoboldCPP returned an empty response")
            self._notify("⚠️ KoboldCPP вернул пустой ответ", "yellow")
            return None
        return content.strip()
