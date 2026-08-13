import logging

import json
import requests

from mineai.constants import LMSTUDIO_BASE_URL
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine


logger = logging.getLogger(__name__)


def _translation_response_format(prompt: str) -> dict | None:
    marker = "\nDATA:\n"
    if marker not in prompt:
        return None
    try:
        data = json.loads(prompt.rsplit(marker, 1)[1])
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict) or not all(
        isinstance(key, str) for key in data
    ):
        return None
    properties = {key: {"type": "string"} for key in data}
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "translations",
            "schema": {
                "type": "object",
                "properties": properties,
                "required": list(properties),
                "additionalProperties": False,
            },
        },
    }


def normalize_lmstudio_base_url(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/") or LMSTUDIO_BASE_URL
    if value.endswith("/chat/completions"):
        value = value[: -len("/chat/completions")]
    if not value.endswith("/v1"):
        value += "/v1"
    return value


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (api_key or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def list_lmstudio_models(
    base_url: str,
    *,
    api_key: str = "",
    timeout: int = 10,
    session=None,
) -> list[str]:
    client = session or requests.Session()
    response = client.get(
        f"{normalize_lmstudio_base_url(base_url)}/models",
        headers=_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return [
        item["id"].strip()
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].strip()
    ]


class LmStudioEngine(BatchLlmEngine):
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        prompt_type: str = "mods",
        mode: str = "safe",
        context: str = "",
        retries: int = 3,
        session=None,
    ) -> None:
        self.base_url = normalize_lmstudio_base_url(base_url)
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.session = session or requests.Session()
        self._should_continue = None
        self._on_log = None
        super().__init__(
            mode=mode,
            context=context,
            prompt_type=prompt_type,
            call_api=self._request,
            label="LM Studio",
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
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "repeat_penalty": 1.0,
            "max_tokens": max_tokens,
            "stream": False,
        }
        response_format = _translation_response_format(prompt)
        if response_format:
            payload["response_format"] = response_format
        try:
            response = request_with_retry(
                lambda: self.session.post(
                    f"{self.base_url}/chat/completions",
                    headers=_headers(self.api_key),
                    json=payload,
                    timeout=300,
                ),
                operation="LM Studio",
                on_log=active_log,
                should_continue=self._should_continue,
            )
        except RequestCancelled:
            raise
        except requests.RequestException as exc:
            if active_log:
                active_log(f"❌ LM Studio сеть: {exc}", "red")
            return None

        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("LM Studio invalid JSON: %s", exc)
            if active_log:
                active_log(f"❌ LM Studio: неверный JSON ответа: {exc}", "red")
            return None

        if not isinstance(content, str) or not content.strip():
            if active_log:
                active_log("⚠️ LM Studio вернул пустой ответ", "yellow")
            return None
        return content.strip()
