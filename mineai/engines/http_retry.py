"""Shared HTTP retry helpers for translation engines."""

from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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


def _retry_after_seconds(exc: requests.RequestException) -> float | None:
    if not isinstance(exc, requests.HTTPError) or exc.response is None:
        return None
    if exc.response.status_code != 429:
        return None
    headers = getattr(exc.response, "headers", {}) or {}
    raw = headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        pass
    try:
        retry_at = parsedate_to_datetime(str(raw))
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None


def _retry_delay(exc, retry_index, base_delay, rate_limit_delays):
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after
    if (rate_limit_delays and isinstance(exc, requests.HTTPError)
            and exc.response is not None and exc.response.status_code == 429):
        return float(rate_limit_delays[min(retry_index, len(rate_limit_delays) - 1)])
    return base_delay * (2 ** retry_index)


def request_with_retry(
    request: Callable[[], requests.Response],
    *,
    operation: str,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    rate_limit_delays: Sequence[float] | None = None,
    on_retry: Callable[[str], None] | None = None,
) -> requests.Response:
    """Execute an HTTP request with configurable retry delays."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if base_delay < 0:
        raise ValueError("base_delay must be non-negative")
    if rate_limit_delays is not None and (not rate_limit_delays or any(d < 0 for d in rate_limit_delays)):
        raise ValueError("rate_limit_delays must contain non-negative values")

    for attempt in range(1, attempts + 1):
        try:
            response = request()
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if not _is_retryable(exc) or attempt >= attempts:
                raise
            delay = _retry_delay(exc, attempt - 1, base_delay, rate_limit_delays)
            message = (
                f"{operation}: попытка {attempt}/{attempts} завершилась ошибкой {exc}; "
                f"повтор через {delay:.1f} сек."
            )
            logger.warning(message)
            if on_retry is not None:
                try:
                    on_retry(message)
                except Exception:
                    logger.exception("Retry notification callback failed")
            time.sleep(delay)
    raise RuntimeError("unreachable retry state")
