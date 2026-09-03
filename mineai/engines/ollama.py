"""Ollama native local provider.

The translation pipeline deliberately stays in :mod:`llm_common`.  This
adapter only translates the common prompt into Ollama's native chat request
and extracts the assistant content from its response.
"""

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

OLLAMA_BASE_URL = "http://localhost:11434/api"


def normalize_ollama_base_url(base_url: str) -> str:
    """Normalize a host, ``/api`` base or endpoint into Ollama's API root."""

    value = (base_url or "").strip().rstrip("/") or OLLAMA_BASE_URL
    for suffix in ("/chat", "/generate", "/tags", "/ps"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
    if value.endswith("/v1"):
        value = value[: -len("/v1")]
    if not value.endswith("/api"):
        value += "/api"
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


def list_ollama_models(
    base_url: str,
    *,
    api_key: str = "",
    timeout: int = 10,
    session=None,
) -> list[str]:
    """Return locally installed model names from the official ``/api/tags``."""

    client = session or requests.Session()
    response = client.get(
        f"{normalize_ollama_base_url(base_url)}/tags",
        headers=_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    models = payload.get("models", []) if isinstance(payload, dict) else []
    return _unique_model_ids(
        model.get("name") or model.get("model")
        for model in models
        if isinstance(model, dict)
    )


def list_loaded_ollama_models(
    base_url: str,
    *,
    api_key: str = "",
    timeout: int = 10,
    session=None,
) -> list[str]:
    """Prefer running models (``/api/ps``), then fall back to installed ones."""

    client = session or requests.Session()
    root = normalize_ollama_base_url(base_url)
    try:
        response = client.get(
            f"{root}/ps",
            headers=_headers(api_key),
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        models = payload.get("models", []) if isinstance(payload, dict) else []
        loaded = _unique_model_ids(
            model.get("name") or model.get("model")
            for model in models
            if isinstance(model, dict)
        )
        if loaded:
            return loaded
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # Older Ollama builds may not expose /api/ps; /api/tags remains stable.
        pass
    return list_ollama_models(
        base_url,
        api_key=api_key,
        timeout=timeout,
        session=client,
    )


class OllamaEngine(BatchLlmEngine):
    """Batch translation adapter for Ollama's native ``/api/chat`` API."""

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
        self.base_url = normalize_ollama_base_url(base_url)
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
            label="Ollama",
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
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": 0.1,
                "repeat_penalty": 1.0,
                "num_predict": _bounded_output_tokens(prompt, max_tokens),
            },
        }
        response_format = _translation_response_format(prompt)
        if response_format:
            # Ollama accepts a JSON schema directly in `format`; unlike the
            # OpenAI-compatible API it does not wrap it in json_schema.
            payload["format"] = response_format["json_schema"]["schema"]
        try:
            response = request_with_retry(
                lambda: self.session.post(
                    f"{self.base_url}/chat",
                    headers=_headers(self.api_key),
                    json=payload,
                    timeout=300,
                ),
                operation="Ollama",
                on_log=active_log,
                should_continue=self._should_continue,
            )
        except RequestCancelled:
            raise
        except requests.RequestException as exc:
            if active_log:
                active_log(f"❌ Ollama сеть: {exc}", "red")
            return None

        try:
            response_payload = response.json()
            message = response_payload["message"]
            content = message["content"]
            done_reason = response_payload.get("done_reason")
            if done_reason in {"length", "max_tokens"}:
                if active_log:
                    active_log(
                        "⚠️ Ollama: ответ обрезан по num_predict; пакет "
                        "будет повторён меньшими частями",
                        "yellow",
                    )
                return None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.error("Ollama invalid JSON: %s", exc)
            if active_log:
                active_log(f"❌ Ollama: неверный JSON ответа: {exc}", "red")
            return None

        if not isinstance(content, str) or not content.strip():
            if active_log:
                active_log("⚠️ Ollama вернул пустой ответ", "yellow")
            return None
        return content.strip()
