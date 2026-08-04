import logging

import requests

from mineai.constants import OPENROUTER_API
from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.http_retry import request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


logger = logging.getLogger(__name__)


class OpenRouterEngine(BatchLlmEngine):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        api_url: str = OPENROUTER_API,
        prompt_type: str = "mods",
        mode: str = "safe",
        context: str = "",
        site_url: str = "",
        app_name: str = "MineAI Translator",
        retries: int = 3,
    ) -> None:
        self.api_url = api_url.strip() or "https://openrouter.ai/api/v1/chat/completions"
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.site_url = site_url.strip()
        self.app_name = app_name.strip() or "MineAI Translator"
        self._on_log = None
        super().__init__(
            mode=mode,
            context=context,
            prompt_type=prompt_type,
            call_api=self._request,
            label="OpenRouter",
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

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    def _request(self, prompt: str, max_tokens: int) -> str | None:
        response = request_with_retry(
            lambda: requests.post(
                self.api_url,
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=300,
            ),
            operation="OpenRouter request",
            attempts=4,
            rate_limit_delays=(15.0, 30.0, 45.0),
            on_retry=lambda message: self._notify(f"⚠️ {message}", "yellow"),
        )
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("OpenRouter returned an invalid response: %s", exc)
            self._notify(f"❌ OpenRouter: некорректный ответ API ({exc})", "red")
            return None
        if not isinstance(content, str) or not content.strip():
            logger.warning("OpenRouter returned an empty response")
            self._notify("⚠️ OpenRouter вернул пустой ответ", "yellow")
            return None
        return content.strip()
