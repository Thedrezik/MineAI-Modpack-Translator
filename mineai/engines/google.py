import re
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.http_retry import RequestCancelled, request_with_retry
from mineai.text_processing import (
    PLACEHOLDER_PATTERN,
    polish_translation,
    suspicious_duplicate_keys,
    translation_length_issue,
    unmask_translation,
)


class GoogleEngine(TranslationEngine):
    API_URL = "https://translate.googleapis.com/translate_a/single"
    BATCH_SEP = " |~| "

    def __init__(self, workers: int = 5, mode: str = "single") -> None:
        self.workers = max(1, min(workers, 10))
        self.mode = mode

    def _request(self, text: str, api_code: str, timeout: int = 10, on_log=None, should_continue=None) -> str | None:
        try:
            response = request_with_retry(
                lambda: requests.get(
                    self.API_URL,
                    params={"client": "gtx", "sl": "en", "tl": api_code, "dt": "t", "q": text},
                    timeout=timeout,
                ),
                operation="Google Translate",
                on_log=on_log,
                should_continue=should_continue,
            )
            return "".join(part[0] for part in response.json()[0] if part[0])
        except RequestCancelled:
            raise
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
            return None

    def _finalize(self, raw: str, item: EngineItem) -> str:
        text = unmask_translation(raw, item.mapping)
        return polish_translation(text, boundary_source=item.original)

    @staticmethod
    def _raw_is_safe(raw: str, item: EngineItem) -> bool:
        return (
            PLACEHOLDER_PATTERN.findall(raw)
            == PLACEHOLDER_PATTERN.findall(item.masked)
            and translation_length_issue(item.masked, raw) is None
        )

    def _request_item(
        self,
        item: EngineItem,
        api_code: str,
        callbacks: EngineCallbacks,
        *,
        attempts: int,
        timeout: int = 10,
    ) -> str | None:
        for attempt in range(attempts):
            raw = self._request(
                item.masked,
                api_code,
                timeout=timeout,
                on_log=callbacks.on_log,
                should_continue=callbacks.should_run,
            )
            if raw and self._raw_is_safe(raw, item):
                return raw
            if raw and attempt + 1 < attempts:
                callbacks.on_log(
                    "⚠️ Google: повреждены маркеры или размер строки; повтор",
                    "yellow",
                )
        return None

    def translate_batch(
        self,
        items: dict[str, EngineItem],
        target_lang: dict,
        callbacks: EngineCallbacks,
    ) -> dict[str, str]:
        if not items:
            return {}
        api_code = target_lang["api"]
        if self.mode == "batch":
            return self._translate_batch_mode(items, api_code, callbacks)
        return self._translate_single_mode(items, api_code, callbacks)

    def _translate_single_mode(
        self, items: dict[str, EngineItem], api_code: str, callbacks: EngineCallbacks
    ) -> dict[str, str]:
        result: dict[str, str] = {}

        def work(key: str, masked: str) -> tuple[str, str | None]:
            if not callbacks.should_run():
                return key, None
            del masked
            return key, self._request_item(
                items[key], api_code, callbacks, attempts=2
            )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(work, k, v.masked): k for k, v in items.items()}
            for fut in as_completed(futures):
                callbacks.wait_if_paused()
                if not callbacks.should_run():
                    break
                try:
                    key, raw = fut.result()
                except RequestCancelled:
                    break
                if raw:
                    result[key] = self._finalize(raw, items[key])
        return result

    def _translate_batch_mode(
        self, items: dict[str, EngineItem], api_code: str, callbacks: EngineCallbacks
    ) -> dict[str, str]:
        chunks: list[tuple[list[str], str]] = []
        keys: list[str] = []
        current = ""

        for key, item in items.items():
            if len(current) + len(item.masked) > 2000 or len(keys) >= 20:
                chunks.append((keys, current))
                keys = [key]
                current = item.masked
            else:
                keys.append(key)
                current = current + self.BATCH_SEP + item.masked if current else item.masked
        if keys:
            chunks.append((keys, current))

        result: dict[str, str] = {}

        def translate_chunk(chunk_keys: list[str], text: str) -> tuple[list[str], list[str] | None]:
            if not callbacks.should_run():
                return chunk_keys, None
            raw = self._request(
                text,
                api_code,
                on_log=callbacks.on_log,
                should_continue=callbacks.should_run,
            )
            if not raw:
                return chunk_keys, None
            parts = re.split(r"\s*\|\s*~\s*\|\s*", raw)
            if len(parts) == len(chunk_keys):
                return chunk_keys, parts
            return chunk_keys, None

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(translate_chunk, ck, ct) for ck, ct in chunks]
            for fut in as_completed(futures):
                callbacks.wait_if_paused()
                if not callbacks.should_run():
                    break
                try:
                    chunk_keys, parts = fut.result()
                except RequestCancelled:
                    break
                if parts:
                    raw_by_key = {
                        key: parts[idx].strip()
                        for idx, key in enumerate(chunk_keys)
                    }
                    retry_keys = {
                        key
                        for key, raw in raw_by_key.items()
                        if not self._raw_is_safe(raw, items[key])
                    }
                    duplicate_keys = suspicious_duplicate_keys(
                        {key: items[key].masked for key in chunk_keys},
                        raw_by_key,
                    )
                    retry_keys.update(duplicate_keys)
                    if duplicate_keys:
                        callbacks.on_log(
                            "⚠️ Google: одинаковый ответ для разных строк; "
                            "повтор по одной",
                            "yellow",
                        )
                    for idx, key in enumerate(chunk_keys):
                        if key not in retry_keys:
                            result[key] = self._finalize(
                                parts[idx].strip(), items[key]
                            )
                    for key in chunk_keys:
                        if key not in retry_keys:
                            continue
                        if not callbacks.should_run():
                            break
                        try:
                            single = self._request_item(
                                items[key],
                                api_code,
                                callbacks,
                                attempts=1,
                                timeout=5,
                            )
                        except RequestCancelled:
                            break
                        if single:
                            result[key] = self._finalize(single, items[key])
                        if callbacks.should_run():
                            time.sleep(0.3)
                else:
                    for key in chunk_keys:
                        if not callbacks.should_run():
                            break
                        try:
                            single = self._request_item(
                                items[key],
                                api_code,
                                callbacks,
                                attempts=1,
                                timeout=5,
                            )
                        except RequestCancelled:
                            break
                        if single:
                            result[key] = self._finalize(single, items[key])
                        if callbacks.should_run():
                            time.sleep(0.3)
        return result
