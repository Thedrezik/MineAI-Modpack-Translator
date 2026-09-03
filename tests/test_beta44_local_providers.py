"""Beta44 regression tests for Ollama and llama.cpp local providers."""

from __future__ import annotations

import unittest
from unittest import mock

import requests

from mineai.cache import TranslationCache
from mineai.config import ConfigManager
from mineai.engines.base import EngineCallbacks
from mineai.engines.llama import (
    LlamaEngine,
    list_llama_models,
    normalize_llama_base_url,
)
from mineai.engines.ollama import (
    OllamaEngine,
    list_loaded_ollama_models,
    list_ollama_models,
    normalize_ollama_base_url,
)
from mineai.engines.service import TranslationService
from mineai.gui_qt.view_model import engine_readiness


class _Response:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    def __init__(self, *, get_payload=None, post_payload=None):
        self.get_payload = get_payload or {"data": []}
        self.post_payload = post_payload or {
            "message": {"role": "assistant", "content": "translated"},
            "done": True,
        }
        self.get_calls = []
        self.post_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return _Response(self.get_payload)

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return _Response(self.post_payload)


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, section, key):
        return self.values.get((section, key), "")

    def getint(self, section, key, fallback=0):
        raw = self.get(section, key)
        return int(raw) if str(raw).isdigit() else fallback

    def getboolean(self, section, key):
        return str(self.get(section, key)).casefold() == "true"


def _callbacks():
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda *_args: None,
        on_status=lambda *_args: None,
    )


class Beta44ProviderApiTests(unittest.TestCase):
    def test_ollama_url_normalization(self):
        self.assertEqual(normalize_ollama_base_url(""), "http://localhost:11434/api")
        self.assertEqual(
            normalize_ollama_base_url("http://127.0.0.1:11434"),
            "http://127.0.0.1:11434/api",
        )
        self.assertEqual(
            normalize_ollama_base_url("http://localhost:11434/api/chat"),
            "http://localhost:11434/api",
        )

    def test_ollama_tags_list_parses_name_and_deduplicates(self):
        session = _Session(
            get_payload={
                "models": [
                    {"name": "qwen3:8b"},
                    {"model": "llama3.2:latest"},
                    {"name": "qwen3:8b"},
                    {"name": ""},
                ]
            }
        )
        self.assertEqual(
            list_ollama_models("http://localhost:11434/api", session=session),
            ["qwen3:8b", "llama3.2:latest"],
        )
        self.assertEqual(session.get_calls[0][0], "http://localhost:11434/api/tags")

    def test_ollama_loaded_probe_prefers_ps(self):
        session = _Session(get_payload={"models": [{"name": "qwen3:8b"}]})
        self.assertEqual(
            list_loaded_ollama_models("http://localhost:11434/api", session=session),
            ["qwen3:8b"],
        )
        self.assertEqual(session.get_calls[0][0], "http://localhost:11434/api/ps")
        self.assertEqual(len(session.get_calls), 1)

    def test_ollama_loaded_probe_falls_back_to_tags_when_ps_is_empty(self):
        class _PsThenTags(_Session):
            def get(self, url, **kwargs):
                self.get_calls.append((url, kwargs))
                if url.endswith("/ps"):
                    return _Response({"models": []})
                return _Response({"models": [{"name": "gemma4"}]})

        session = _PsThenTags()
        self.assertEqual(
            list_loaded_ollama_models("http://localhost:11434/api", session=session),
            ["gemma4"],
        )
        self.assertEqual(
            [url for url, _kwargs in session.get_calls],
            [
                "http://localhost:11434/api/ps",
                "http://localhost:11434/api/tags",
            ],
        )

    def test_ollama_chat_is_non_streaming_and_uses_common_schema(self):
        session = _Session()
        engine = OllamaEngine("http://localhost:11434", "qwen3:8b", session=session)
        result = engine._request(
            'Rules\n\nDATA:\n{"0": "Text"}',
            4096,
        )
        self.assertEqual(result, "translated")
        url, options = session.post_calls[0]
        self.assertEqual(url, "http://localhost:11434/api/chat")
        payload = options["json"]
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["model"], "qwen3:8b")
        self.assertIn("format", payload)
        self.assertEqual(payload["format"]["type"], "object")
        self.assertLess(payload["options"]["num_predict"], 4096)

    def test_llama_url_and_models_follow_openai_compatible_api(self):
        self.assertEqual(normalize_llama_base_url(""), "http://127.0.0.1:8080/v1")
        self.assertEqual(
            normalize_llama_base_url("http://localhost:8080/v1/chat/completions"),
            "http://localhost:8080/v1",
        )
        session = _Session(
            get_payload={
                "data": [
                    {"id": "model.gguf"},
                    {"id": "model.gguf"},
                    {"id": ""},
                ]
            }
        )
        self.assertEqual(
            list_llama_models("http://localhost:8080", session=session),
            ["model.gguf"],
        )
        self.assertEqual(session.get_calls[0][0], "http://localhost:8080/v1/models")

    def test_llama_chat_uses_common_clean_transport_and_no_stream(self):
        session = _Session(
            post_payload={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "translated"},
                    }
                ]
            }
        )
        engine = LlamaEngine("http://localhost:8080", "model.gguf", session=session)
        self.assertEqual(engine._request("plain prompt", 120), "translated")
        url, options = session.post_calls[0]
        self.assertEqual(url, "http://localhost:8080/v1/chat/completions")
        self.assertFalse(options["json"]["stream"])
        self.assertEqual(options["json"]["model"], "model.gguf")

    def test_service_builds_both_providers_and_reuses_session(self):
        for provider, section, engine_type in (
            ("ollama", "OLLAMA", OllamaEngine),
            ("llama", "LLAMA", LlamaEngine),
        ):
            config = _Config(
                {
                    ("AI", "ai_retries"): "2",
                    (section, "base_url"): "http://localhost:1234",
                    (section, "model"): "model",
                    (section, "api_key"): "",
                }
            )
            service = TranslationService(
                "ai",
                mock.Mock(spec=TranslationCache),
                config,
                ai_provider=provider,
            )
            first = service._build_engine(context="first")
            second = service._build_engine(context="second")
            self.assertIsInstance(first, engine_type)
            self.assertIs(first.session, second.session)
            self.assertEqual(first.retries, 2)

    def test_readiness_requires_provider_model_but_not_api_key(self):
        for label, section, provider in (
            ("Ollama", "OLLAMA", "ollama"),
            ("Llama", "LLAMA", "llama"),
        ):
            self.assertFalse(engine_readiness(_Config(), label)[0])
            config = _Config(
                {
                    (section, "base_url"): "http://localhost:1234",
                    (section, "model"): "model",
                }
            )
            ready, text = engine_readiness(config, label)
            self.assertTrue(ready)
            self.assertIn(provider.capitalize(), text)


class Beta44ConfigurationTests(unittest.TestCase):
    def test_defaults_include_native_local_provider_sections(self):
        self.assertEqual(
            ConfigManager._DEFAULTS["OLLAMA"]["base_url"],
            "http://localhost:11434/api",
        )
        self.assertEqual(
            ConfigManager._DEFAULTS["LLAMA"]["base_url"],
            "http://127.0.0.1:8080/v1",
        )


if __name__ == "__main__":
    unittest.main()
