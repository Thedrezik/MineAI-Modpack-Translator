"""Release metadata must follow the runtime version automatically."""

import ast
import re
import unittest
from pathlib import Path

from mineai import __version__
from mineai.cache import _CACHE_VALIDATION_VERSION


ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def setUp(self):
        match = re.search(r"BETAv(\d+)", __version__)
        self.assertIsNotNone(match, f"Unexpected runtime version: {__version__!r}")
        self.beta = match.group(1)
        self.exe_name = f"MineAI_Translator_Beta{self.beta}.exe"

    def test_build_docs_and_ci_use_current_executable(self):
        for relative in ("build.bat", ".github/workflows/tests.yml", "README.md"):
            content = (ROOT / relative).read_text(encoding="utf-8-sig")
            self.assertIn(self.exe_name, content, relative)
        spec = (ROOT / f"MineAI_Translator_Beta{self.beta}.spec").read_text(encoding="utf-8-sig")
        self.assertIn(self.exe_name.removesuffix(".exe"), spec)

    def test_user_dictionaries_are_not_bundled(self):
        spec = (ROOT / f"MineAI_Translator_Beta{self.beta}.spec").read_text(encoding="utf-8-sig")
        self.assertNotIn("dictionary.json", spec)
        self.assertNotIn("glossary.json", spec)

    def test_spec_collects_qt_runtime_for_frozen_gui(self):
        spec = (ROOT / f"MineAI_Translator_Beta{self.beta}.spec").read_text(encoding="utf-8-sig")
        self.assertIn("qt_hiddenimports", spec)
        for module in ("PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets"):
            self.assertIn(f'"{module}"', spec)

    def test_windows_package_job_smokes_the_frozen_qt_app(self):
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8-sig")
        self.assertIn("QT_QPA_PLATFORM", workflow)
        self.assertIn("HasExited", workflow)
        self.assertIn("$size -lt 30000000", workflow)
        self.assertIn("taskkill /PID", workflow)

    def test_build_script_uses_python_launcher(self):
        build = (ROOT / "build.bat").read_text(encoding="utf-8-sig")
        self.assertIn("py -3 -m pip", build)
        self.assertIn("py -3 -m PyInstaller", build)
        self.assertNotIn("python -m pip", build)

    def test_cache_marker_contains_release_and_validator_fingerprint(self):
        self.assertIn(__version__, _CACHE_VALIDATION_VERSION)
        self.assertRegex(_CACHE_VALIDATION_VERSION, r"\|[0-9a-f]{12}$")

    def test_preview_avoids_python312_only_fstring_backslashes(self):
        """The CI compile step must remain valid on the supported Python 3.10 runtime."""
        source = (ROOT / "mineai/preview.py").read_text(encoding="utf-8-sig")
        ast.parse(source, filename="mineai/preview.py", feature_version=(3, 10))
        self.assertNotIn("normalized = f\"/{path.casefold().replace", source)
        self.assertNotIn("f\"/{item.logical_path.casefold().replace", source)


if __name__ == "__main__":
    unittest.main()
