import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from helpers import FakeHomeTest, TTYStringIO
from umbrella import backup, cli, paths, profiles, shell


class CliTest(FakeHomeTest):
    def run_cli(self, *argv, stdin="", tty=False, env=None):
        out, err = io.StringIO(), io.StringIO()
        full_env = dict(os.environ)
        full_env.update(env or {})
        code = cli.main(list(argv), stdout=out, stderr=err,
                        stdin=TTYStringIO(stdin) if tty else io.StringIO(stdin), env=full_env)
        return code, out.getvalue(), err.getvalue()

    def setup_two_profiles(self):
        self.claude_tree()
        self.sign_in(self.home / ".claude.json", "me@home.com", "Home")
        self.assertEqual(self.run_cli("init", "--name", "personal")[0], 0)
        self.assertEqual(self.run_cli("add", "work")[0], 0)
        work = profiles.Registry().get("work")
        self.sign_in(work.state_file, "me@work.com", "Work Inc")
        return work


class BasicsTest(CliTest):
    def test_help_and_version(self):
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertIn("Quick start", out)
        with self.assertRaises(SystemExit):
            self.run_cli("--version")

    def test_main_uses_sys_defaults(self):
        with mock.patch.object(sys, "argv", ["umbrella", "current"]), \
                mock.patch("sys.stdout", io.StringIO()) as out, mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(cli.main(), 1)
        self.assertIn("isn't registered", out.getvalue())

    def test_main_entry_exits(self):
        with mock.patch.object(cli, "main", return_value=3), self.assertRaises(SystemExit) as cm:
            cli.main_entry()
        self.assertEqual(cm.exception.code, 3)

    def test_errors_are_friendly(self):
        code, _, err = self.run_cli("list")
        self.assertEqual(code, 1)
        self.assertIn("isn't set up yet", err)
        self.assertIn("umbrella init", err)
        code, _, err = self.run_cli("__env", "use")
        self.assertIn("which profile", err)

    def test_error_without_hint(self):
        (self.home / ".claude").mkdir()
        self.run_cli("init", "--name", "personal")
        code, _, err = self.run_cli("add", "personal")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().count("\n"), 0)

    def test_keyboard_interrupt(self):
        with mock.patch.object(cli, "cmd_doctor", side_effect=KeyboardInterrupt):
            code, _, err = self.run_cli("doctor")
        self.assertEqual(code, 130)
        self.assertIn("Cancelled", err)

    def test_main_module(self):
        import runpy
        with mock.patch.object(cli, "main_entry") as entry:
            runpy.run_module("umbrella", run_name="__main__")
        entry.assert_called_once_with()

    def test_python_dash_m(self):
        env = dict(os.environ, PYTHONPATH=str(Path(cli.__file__).parents[1]))
        result = subprocess.run([sys.executable, "-m", "umbrella", "--version"], env=env,
                                stdout=subprocess.PIPE, universal_newlines=True)
        self.assertIn("umbrella 0.1.0", result.stdout)


