"""OpenAI-compatible adapter for ``llama serve`` from llama.app."""

from __future__ import annotations

import json
import logging

import requests

from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.engines.llm_common import BatchLlmEngine
from mineai.engines.lmstudio import (
    _bounded_output_tokens,
    _translation_response_format,
)


logger = logging.getLogger(__name__)

LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"


def normalize_llama_base_url(base_url: str) -> str:
    """Normalize a llama.cpp server host or endpoint to its ``/v1`` root."""

    value = (base_url or "").strip().rstrip("/") or LLAMA_BASE_URL
    for suffix in ("/chat/completions", "/models"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    if not value.endswith("/v1"):
        value += "/v1"
    return value


def _headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = (api_key or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _unique_model_ids(values) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        model_id = value.strip()
        if model_id and model_id not in seen:
            seen.add(model_id)
            result.append(model_id)
    return result


def list_llama_models(
    base_url: str,
    *,
    api_key: str = "",
    timeout: int = 10,
    session=None,
) -> list[str]:
    """Return model IDs exposed by llama.cpp's ``GET /v1/models``."""

    client = session or requests.Session()
    response = client.get(
        f"{normalize_llama_base_url(base_url)}/models",
        headers=_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return _unique_model_ids(
        item.get("id")
        for item in data
        if isinstance(item, dict)
    )


class LlamaEngine(BatchLlmEngine):
    """Batch translation adapter for llama.cpp's OpenAI-compatible API."""

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
        self.base_url = normalize_llama_base_url(base_url)
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
            label="Llama",
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
            "max_tokens": _bounded_output_tokens(prompt, max_tokens),
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
                operation="Llama",
                on_log=active_log,
                should_continue=self._should_continue,
            )
        except RequestCancelled:
            raise
        except requests.RequestException as exc:
            if active_log:
                active_log(f"❌ Llama сеть: {exc}", "red")
            return None

        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason in {"length", "max_tokens"}:
                if active_log:
                    active_log(
                        "⚠️ Llama: ответ обрезан по max_tokens; пакет "
                        "будет повторён меньшими частями",
                        "yellow",
                    )
                return None
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Llama invalid JSON: %s", exc)
            if active_log:
                active_log(f"❌ Llama: неверный JSON ответа: {exc}", "red")
            return None

        if not isinstance(content, str) or not content.strip():
            if active_log:
                active_log("⚠️ Llama вернул пустой ответ", "yellow")
            return None
        return content.strip()
