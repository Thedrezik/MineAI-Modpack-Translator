import requests

from mineai.constants import KOBOLD_API
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


class KoboldEngine(BatchLlmEngine):
    def __init__(self, mode: str = "safe", context: str = "", prompt_type: str = "mods", retries: int = 3) -> None:
        self._should_continue = None
        super().__init__(
            mode=mode,
            context=context,
            prompt_type=prompt_type,
            call_api=self._request,
            label="KoboldCPP",
            retries=retries,
        )

    def translate_batch(self, items, target_lang, callbacks):
        self._should_continue = callbacks.should_run
        try:
            return super().translate_batch(items, target_lang, callbacks)
        finally:
            self._should_continue = None

    def _request(self, prompt: str, max_tokens: int, on_log=None) -> str | None:
        try:
            response = request_with_retry(
                lambda: requests.post(
                    KOBOLD_API,
                    json={
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                        "repetition_penalty": 1.0,
                        "max_tokens": max_tokens,
                    },
                    timeout=300,
                ),
                operation="KoboldCPP",
                on_log=on_log,
                should_continue=self._should_continue,
            )
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                return None
            return content.strip()
        except RequestCancelled:
            return None
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError):
            return None