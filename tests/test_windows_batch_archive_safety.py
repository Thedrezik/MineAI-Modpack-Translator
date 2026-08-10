import unittest
from pathlib import Path


class WindowsBatchArchiveSafetyTests(unittest.TestCase):
    def test_batch_files_are_ascii_and_crlf(self) -> None:
        for filename in ("build.bat", "start.bat"):
            with self.subTest(filename=filename):
                data = Path(filename).read_bytes()
                self.assertTrue(data.endswith(b"\r\n"))
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
                data.decode("ascii")

    def test_gitattributes_preserves_batch_blob_bytes(self) -> None:
        attributes = Path(".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.bat -text", attributes)


if __name__ == "__main__":
    unittest.main()