class InitTest(CliTest):
    def test_init_with_existing_setup_and_hook(self):
        self.claude_tree()
        self.sign_in(self.home / ".claude.json")
        code, out, _ = self.run_cli("init", stdin="home\ny\n", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("signed in as me@example.com", out)
        self.assertIn("profile 'home'", out)
        self.assertIn("Added.", out)
        self.assertTrue(shell.hook_installed("zsh"))
        code, out, _ = self.run_cli("init")
        self.assertIn("already set up with 1 profile", out)

    def test_init_without_claude_and_declined_hook(self):
        code, out, _ = self.run_cli("init", "--shell", "bash")
        self.assertEqual(code, 0)
        self.assertIn("No existing Claude Code setup", out)
        self.assertIn('eval "$(umbrella shell-init bash)"', out)
        self.assertTrue(profiles.Registry().exists)

    def test_init_hook_already_present(self):
        shell.install_hook("zsh")
        (self.home / ".claude").mkdir()
        code, out, _ = self.run_cli("init", "--name", "p", "--yes")
        self.assertIn("Shell hook already in ~/.zshrc", out)


class ProfileCommandsTest(CliTest):
    def test_add_with_share_and_list(self):
        self.setup_two_profiles()
        code, out, _ = self.run_cli("add", "lab", "--share", "settings,skills,commands")
        self.assertEqual(code, 0)
        self.assertIn("Sharing with your default setup: settings.json, skills", out)
        self.assertIn("Not shared", out)
        self.assertIn("umbrella login lab", out)

        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("me@home.com (Home)", out)
        self.assertIn("me@work.com (Work Inc)", out)
        self.assertIn("personal (default)", out)
        code, out, _ = self.run_cli("list", env={"CLAUDE_CONFIG_DIR": "/somewhere/else"})
        self.assertIn("isn't using any of these", out)
        rows = json.loads(self.run_cli("list", "--json")[1])
        self.assertEqual([r["name"] for r in rows], ["lab", "personal", "work"])
        self.assertTrue(rows[1]["active"])

    def test_add_all_shared_ok(self):
        self.setup_two_profiles()
        code, out, _ = self.run_cli("add", "x", "--share", "settings")
        self.assertNotIn("Not shared", out)
        code, out, _ = self.run_cli("add", "y", "--share", "commands")
        self.assertNotIn("Sharing with", out)

    def test_add_with_custom_path_and_errors(self):
        self.setup_two_profiles()
        code, out, _ = self.run_cli("add", "ext", "--path", str(self.tmp / "ext-claude"))
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp / "ext-claude").is_dir())
        self.assertEqual(self.run_cli("add", "work")[0], 1)
        self.assertEqual(self.run_cli("add", "Bad Name")[0], 1)
        self.assertEqual(self.run_cli("add", "z", "--share", "nope")[0], 1)

    def test_list_empty(self):
        self.run_cli("init")
        code, out, _ = self.run_cli("list")
        self.assertIn("No profiles yet", out)

    def test_current(self):
        work = self.setup_two_profiles()
        code, out, _ = self.run_cli("current")
        self.assertEqual(code, 0)
        self.assertIn("personal", out)
        self.assertIn("me@home.com", out)
        code, out, _ = self.run_cli("current", env={"CLAUDE_CONFIG_DIR": str(work.config_dir)})
        self.assertIn("me@work.com", out)
        code, out, _ = self.run_cli("current", env={"CLAUDE_CONFIG_DIR": "/x"})
        self.assertEqual(code, 1)
        self.assertIn("isn't an Umbrella profile", out)
        os.remove(str(work.state_file))
        code, out, _ = self.run_cli("current", env={"CLAUDE_CONFIG_DIR": str(work.config_dir)})
        self.assertIn("not signed in", out)

    def test_login_without_claude_on_wsl(self):
        self.setup_two_profiles()
        with mock.patch.object(paths, "detect_platform", return_value="wsl"):
            code, out, err = self.run_cli("login", "work")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")  # no "Opening Claude Code..." before the error
        self.assertIn("isn't installed inside WSL", err)

    def test_login_and_run(self):
        work = self.setup_two_profiles()
        record = self.tmp / "record"
        self.stub("claude", 'echo "$CLAUDE_CONFIG_DIR $*" > {}'.format(record))
        code, out, _ = self.run_cli("login", "work")
        self.assertEqual(code, 0)
        self.assertIn("signed in as me@work.com", out)
        self.assertEqual(record.read_text().strip(), str(work.config_dir))
        os.remove(str(work.state_file))
        code, out, _ = self.run_cli("login", "work")
        self.assertIn("isn't signed in yet", out)

        self.assertEqual(self.run_cli("run", "work", "--", "--resume")[0], 0)
        self.assertEqual(record.read_text().strip(), "{} --resume".format(work.config_dir))
        self.run_cli("run", "personal", "-p", "hi")
        self.assertEqual(record.read_text().strip(), "-p hi")


