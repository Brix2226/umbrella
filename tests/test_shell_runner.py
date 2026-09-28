import hashlib
import os
import subprocess

from helpers import FakeHomeTest
from umbrella import profiles, runner, shell
from umbrella.errors import UmbrellaError


class ShellTest(FakeHomeTest):
    def test_detect_shell(self):
        self.assertEqual(shell.detect_shell({"SHELL": "/usr/bin/fish"}), "fish")
        self.assertEqual(shell.detect_shell({"SHELL": "/bin/tcsh"}), "bash")
        self.assertEqual(shell.detect_shell({}), "bash")
        self.assertEqual(shell.detect_shell(), "zsh")

    def test_check_shell(self):
        with self.assertRaises(UmbrellaError):
            shell.check_shell("csh")

    def test_hook_scripts(self):
        self.assertIn("__env --shell zsh", shell.hook_script("zsh"))
        self.assertIn("| source", shell.hook_script("fish"))
        self.assertEqual(shell.hook_line("bash"), 'eval "$(umbrella shell-init bash)"')
        self.assertEqual(shell.hook_line("fish"), "umbrella shell-init fish | source")

    def test_rc_file(self):
        self.assertEqual(shell.rc_file("zsh"), self.home / ".zshrc")
        self.assertEqual(shell.rc_file("fish"), self.home / ".config" / "fish" / "config.fish")

    def test_install_hook(self):
        self.assertFalse(shell.hook_installed("zsh"))
        self.assertTrue(shell.install_hook("zsh"))
        self.assertTrue(shell.hook_installed("zsh"))
        self.assertFalse(shell.install_hook("zsh"))
        self.assertEqual(shell.rc_file("zsh").read_text().count("umbrella shell-init"), 1)

    def test_install_hook_appends_newline(self):
        self.write(".bashrc", "alias x=y")
        shell.install_hook("bash")
        self.assertTrue(shell.rc_file("bash").read_text().startswith("alias x=y\n\n# Added by Umbrella"))
        self.write(".config/fish/config.fish", "set x 1\n")
        shell.install_hook("fish")
        self.assertIn("set x 1\n\n# Added", shell.rc_file("fish").read_text())

    def test_env_script(self):
        changes = {"CLAUDE_CONFIG_DIR": "/a b/it's", "UMBRELLA_PROFILE": None}
        self.assertEqual(shell.env_script(changes, "zsh"),
                         "export CLAUDE_CONFIG_DIR='/a b/it'\"'\"'s'\nunset UMBRELLA_PROFILE\n")
        self.assertEqual(shell.env_script(changes, "fish"),
                         "set -gx CLAUDE_CONFIG_DIR '/a b/it\\'s'\nset -e UMBRELLA_PROFILE\n")

    def test_env_script_round_trips_through_sh(self):
        script = shell.env_script({"CLAUDE_CONFIG_DIR": "/tmp/$HOME `x` 'q'"}, "bash")
        out = subprocess.run(["/bin/sh", "-c", script + 'printf %s "$CLAUDE_CONFIG_DIR"'],
                             stdout=subprocess.PIPE, universal_newlines=True).stdout
        self.assertEqual(out, "/tmp/$HOME `x` 'q'")

    def test_is_on_path(self):
        self.assertFalse(shell.is_on_path("umbrella"))
        self.stub("umbrella", "true")
        self.assertTrue(shell.is_on_path("umbrella"))
        self.write("bin2/umbrella", "not executable", base=self.tmp)
        self.assertFalse(shell.is_on_path("umbrella", {"PATH": str(self.tmp / "bin2") + os.pathsep}))
        self.assertFalse(shell.is_on_path("umbrella", {}))


class RunnerTest(FakeHomeTest):
    def test_find_claude(self):
        self.assertIsNone(runner.find_claude())
        local = self.write(".claude/local/claude", "#!/bin/sh\n", mode=0o755)
        self.assertEqual(runner.find_claude(), str(local))
        stub = self.stub("claude", "true")
        self.assertEqual(runner.find_claude({"PATH": str(self.bin)}), str(stub))

    def test_find_claude_in_desktop_app(self):
        base = "Library/Application Support/Claude/claude-code/{}/claude.app/Contents/MacOS/claude"
        for version in ("2.1.9", "2.1.281", "beta"):
            self.write(base.format(version), "#!/bin/sh\n", mode=0o755)
        self.assertTrue(runner.find_claude({}).endswith(base.format("2.1.281")))

    def test_find_claude_ignores_non_executable(self):
        self.write(".local/bin/claude", "x", mode=0o644)
        self.assertIsNone(runner.find_claude({}))

    def test_profile_environment(self):
        base = {"CLAUDE_CONFIG_DIR": "/old", "KEEP": "1"}
        env = runner.profile_environment(profiles.Profile("personal"), base)
        self.assertEqual(env, {"KEEP": "1", "UMBRELLA_PROFILE": "personal"})
        env = runner.profile_environment(profiles.Profile("work", "/w"), base)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/w")
        self.assertIn("HOME", runner.profile_environment(profiles.Profile("p")))

    def test_run_claude(self):
        record = self.tmp / "record"
        self.stub("claude", 'echo "$CLAUDE_CONFIG_DIR|$UMBRELLA_PROFILE|$*" > {}; exit 3'.format(record))
        code = runner.run_claude(profiles.Profile("work", "/w"), ["--resume", "x y"])
        self.assertEqual(code, 3)
        self.assertEqual(record.read_text(), "/w|work|--resume x y\n")
        runner.run_claude(profiles.Profile("personal"), [], {"PATH": str(self.bin), "CLAUDE_CONFIG_DIR": "/old"})
        self.assertEqual(record.read_text(), "|personal|\n")

    def test_run_claude_missing(self):
        with self.assertRaises(UmbrellaError) as cm:
            runner.run_claude(profiles.Profile("p"), [])
        self.assertIn("PATH", cm.exception.hint)

    def test_keychain_service(self):
        self.assertEqual(runner.keychain_service(profiles.Profile("p")), "Claude Code-credentials")
        service = runner.keychain_service(profiles.Profile("w", "/w"))
        self.assertEqual(service, "Claude Code-credentials-" + hashlib.sha256(b"/w").hexdigest()[:8])

    def test_keychain_without_security_tool(self):
        self.assertIsNone(runner.keychain_has_login(profiles.Profile("p")))
        self.assertFalse(runner.keychain_delete_login(profiles.Profile("p")))

    def test_keychain_with_security_tool(self):
        log = self.tmp / "security.log"
        self.stub("security", 'echo "$@" >> {}; [ "$3" = "Claude Code-credentials" ]'.format(log))
        self.assertTrue(runner.keychain_has_login(profiles.Profile("p")))
        self.assertFalse(runner.keychain_has_login(profiles.Profile("w", "/w")))
        self.assertTrue(runner.keychain_delete_login(profiles.Profile("p")))
        self.assertIn("delete-generic-password -s Claude Code-credentials", log.read_text())
