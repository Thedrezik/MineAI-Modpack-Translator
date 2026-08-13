import logging
import requests
from mineai.constants import OPENROUTER_API
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine

logger = logging.getLogger(__name__)

class OpenRouterEngine(BatchLlmEngine):
    def __init__(
        self, api_key: str, model: str, *, api_url: str = OPENROUTER_API,
        prompt_type: str = "mods", mode: str = "safe", context: str = "",
        site_url: str = "", app_name: str = "MineAI Translator", retries: int = 3,
    ) -> None:
        self.api_url = api_url.strip() or "https://openrouter.ai/api/v1/chat/completions"
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.site_url = site_url.strip()
        self.app_name = app_name.strip() or "MineAI Translator"
        self._should_continue = None
        self._on_log = None
        super().__init__(
            mode=mode, context=context, prompt_type=prompt_type,
            call_api=self._request, label="OpenRouter", retries=retries,
        )

    def translate_batch(self, items, target_lang, callbacks):
        self._should_continue = callbacks.should_run
        self._on_log = callbacks.on_log
        try:
            return super().translate_batch(items, target_lang, callbacks)
        except RequestCancelled:
            return {}
        finally:
            self._should_continue = None
            self._on_log = None

    def _headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.site_url: headers["HTTP-Referer"] = self.site_url
        if self.app_name: headers["X-Title"] = self.app_name
        return headers

    def _request(self, prompt: str, max_tokens: int, on_log=None) -> str | None:
        active_log = on_log or self._on_log

        def openrouter_delay(attempt: int, exc: Exception) -> float:
            if isinstance(exc, requests.HTTPError) and exc.response is not None and exc.response.status_code == 429:
                return 15.0 * attempt
            return 4.0 * attempt

        try:
            response = request_with_retry(
                lambda: requests.post(
                    self.api_url, headers=self._headers(),
                    json={"model": self.model, "messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.1, "repetition_penalty": 1.0, "max_tokens": max_tokens},
                    timeout=300,
                ),
                operation="OpenRouter",
                attempts=4,
                on_log=active_log,
                delay_func=openrouter_delay,
                should_continue=self._should_continue,
            )
        except RequestCancelled:
            raise
        except requests.RequestException as exc:
            if active_log: active_log(f"❌ OpenRouter сеть: {exc}", "red")
            return None

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("OpenRouter invalid JSON: %s", exc)
            if active_log: active_log(f"❌ OpenRouter: неверный JSON ответа: {exc}", "red")
            return None

        if not isinstance(content, str) or not content.strip():
            if active_log: active_log("⚠️ OpenRouter вернул пустой ответ (фильтр модели)", "yellow")
            return None

        return content.strip()