class UseAndEnvTest(CliTest):
    def test_use_without_hook(self):
        self.setup_two_profiles()
        code, out, _ = self.run_cli("use", "work")
        self.assertEqual(code, 1)
        self.assertIn('eval "$(umbrella __env use work)"', out)
        code, out, _ = self.run_cli("use", "work", "--default", env={"SHELL": "/usr/bin/fish"})
        self.assertIn("New shells will start on 'work'", out)
        self.assertIn("umbrella __env --shell fish use work | source", out)
        self.assertEqual(profiles.Registry().default, "work")

    def test_off_without_hook(self):
        code, out, _ = self.run_cli("off")
        self.assertEqual(code, 1)
        self.assertIn("unset CLAUDE_CONFIG_DIR", out)

    def test_env_use_off_startup(self):
        work = self.setup_two_profiles()
        code, out, err = self.run_cli("__env", "--shell", "bash", "use", "work")
        self.assertEqual(code, 0)
        self.assertIn("export CLAUDE_CONFIG_DIR=" + str(work.config_dir), out)
        self.assertIn("export UMBRELLA_PROFILE=work", out)
        self.assertIn("Switched to 'work' (me@work.com)", err)

        code, out, err = self.run_cli("__env", "use", "personal", "--default")
        self.assertIn("unset CLAUDE_CONFIG_DIR", out)
        self.assertEqual(profiles.Registry().default, "personal")

        code, out, err = self.run_cli("__env", "--shell", "fish", "off")
        self.assertIn("set -e CLAUDE_CONFIG_DIR", out)
        self.assertIn("Back to Claude's default", err)

        self.assertIn("UMBRELLA_PROFILE=personal", self.run_cli("__env", "--startup")[1])
        self.assertEqual(self.run_cli("__env", "--startup", env={"CLAUDE_CONFIG_DIR": "/x"})[1], "")

        os.remove(str(work.state_file))
        self.assertIn("not signed in yet", self.run_cli("__env", "use", "work")[2])
        self.assertEqual(self.run_cli("__env", "use", "nope")[0], 1)

    def test_env_startup_uninitialized(self):
        self.assertEqual(self.run_cli("__env", "--startup"), (0, "", ""))

    def test_shell_init(self):
        self.assertIn("__env --shell zsh", self.run_cli("shell-init")[1])
        self.assertIn("function umbrella", self.run_cli("shell-init", "fish")[1])

    def test_hook_really_switches_a_shell(self):
        """End to end: source the bash hook and check `umbrella use` changes the environment."""
        work = self.setup_two_profiles()
        src = Path(cli.__file__).parents[1]
        self.stub("umbrella", 'PYTHONPATH="{}" exec "{}" -m umbrella "$@"'.format(src, sys.executable))
        script = ('eval "$(umbrella shell-init bash)"; echo "start=$UMBRELLA_PROFILE"; '
                  'umbrella use work; echo "dir=$CLAUDE_CONFIG_DIR"; umbrella off; echo "off=$CLAUDE_CONFIG_DIR"')
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + "/usr/bin:/bin")
        result = subprocess.run(["bash", "-c", script], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
        self.assertIn("start=personal", result.stdout)
        self.assertIn("dir={}".format(work.config_dir), result.stdout)
        self.assertIn("off=\n", result.stdout)
        self.assertIn("Switched to 'work'", result.stderr)


class RemoveTest(CliTest):
    def test_remove_managed_profile(self):
        work = self.setup_two_profiles()
        log = self.tmp / "security.log"
        self.stub("security", 'echo "$1" >> {}; exit 0'.format(log))
        code, out, _ = self.run_cli("remove", "work")
        self.assertEqual(code, 1)
        self.assertIn("Nothing changed", out)
        code, out, _ = self.run_cli("remove", "work", stdin="y\n", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("Backed up first", out)
        self.assertIn("macOS Keychain", out)
        self.assertFalse(work.config_dir.exists())
        self.assertEqual(profiles.Registry().names(), ["personal"])
        self.assertIn("delete-generic-password", log.read_text())
        self.assertEqual(len(list(paths.backups_dir().iterdir())), 1)

    def test_remove_without_backup_or_keychain(self):
        work = self.setup_two_profiles()
        code, out, _ = self.run_cli("remove", "work", "--yes", "--no-backup")
        self.assertEqual(code, 0)
        self.assertNotIn("Backed up", out)
        self.assertNotIn("Keychain", out)
        self.assertFalse(paths.backups_dir().exists())

    def test_remove_keeps_files(self):
        work = self.setup_two_profiles()
        code, out, _ = self.run_cli("remove", "work", "--yes", "--keep-files")
        self.assertTrue(work.config_dir.exists())
        code, out, _ = self.run_cli("remove", "personal")
        self.assertIn("Its files stay where they are", out)
        self.assertEqual(self.run_cli("remove", "personal", "--yes")[0], 0)
        self.assertTrue((self.home / ".claude").exists())


class InspectBackupRestoreTest(CliTest):
    def test_inspect_defaults_without_init(self):
        self.claude_tree()
        code, out, _ = self.run_cli("inspect")
        self.assertEqual(code, 0)
        self.assertIn("profile 'default'", out)
        code, out, _ = self.run_cli("inspect", env={"CLAUDE_CONFIG_DIR": str(self.tmp / "custom")})
        self.assertIn("(CLAUDE_CONFIG_DIR)", out)
        self.assertIn("doesn't exist yet", out)

    def test_inspect_profiles(self):
        work = self.setup_two_profiles()
        self.assertIn("profile 'work'", self.run_cli("inspect", "--profile", "work", "--all")[1])
        data = json.loads(self.run_cli("inspect", "--json")[1])
        self.assertEqual(data["profile"], "personal")
        self.assertNotIn("SHOULD-NEVER-BE-SHOWN", json.dumps(data))
        out = self.run_cli("inspect", env={"CLAUDE_CONFIG_DIR": "/nowhere"})[1]
        self.assertIn("profile 'personal'", out)  # falls back to the registry default
        self.assertEqual(self.run_cli("inspect", "--profile", "nope")[0], 1)

    def test_backup_and_restore(self):
        work = self.setup_two_profiles()
        self.write(".credentials.json", "{}", base=work.config_dir)
        archive = self.tmp / "work.tar.gz"
        code, out, _ = self.run_cli("backup", "--profile", "work", "-o", str(archive))
        self.assertEqual(code, 0)
        self.assertIn("Left out your login tokens", out)
        self.assertNotIn("contains your login tokens", out)

        code, out, _ = self.run_cli("backup", "--profile", "work", "--include-secrets")
        self.assertIn("contains your login tokens", out)

        with mock.patch.object(backup, "create_backup",
                               return_value=backup.BackupResult(archive, 1, 1, [], ["config/x"])):
            self.assertIn("Couldn't read 1 file", self.run_cli("backup")[1])

        work.state_file.write_text("changed")
        code, out, _ = self.run_cli("restore", str(archive), "--profile", "work")
        self.assertEqual(code, 1)
        self.assertIn("Nothing changed", out)
        code, out, _ = self.run_cli("restore", str(archive), "--profile", "work", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Saved your current state first", out)
        self.assertIn("Restored", out)
        self.assertIn("me@work.com", work.state_file.read_text())

    def test_restore_reports_skipped(self):
        work = self.setup_two_profiles()
        os.symlink(str(self.home / ".claude" / "skills"), str(work.config_dir / "skills"))
        archive = self.tmp / "p.tar.gz"
        self.run_cli("backup", "--profile", "personal", "-o", str(archive))
        archive2 = self.tmp / "w.tar.gz"
        self.run_cli("backup", "--profile", "work", "-o", str(archive2))
        code, out, _ = self.run_cli("restore", str(archive), "--profile", "work", "--yes")
        self.assertIn("would land outside this profile", out)
        code, out, _ = self.run_cli("restore", str(archive2), "--profile", "work", "--yes")
        self.assertIn("Skipped 1 link", out)
        self.assertIn("includes", self.run_cli("restore", str(archive2), "--profile", "work")[1] + "includes")
        self.assertEqual(self.run_cli("restore", str(self.tmp / "missing.tgz"))[0], 1)

    def test_restore_shows_secret_flag(self):
        work = self.setup_two_profiles()
        archive = self.tmp / "s.tar.gz"
        self.run_cli("backup", "--profile", "work", "--include-secrets", "-o", str(archive))
        self.assertIn("includes login tokens", self.run_cli("restore", str(archive))[1])


class DoctorTest(CliTest):
    def test_doctor_problems(self):
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 1)
        self.assertIn("isn't on your PATH", out)
        self.assertIn("isn't set up yet", out)
        self.assertIn("1 problem to fix", out)

    def test_doctor_all_good(self):
        work = self.setup_two_profiles()
        self.stub("claude", "true")
        self.stub("umbrella", "true")
        shell.install_hook("zsh")
        code, out, _ = self.run_cli("doctor")
        self.assertEqual(code, 0, out)
        self.assertIn("signed in as me@work.com", out)
        self.assertIn("All good.", out)

        os.chmod(str(work.config_dir), 0o755)
        os.remove(str(work.state_file))
        self.run_cli("add", "fresh")
        (paths.profiles_dir() / "fresh").rmdir()
        code, out, _ = self.run_cli("doctor")
        self.assertIn("can be read by other users", out)
        self.assertIn("Profile 'work' isn't signed in", out)
        self.assertIn("doesn't exist yet", out)
        self.assertIn("3 suggestions above are worth a look", out)
        (paths.profiles_dir() / "fresh").mkdir(mode=0o700)
        os.chmod(str(work.config_dir), 0o700)
        self.sign_in(work.state_file)
        self.sign_in(paths.profiles_dir() / "fresh" / ".claude.json")
        self.assertIn("1 suggestion above is worth a look", self.run_cli("doctor", env={"SHELL": "/bin/bash"})[1])

    def test_doctor_old_python(self):
        with mock.patch.object(sys, "version_info", (3, 7, 0)):
            code, out, _ = self.run_cli("doctor")
        self.assertIn("needs Python 3.8", out)
