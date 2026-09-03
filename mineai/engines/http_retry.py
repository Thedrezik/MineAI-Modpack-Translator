"""Shared HTTP retry helpers for translation engines."""

from collections.abc import Callable
import logging
import time

import requests


logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0


class RequestCancelled(Exception):
    """Raised when the active translation job is cancelled between HTTP attempts."""


def _is_retryable(
    exc: requests.RequestException,
    *,
    retry_429: bool = True,
) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status_code = exc.response.status_code
        return (retry_429 and status_code == 429) or 500 <= status_code <= 599
    return False


def _ensure_running(should_continue: Callable[[], bool] | None) -> None:
    if should_continue is not None and not should_continue():
        raise RequestCancelled("request cancelled")


def _interruptible_sleep(
    delay: float,
    should_continue: Callable[[], bool] | None,
) -> None:
    if delay <= 0:
        _ensure_running(should_continue)
        return
    if should_continue is None:
        time.sleep(delay)
        return

    deadline = time.monotonic() + delay
    while True:
        _ensure_running(should_continue)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.1, remaining))


def request_with_retry(
    request: Callable[[], requests.Response],
    *,
    operation: str,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    on_log: Callable[[str, str], None] | None = None,
    delay_func: Callable[[int, Exception], float] | None = None,
    should_continue: Callable[[], bool] | None = None,
    retry_429: bool = True,
) -> requests.Response:
    """Execute an HTTP request with bounded, cancellation-aware backoff.

    Timeouts, connection failures, HTTP 429 and HTTP 5xx responses are retried
    by default. Callers that have another endpoint to try can disable 429
    retries so a rate-limited host is abandoned immediately.
    Other HTTP and request errors are raised immediately. Cancellation is checked
    before every attempt and while waiting between retries.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must be non-negative")

    for attempt in range(1, attempts + 1):
        _ensure_running(should_continue)
        try:
            response = request()
            response.raise_for_status()
            return response
        except RequestCancelled:
            raise
        except requests.RequestException as exc:
            if not _is_retryable(exc, retry_429=retry_429) or attempt >= attempts:
                raise

            if delay_func:
                delay = delay_func(attempt, exc)
            else:
                delay = base_delay * (2 ** (attempt - 1))

            msg = f"{operation} (попытка {attempt}/{attempts}): {exc}. Повтор через {delay:.1f}с"
            if on_log:
                on_log(f"⚠️ {msg}", "yellow")
            else:
                logger.warning(msg)

            _interruptible_sleep(delay, should_continue)

    raise RuntimeError("unreachable retry state")
