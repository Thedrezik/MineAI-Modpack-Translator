import unittest

from mineai.runtime.state import JobSnapshot, JobState


class JobStateProgressTests(unittest.TestCase):
    def test_start_resets_all_progress_counters(self):
        state = JobState(
            total_strings=100,
            translated_strings=80,
            current_file_type="Моды",
            current_file_done=4,
            total_files=5,
            start_time=123.0,
        )

        state.start()

        snapshot = state.snapshot()
        self.assertTrue(snapshot.is_running)
        self.assertFalse(snapshot.is_paused)
        self.assertEqual(snapshot.total_strings, 0)
        self.assertEqual(snapshot.translated_strings, 0)
        self.assertEqual(snapshot.current_file_type, "")
        self.assertEqual(snapshot.current_file_done, 0)
        self.assertEqual(snapshot.total_files, 0)
        self.assertIsNone(snapshot.start_time)

    def test_finish_preserves_final_progress(self):
        state = JobState(
            is_running=True,
            total_strings=10,
            translated_strings=10,
            current_file_type="Моды",
            current_file_done=2,
            total_files=2,
            start_time=100.0,
        )

        state.finish()

        snapshot = state.snapshot()
        self.assertFalse(snapshot.is_running)
        self.assertEqual(snapshot.total_strings, 10)
        self.assertEqual(snapshot.translated_strings, 10)
        self.assertEqual(snapshot.current_file_type, "Моды")
        self.assertEqual(snapshot.current_file_done, 2)
        self.assertEqual(snapshot.total_files, 2)
        self.assertEqual(snapshot.start_time, 100.0)

    def test_begin_progress_preserves_estimated_total(self):
        state = JobState(
            total_strings=25,
            translated_strings=20,
            current_file_type="BQ",
            current_file_done=3,
            total_files=4,
        )

        state.begin_progress()

        snapshot = state.snapshot()
        self.assertEqual(snapshot.total_strings, 25)
        self.assertEqual(snapshot.translated_strings, 0)
        self.assertEqual(snapshot.current_file_type, "")
        self.assertEqual(snapshot.current_file_done, 0)
        self.assertEqual(snapshot.total_files, 0)
        self.assertIsNotNone(snapshot.start_time)

    def test_display_and_line_progress_are_capped_at_one_hundred_percent(self):
        state = JobState(
            is_running=True,
            total_strings=5,
            translated_strings=8,
            start_time=100.0,
        )

        self.assertEqual(state.line_progress(), 1.0)
        self.assertIn("Строки: 5/5", state.get_full_status())
        self.assertNotIn("Строки: 8/5", state.get_full_status())

    def test_eta_reports_finishing_while_job_is_still_running(self):
        snapshot = JobSnapshot(
            is_running=True,
            is_paused=False,
            total_strings=10,
            translated_strings=10,
            current_file_type="",
            current_file_done=0,
            total_files=0,
            start_time=100.0,
        )

        self.assertEqual(JobState._eta_text(snapshot, now=110.0), "завершается...")

    def test_eta_reports_ready_only_after_job_has_finished(self):
        snapshot = JobSnapshot(
            is_running=False,
            is_paused=False,
            total_strings=10,
            translated_strings=12,
            current_file_type="",
            current_file_done=0,
            total_files=0,
            start_time=100.0,
        )

        self.assertEqual(JobState._eta_text(snapshot, now=110.0), "готово")

    def test_eta_keeps_calculating_during_warmup(self):
        snapshot = JobSnapshot(
            is_running=True,
            is_paused=False,
            total_strings=10,
            translated_strings=1,
            current_file_type="",
            current_file_done=0,
            total_files=0,
            start_time=100.0,
        )

        self.assertEqual(JobState._eta_text(snapshot, now=104.9), "расчёт...")


if __name__ == "__main__":
    unittest.main()
