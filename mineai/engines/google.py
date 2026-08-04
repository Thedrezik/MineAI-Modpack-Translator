import logging
import re
import time

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

from mineai.engines.base import EngineCallbacks, EngineItem, TranslationEngine
from mineai.engines.http_retry import request_with_retry
from mineai.text_processing import polish_translation, unmask_translation


logger = logging.getLogger(__name__)


class GoogleEngine(TranslationEngine):
    API_URL = "https://translate.googleapis.com/translate_a/single"
    BATCH_SEP = " |~| "

    def __init__(self, workers: int = 5, mode: str = "single") -> None:
        self.workers = max(1, min(workers, 10))
        self.mode = mode

    def _request(self, text: str, api_code: str, timeout: int = 10) -> str | None:
        response = request_with_retry(
            lambda: requests.get(
                self.API_URL,
                params={"client": "gtx", "sl": "en", "tl": api_code, "dt": "t", "q": text},
                timeout=timeout,
            ),
            operation="Google Translate request",
        )
        try:
            return "".join(part[0] for part in response.json()[0] if part[0])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.error("Google Translate returned an invalid response: %s", exc)
            return None

    def _finalize(self, raw: str, item: EngineItem) -> str:
        text = unmask_translation(raw, item.mapping)
        return polish_translation(text)

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
            return key, self._request(masked, api_code)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(work, k, v.masked): k for k, v in items.items()}
            for fut in as_completed(futures):
                callbacks.wait_if_paused()
                if not callbacks.should_run():
                    break
                key = futures[fut]
                try:
                    _, raw = fut.result()
                    if raw:
                        result[key] = self._finalize(raw, items[key])
                    else:
                        result[key] = items[key].original
                except Exception as exc:
                    logger.exception("Google Translate item failed: %s", key)
                    callbacks.on_log(f"❌ Ошибка Google для строки {key}: {exc}", "red")
                    result[key] = items[key].original
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
            raw = self._request(text, api_code)
            if not raw:
                return chunk_keys, None
            parts = re.split(r"\s*\|\s*~\s*\|\s*", raw)
            if len(parts) == len(chunk_keys):
                return chunk_keys, parts
            return chunk_keys, None

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(translate_chunk, chunk_keys, text): chunk_keys
                for chunk_keys, text in chunks
            }
            for fut in as_completed(futures):
                callbacks.wait_if_paused()
                if not callbacks.should_run():
                    break
                chunk_keys = futures[fut]
                try:
                    _, parts = fut.result()
                except Exception as exc:
                    logger.exception("Google Translate batch failed")
                    callbacks.on_log(
                        f"❌ Ошибка пакета Google ({len(chunk_keys)} строк): {exc}",
                        "red",
                    )
                    parts = None

                if parts:
                    for idx, key in enumerate(chunk_keys):
                        result[key] = self._finalize(parts[idx].strip(), items[key])
                    continue

                for key in chunk_keys:
                    try:
                        single = self._request(items[key].masked, api_code, timeout=5)
                        result[key] = (
                            self._finalize(single, items[key])
                            if single
                            else items[key].original
                        )
                    except Exception as exc:
                        logger.exception("Google Translate fallback item failed: %s", key)
                        callbacks.on_log(
                            f"❌ Ошибка Google для строки {key}: {exc}",
                            "red",
                        )
                        result[key] = items[key].original
                    time.sleep(0.3)
        return result
