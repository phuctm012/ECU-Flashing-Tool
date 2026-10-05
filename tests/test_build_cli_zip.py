"""tools/build_cli_zip.py must never package GUI code, and must refuse to
replace the zip when the CLI can't run without it."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import build_cli_zip  # noqa: E402


class TestPackageContents(unittest.TestCase):

    def setUp(self):
        self.files, self.missing = build_cli_zip.list_package_files("HEAD")

    def test_no_gui_code(self):
        leaked = [f for f in self.files
                  if f.startswith(("gui/", "resources/"))
                  or f in ("main.py", "build.bat")]
        self.assertEqual(leaked, [])

    def test_has_everything_the_cli_and_build_script_need(self):
        for f in ("cli.py", "cli_gitlab.py", "build_cli.bat",
                  "requirements.txt", "requirements_build.txt",
                  "core/flash_controller.py", "communication/uds_client.py",
                  "parsers/auto_parser.py", "config/settings.py"):
            self.assertIn(f, self.files)
        self.assertEqual(self.missing, [])

    def test_only_code_and_the_english_cli_guide(self):
        non_code = {f for f in self.files if not f.endswith(".py")}
        self.assertEqual(non_code, {"README_CLI_EN.md", "build_cli.bat",
                                    "requirements.txt",
                                    "requirements_build.txt"})

    def test_forbidden_path_is_a_hard_error(self):
        original = build_cli_zip.ROOT_FILES
        build_cli_zip.ROOT_FILES = original + ["main.py"]
        try:
            with self.assertRaises(RuntimeError):
                build_cli_zip.list_package_files("HEAD")
        finally:
            build_cli_zip.ROOT_FILES = original


class TestZipOnlyRewrittenOnChange(unittest.TestCase):

    def test_same_content_leaves_the_file_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp, "src")
            src.mkdir()
            Path(src, "cli.py").write_text("print(1)\n")
            out = Path(tmp, "out.zip")
            self.assertTrue(build_cli_zip.write_zip(src, out))
            first = out.read_bytes()
            self.assertFalse(build_cli_zip.write_zip(src, out))
            self.assertEqual(out.read_bytes(), first)
            Path(src, "cli.py").write_text("print(2)\n")
            self.assertTrue(build_cli_zip.write_zip(src, out))


class TestSmokeTestRejectsGuiDependency(unittest.TestCase):

    def test_cli_importing_gui_fails_the_smoke_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "cli.py").write_text("import gui.main_window\n")
            ok = build_cli_zip.smoke_test(Path(tmp), sys.executable,
                                          Path(tmp, "sample.hex"))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
