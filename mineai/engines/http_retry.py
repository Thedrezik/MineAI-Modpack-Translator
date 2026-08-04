"""Shared HTTP retry helpers for translation engines."""

from collections.abc import Callable
import logging
import time

import requests


logger = logging.getLogger(__name__)

DEFAULT_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0


def _is_retryable(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599
    return False


def request_with_retry(
    request: Callable[[], requests.Response],
    *,
    operation: str,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
) -> requests.Response:
    """Execute an HTTP request with bounded exponential backoff.

    Timeouts, connection failures, HTTP 429 and HTTP 5xx responses are retried.
    Other HTTP and request errors are raised immediately.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must be non-negative")

    for attempt in range(1, attempts + 1):
        try:
            response = request()
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if not _is_retryable(exc) or attempt >= attempts:
                raise

            delay = base_delay * (2 ** (attempt - 1))
            logger.warning(
                "%s failed on attempt %s/%s: %s; retrying in %.1f seconds",
                operation,
                attempt,
                attempts,
                exc,
                delay,
            )
            time.sleep(delay)

    raise RuntimeError("unreachable retry state")
