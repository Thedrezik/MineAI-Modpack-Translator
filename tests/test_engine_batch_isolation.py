import unittest
from unittest import mock

import requests

from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.google import GoogleEngine


def callbacks(logs=None) -> EngineCallbacks:
    logs = logs if logs is not None else []
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda _message: None,
    )


class GoogleBatchIsolationTests(unittest.TestCase):
    def test_single_mode_isolates_failed_future(self) -> None:
        logs = []
        engine = GoogleEngine(workers=1, mode="single")
        items = {
            "bad": EngineItem("bad", "Bad", "Bad"),
            "good": EngineItem("good", "Good", "Good"),
        }

        def request(text: str, _api_code: str, timeout: int = 10):
            del timeout
            if text == "Bad":
                raise requests.ConnectionError("temporary failure")
            return "Хорошо"

        with mock.patch.object(engine, "_request", side_effect=request):
            result = engine.translate_batch(items, {"api": "ru"}, callbacks(logs))

        self.assertEqual(result["bad"], "Bad")
        self.assertEqual(result["good"], "Хорошо")
        self.assertTrue(any("Ошибка Google" in message for message, _tag in logs))

    def test_batch_mode_continues_with_single_fallback(self) -> None:
        logs = []
        engine = GoogleEngine(workers=1, mode="batch")
        items = {
            "first": EngineItem("first", "First", "First"),
            "second": EngineItem("second", "Second", "Second"),
        }
        calls = []

        def request(text: str, _api_code: str, timeout: int = 10):
            calls.append((text, timeout))
            if timeout == 10:
                raise requests.ConnectionError("batch failed")
            return {"First": "Первый", "Second": "Второй"}[text]

        with mock.patch.object(engine, "_request", side_effect=request):
            result = engine.translate_batch(items, {"api": "ru"}, callbacks(logs))

        self.assertEqual(result, {"first": "Первый", "second": "Второй"})
        self.assertTrue(any("Ошибка пакета Google" in message for message, _tag in logs))
        self.assertEqual(sum(timeout == 5 for _text, timeout in calls), 2)


if __name__ == "__main__":
    unittest.main()
