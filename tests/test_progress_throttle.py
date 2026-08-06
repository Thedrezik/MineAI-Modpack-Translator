import unittest
from unittest import mock

from mineai.runtime.job import TranslationJob
from mineai.runtime.state import JobState


class ProgressThrottleTests(unittest.TestCase):
    @staticmethod
    def _job(state, statuses):
        return TranslationJob(
            mock.Mock(),
            mock.Mock(),
            mock.Mock(),
            state,
            on_log=lambda *_args: None,
            on_status=lambda *args: statuses.append(args),
            on_row=lambda *_args: None,
        )

    def test_progress_count_is_never_dropped_by_throttling(self):
        state = JobState(is_running=True, total_strings=10)
        state.begin_progress()
        statuses = []
        job = self._job(state, statuses)

        with mock.patch(
            "mineai.runtime.job.time.monotonic",
            side_effect=[10.0, 10.1, 10.2],
        ):
            job._on_progress(2)
            job._on_progress(3)
            job._on_progress(1)

        self.assertEqual(state.snapshot().translated_strings, 6)
        self.assertEqual(len(statuses), 1)

    def test_status_is_published_again_after_throttle_interval(self):
        state = JobState(is_running=True, total_strings=10)
        state.begin_progress()
        statuses = []
        job = self._job(state, statuses)

        with mock.patch(
            "mineai.runtime.job.time.monotonic",
            side_effect=[10.0, 10.2, 10.4],
        ):
            job._on_progress()
            job._on_progress()
            job._on_progress()

        self.assertEqual(state.snapshot().translated_strings, 3)
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[-1][1], 0.3)

    def test_callbacks_use_the_throttled_progress_handler(self):
        state = JobState(is_running=True, total_strings=2)
        statuses = []
        job = self._job(state, statuses)
        callbacks = job._callbacks()

        with mock.patch("mineai.runtime.job.time.monotonic", return_value=10.0):
            callbacks.on_progress(1)

        self.assertEqual(state.snapshot().translated_strings, 1)
        self.assertEqual(len(statuses), 1)


if __name__ == "__main__":
    unittest.main()
