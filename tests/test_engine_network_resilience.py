import unittest
from unittest import mock

import requests

from mineai.engines.base import EngineCallbacks, EngineItem
from mineai.engines.deepl import DeepLEngine
from mineai.engines.google import GoogleEngine
from mineai.engines.http_retry import request_with_retry
from mineai.engines.kobold import KoboldEngine
from mineai.engines.openrouter import OpenRouterEngine


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.reason = text or f"HTTP {status_code}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.HTTPError(
                f"{self.status_code}: {self.reason}",
                response=self,
            )
            raise error

    def json(self):
        return self._payload


def callbacks(logs=None) -> EngineCallbacks:
    logs = logs if logs is not None else []
    return EngineCallbacks(
        should_run=lambda: True,
        wait_if_paused=lambda: None,
        on_log=lambda message, tag: logs.append((message, tag)),
        on_status=lambda _message: None,
    )


class HttpRetryTests(unittest.TestCase):
    def test_retries_429_and_5xx_with_exponential_backoff(self) -> None:
        responses = iter(
            [
                FakeResponse(429, text="rate limited"),
                FakeResponse(503, text="unavailable"),
                FakeResponse(200, payload={"ok": True}),
            ]
        )

        with mock.patch("mineai.engines.http_retry.time.sleep") as sleep:
            response = request_with_retry(
                lambda: next(responses),
                operation="test request",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_retries_timeouts_using_delays_one_two_four(self) -> None:
        request = mock.Mock(
            side_effect=[
                requests.Timeout("first"),
                requests.Timeout("second"),
                requests.Timeout("third"),
                FakeResponse(200),
            ]
        )

        with mock.patch("mineai.engines.http_retry.time.sleep") as sleep:
            response = request_with_retry(request, operation="test request")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0, 4.0])

    def test_does_not_retry_non_retryable_4xx(self) -> None:
        request = mock.Mock(return_value=FakeResponse(401, text="unauthorized"))

        with mock.patch("mineai.engines.http_retry.time.sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                request_with_retry(request, operation="test request")

        request.assert_called_once_with()
        sleep.assert_not_called()


class EngineRetryIntegrationTests(unittest.TestCase):
    def test_google_retries_timeout(self) -> None:
        engine = GoogleEngine(workers=1)
        success = FakeResponse(200, payload=[[['Привет']]])

        with (
            mock.patch(
                "mineai.engines.google.requests.get",
                side_effect=[requests.Timeout("temporary"), success],
            ),
            mock.patch("mineai.engines.http_retry.time.sleep") as sleep,
        ):
            result = engine._request("Hello", "ru")

        self.assertEqual(result, "Привет")
        sleep.assert_called_once_with(1.0)

    def test_deepl_retries_5xx(self) -> None:
        engine = DeepLEngine("secret:fx")
        items = {"key": EngineItem("key", "Hello", "Hello")}
        success = FakeResponse(
            200,
            payload={"translations": [{"text": "Привет"}]},
        )

        with (
            mock.patch(
                "mineai.engines.deepl.requests.post",
                side_effect=[FakeResponse(503), success],
            ),
            mock.patch("mineai.engines.deepl.time.sleep") as sleep,
        ):
            result = engine.translate_batch(
                items,
                {"api": "ru", "deepl": "RU"},
                callbacks(),
            )

        self.assertEqual(result, {"key": "Привет"})
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 0.5])

    def test_openrouter_retries_429(self) -> None:
        engine = OpenRouterEngine(api_key="secret", model="model")
        success = FakeResponse(
            200,
            payload={"choices": [{"message": {"content": "translated"}}]},
        )

        with (
            mock.patch(
                "mineai.engines.openrouter.requests.post",
                side_effect=[FakeResponse(429), success],
            ),
            mock.patch("mineai.engines.http_retry.time.sleep") as sleep,
        ):
            result = engine._request("prompt", 100)

        self.assertEqual(result, "translated")
        sleep.assert_called_once_with(1.0)

    def test_kobold_retries_5xx(self) -> None:
        engine = KoboldEngine()
        success = FakeResponse(
            200,
            payload={"choices": [{"message": {"content": "translated"}}]},
        )

        with (
            mock.patch(
                "mineai.engines.kobold.requests.post",
                side_effect=[FakeResponse(500), success],
            ),
            mock.patch("mineai.engines.http_retry.time.sleep") as sleep,
        ):
            result = engine._request("prompt", 100)

        self.assertEqual(result, "translated")
        sleep.assert_called_once_with(1.0)


if __name__ == "__main__":
    unittest.main()
