import requests

from mineai.constants import KOBOLD_API
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


class KoboldEngine(BatchLlmEngine):
    def __init__(self, mode: str = "safe", context: str = "", prompt_type: str = "mods", retries: int = 3, *, session=None) -> None:
        self.session = session or requests.Session()
        self._should_continue = None
        self._on_log = None
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
        self._on_log = callbacks.on_log
        try:
            return super().translate_batch(items, target_lang, callbacks)
        except RequestCancelled:
            return {}
        finally:
            self._should_continue = None
            self._on_log = None

    def _request(self, prompt: str, max_tokens: int, on_log=None) -> str | None:
        active_log = on_log or self._on_log
        try:
            response = request_with_retry(
                lambda: self.session.post(
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
                on_log=active_log,
                should_continue=self._should_continue,
            )
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                if active_log: active_log("⚠️ KoboldCPP вернул пустой ответ", "yellow")
                return None
            return content.strip()
        except RequestCancelled:
            raise
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            if active_log: active_log(f"❌ KoboldCPP: {exc}", "red")
            return None
