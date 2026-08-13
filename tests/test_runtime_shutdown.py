import subprocess
import threading
import time
import unittest
from unittest import mock

from mineai.runtime.ai_launcher import AiLauncher
from mineai.runtime.state import JobState


class JobStateShutdownTests(unittest.TestCase):
    def test_stop_wakes_worker_waiting_on_pause(self) -> None:
        state = JobState()
        state.start()
        self.assertTrue(state.pause())

        returned = threading.Event()

        def wait() -> None:
            state.wait_if_paused()
            returned.set()

        worker = threading.Thread(target=wait)
        worker.start()
        time.sleep(0.02)
        self.assertFalse(returned.is_set())

        state.stop()
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())
        self.assertTrue(returned.is_set())
        self.assertFalse(state.should_run())

    def test_resume_wakes_worker_waiting_on_pause(self) -> None:
        state = JobState()
        state.start()
        state.pause()
        returned = threading.Event()

        worker = threading.Thread(
            target=lambda: (state.wait_if_paused(), returned.set())
        )
        worker.start()
        time.sleep(0.02)

        self.assertTrue(state.resume())
        worker.join(timeout=1)

        self.assertTrue(returned.is_set())
        self.assertTrue(state.should_run())

    def test_stop_is_idempotent(self) -> None:
        state = JobState()
        state.start()
        state.stop()
        state.stop()

        snapshot = state.snapshot()
        self.assertFalse(snapshot.is_running)
        self.assertFalse(snapshot.is_paused)


class AiLauncherShutdownTests(unittest.TestCase):
    @staticmethod
    def _launcher() -> AiLauncher:
        return AiLauncher(mock.Mock())

    def test_terminate_clears_already_exited_process(self) -> None:
        launcher = self._launcher()
        process = mock.Mock()
        process.poll.return_value = 0
        launcher.process = process

        self.assertTrue(launcher.terminate())
        self.assertIsNone(launcher.process)
        process.terminate.assert_not_called()

    def test_terminate_timeout_falls_back_to_kill(self) -> None:
        launcher = self._launcher()
        process = mock.Mock()
        process.poll.side_effect = [None, 0]
        process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="kobold", timeout=2),
            0,
        ]
        launcher.process = process

        self.assertTrue(launcher.terminate())

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertEqual(process.wait.call_count, 2)
        self.assertIsNone(launcher.process)

    def test_failed_kill_keeps_process_reference(self) -> None:
        launcher = self._launcher()
        process = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="kobold", timeout=2),
            subprocess.TimeoutExpired(cmd="kobold", timeout=2),
        ]
        launcher.process = process

        self.assertFalse(launcher.terminate())

        process.terminate.assert_called_once_with()
        process.kill.assert_called_once_with()
        self.assertIs(launcher.process, process)


if __name__ == "__main__":
    unittest.main()
