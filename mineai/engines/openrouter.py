import time
import requests

from mineai.constants import OPENROUTER_API
from mineai.engines.llm_common import BatchLlmEngine


class OpenRouterEngine(BatchLlmEngine):
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        api_url: str = OPENROUTER_API,
        mode: str = "safe",
        context: str = "",
        site_url: str = "",
        app_name: str = "MineAI Translator",
    ) -> None:
        self.api_url = api_url.strip() or "https://openrouter.ai/api/v1/chat/completions"
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.site_url = site_url.strip()
        self.app_name = app_name.strip() or "MineAI Translator"
        super().__init__(
            mode=mode,
            context=context,
            call_api=self._request,
            label="OpenRouter",
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.site_url:
            headers["HTTP-Referer"] = self.site_url
        if self.app_name:
            headers["X-Title"] = self.app_name
        return headers

    def _request(self, prompt: str, max_tokens: int) -> str | None:
        max_retries = 3
        base_delay = 4  # Базовая пауза в 4 секунды между запросами для бесплатных ИИ

        for attempt in range(max_retries):
            time.sleep(base_delay)
            
            response = requests.post(
                self.api_url,
                headers=self._headers(),
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                    "max_tokens": max_tokens,
                },
                timeout=300,
            )
            
            # Если словили лимит (429), ждем дольше и пробуем снова
            if response.status_code == 429:
                wait_time = 15 * (attempt + 1)
                print(f"\n[OpenRouter] Поймали лимит 429. Ждем {wait_time} сек. (Попытка {attempt + 1}/{max_retries})...")
                time.sleep(wait_time)
                continue

            if not response.ok:
                detail = response.text[:200] if response.text else response.reason
                raise requests.HTTPError(f"{response.status_code}: {detail}", response=response)
            
            data = response.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content")
            
            if content is None:
                print("\n[Предупреждение] OpenRouter вернул пустой ответ (возможно, сработал фильтр модели).")
                return None
                
            return content.strip()
            
        print("\n[Ошибка] Не удалось получить ответ: бесплатная модель слишком перегружена.")
        return None
