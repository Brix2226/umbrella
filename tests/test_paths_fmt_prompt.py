import io
import os
from pathlib import Path
from unittest import mock

from helpers import FakeHomeTest, TTYStringIO
from umbrella import fmt, paths, prompt


class PathsTest(FakeHomeTest):
    def test_locations(self):
        self.assertEqual(paths.umbrella_dir(), self.home / ".umbrella")
        self.assertEqual(paths.registry_file(), self.home / ".umbrella" / "profiles.json")
        self.assertEqual(paths.profiles_dir(), self.home / ".umbrella" / "profiles")
        self.assertEqual(paths.backups_dir(), self.home / ".umbrella" / "backups")
        self.assertEqual(paths.default_claude_dir(), self.home / ".claude")
        self.assertEqual(paths.default_state_file(), self.home / ".claude.json")

    def test_umbrella_dir_override(self):
        os.environ["UMBRELLA_DIR"] = str(self.tmp / "u")
        self.assertEqual(paths.umbrella_dir(), self.tmp / "u")

    def test_detect_platform(self):
        self.assertEqual(paths.detect_platform("Darwin"), "macos")
        self.assertEqual(paths.detect_platform("Linux", "Linux version 5.15 microsoft-standard-WSL2"), "wsl")
        self.assertEqual(paths.detect_platform("Linux", "Linux version 6.1 generic"), "linux")
        self.assertEqual(paths.detect_platform("FreeBSD"), "freebsd")
        self.assertIn(paths.detect_platform(), ("macos", "linux", "wsl"))

    def test_detect_platform_reads_proc_version(self):
        with mock.patch.object(paths, "_read_proc_version", return_value="Microsoft"):
            self.assertEqual(paths.detect_platform("Linux"), "wsl")

    def test_read_proc_version(self):
        with mock.patch.object(Path, "read_text", return_value="Linux x"):
            self.assertEqual(paths._read_proc_version(), "Linux x")
        with mock.patch.object(Path, "read_text", side_effect=OSError):
            self.assertEqual(paths._read_proc_version(), "")

    def test_platform_name(self):
        self.assertEqual(paths.platform_name("wsl"), "Windows (WSL2)")
        self.assertEqual(paths.platform_name("plan9"), "plan9")

    def test_pretty(self):
        self.assertEqual(paths.pretty(self.home), "~")
        self.assertEqual(paths.pretty(self.home / ".claude"), "~/.claude")
        self.assertEqual(paths.pretty("/etc/x"), "/etc/x")
        self.assertEqual(paths.pretty(str(self.home) + "x"), str(self.home) + "x")


class FmtTest(FakeHomeTest):
    def test_human_size(self):
        self.assertEqual(fmt.human_size(0), "0 B")
        self.assertEqual(fmt.human_size(1023), "1023 B")
        self.assertEqual(fmt.human_size(1024), "1 KB")
        self.assertEqual(fmt.human_size(1536), "1.5 KB")
        self.assertEqual(fmt.human_size(5 * 1024 ** 2), "5 MB")
        self.assertEqual(fmt.human_size(3 * 1024 ** 4), "3072 GB")

    def test_human_age(self):
        now = 1_000_000_000
        self.assertEqual(fmt.human_age(0, now), "never")
        self.assertEqual(fmt.human_age(now - 5, now), "just now")
        self.assertEqual(fmt.human_age(now + 50, now), "just now")
        self.assertEqual(fmt.human_age(now - 60, now), "1 minute ago")
        self.assertEqual(fmt.human_age(now - 7200, now), "2 hours ago")
        self.assertEqual(fmt.human_age(now - 86400 * 3, now), "3 days ago")
        self.assertEqual(fmt.human_age(now - 86400 * 90, now), "3 months ago")
        self.assertEqual(fmt.human_age(now - 86400 * 365 * 3, now), "3 years ago")
        self.assertEqual(fmt.human_age(1), fmt.human_age(1, None))

    def test_plural(self):
        self.assertEqual(fmt.plural(1, "file"), "1 file")
        self.assertEqual(fmt.plural(2, "file"), "2 files")
        self.assertEqual(fmt.plural(0, "entry", "entries"), "0 entries")


class PromptTest(FakeHomeTest):
    def run_confirm(self, answer, **kw):
        out = io.StringIO()
        return prompt.confirm("Go?", stdin=TTYStringIO(answer), stdout=out, **kw), out.getvalue()

    def test_confirm_answers(self):
        self.assertTrue(self.run_confirm("y\n")[0])
        self.assertTrue(self.run_confirm("YES\n")[0])
        self.assertFalse(self.run_confirm("n\n")[0])
        result, shown = self.run_confirm("\n")
        self.assertFalse(result)
        self.assertIn("[y/N]", shown)
        result, shown = self.run_confirm("\n", default=True)
        self.assertTrue(result)
        self.assertIn("[Y/n]", shown)

    def test_confirm_assume_yes(self):
        self.assertTrue(prompt.confirm("Go?", assume_yes=True))

    def test_confirm_non_interactive(self):
        out = io.StringIO()
        self.assertFalse(prompt.confirm("Go?", stdin=io.StringIO("y\n"), stdout=out))
        self.assertIn("--yes", out.getvalue())

    def test_confirm_uses_sys_streams(self):
        with mock.patch("sys.stdin", io.StringIO()), mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertFalse(prompt.confirm("Go?"))
        self.assertIn("Go?", out.getvalue())

    def test_ask(self):
        out = io.StringIO()
        self.assertEqual(prompt.ask("Name?", "personal", TTYStringIO("work\n"), out), "work")
        self.assertIn("[personal]", out.getvalue())
        self.assertEqual(prompt.ask("Name?", "personal", TTYStringIO("\n"), out), "personal")
        self.assertEqual(prompt.ask("Name?", "personal", io.StringIO("work\n"), out), "personal")
        with mock.patch("sys.stdin", io.StringIO()):
            self.assertEqual(prompt.ask("Name?", "p"), "p")
