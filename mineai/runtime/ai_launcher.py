import subprocess
import threading
import time
from collections.abc import Callable

import requests

from mineai.config import ConfigManager
from mineai.constants import KOBOLD_MODELS_URL


class AiLauncher:
    STARTUP_TIMEOUT_SECONDS = 180
    POLL_INTERVAL_SECONDS = 1.0
    TERMINATE_TIMEOUT_SECONDS = 2.0
    KILL_TIMEOUT_SECONDS = 2.0

    def __init__(self, config: ConfigManager) -> None:
        self.config = config
        self.process: subprocess.Popen | None = None
        self._lock = threading.RLock()

    def is_alive(self) -> bool:
        try:
            return requests.get(KOBOLD_MODELS_URL, timeout=1).status_code == 200
        except requests.RequestException:
            return False

    def _owned_process(self) -> subprocess.Popen | None:
        with self._lock:
            process = self.process
            if process is not None and process.poll() is not None:
                self.process = None
                return None
            return process

    def ensure_running(
        self,
        should_continue: Callable[[], bool],
        on_status,
        on_log,
    ) -> bool:
        if self.is_alive():
            on_log("✅ ИИ уже работает", "green")
            return True

        existing = self._owned_process()
        if existing is not None and not self.terminate():
            on_log("❌ Не удалось завершить предыдущий процесс ИИ.", "red")
            return False

        exe = self.config.get("AI", "exe_path")
        model = self.config.get("AI", "model_path")
        gpu = self.config.get("AI", "gpu_layers")
        if not gpu.isdigit():
            gpu = "99"

        on_log("🤖 Запуск ИИ...", "cyan")
        try:
            process = subprocess.Popen(
                [
                    exe,
                    model,
                    "--port",
                    "5001",
                    "--contextsize",
                    "8192",
                    "--gpulayers",
                    gpu,
                ]
            )
        except OSError as exc:
            on_log(f"❌ Ошибка запуска ИИ: {exc}", "red")
            return False

        with self._lock:
            self.process = process

        start_time = time.monotonic()
        deadline = start_time + self.STARTUP_TIMEOUT_SECONDS
        
        while time.monotonic() < deadline:
            if not should_continue():
                self.terminate()
                on_log("🛑 Запуск ИИ отменён.", "yellow")
                return False

            if process.poll() is not None:
                with self._lock:
                    if self.process is process:
                        self.process = None
                on_log("❌ Процесс ИИ завершился до запуска сервера.", "red")
                return False

            elapsed = int(time.monotonic() - start_time)
            on_status(
                "Прогрев нейросети... "
                f"({elapsed}/{self.STARTUP_TIMEOUT_SECONDS} сек)"
            )
            if self.is_alive():
                on_log("✅ ИИ успешно запущен!\n", "green")
                return True
            time.sleep(self.POLL_INTERVAL_SECONDS)

        self.terminate()
        on_log("❌ Сервер ИИ не отвечает.", "red")
        return False

    def terminate(self) -> bool:
        with self._lock:
            process = self.process
            if process is None:
                return True

            if process.poll() is not None:
                self.process = None
                return True

            try:
                process.terminate()
                process.wait(timeout=self.TERMINATE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    process.wait(timeout=self.KILL_TIMEOUT_SECONDS)
                except (OSError, subprocess.TimeoutExpired):
                    return False
            except OSError:
                if process.poll() is None:
                    return False

            if process.poll() is None:
                return False

            if self.process is process:
                self.process = None
            return True