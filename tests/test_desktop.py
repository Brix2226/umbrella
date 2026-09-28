import json
import os
import plistlib
import subprocess
import time
from pathlib import Path, PureWindowsPath
from unittest import mock

from helpers import FakeHomeTest
from test_cli import CliTest
from umbrella import desktop, paths, profiles
from umbrella.errors import UmbrellaError

REAL_MAC_APP_DIRS = desktop._mac_app_dirs

WSLPATH = r'''
import sys
root = {root!r}
flag, value = sys.argv[1], sys.argv[2]
if flag == "-u":
    if not value.startswith("C:\\"):
        sys.exit(1)
    print(root + "/" + value[3:].replace("\\", "/"))
else:
    if not value.startswith(root + "/"):
        sys.exit(1)
    print("C:\\" + value[len(root) + 1:].replace("/", "\\"))
'''

CMD = r'''
import sys
values = {values!r}
name = sys.argv[-1].split("%")[1]
print(values.get(name, "%" + name + "%"))
'''

OSACOMPILE = r'''
import os, plistlib, sys
args = sys.argv[1:]
target, script = args[args.index("-o") + 1], args[args.index("-e") + 1]
res = os.path.join(target, "Contents", "Resources")
os.makedirs(os.path.join(target, "Contents", "MacOS"))
os.makedirs(res)
open(os.path.join(res, "applet.icns"), "w").write("applet icon")
open(os.path.join(res, "script.txt"), "w").write(script)
with open(os.path.join(target, "Contents", "Info.plist"), "wb") as fh:
    plistlib.dump({"CFBundleExecutable": "applet"}, fh)
'''

CODESIGN = r'''
import os, sys
open({log!r}, "a").write(" ".join(sys.argv[1:]) + "\n")
if os.environ.get("FAIL_CODESIGN"):
    sys.stderr.write("bad signature\n")
    sys.exit(1)
'''

POWERSHELL = r'''
import sys
open({log!r}, "a").write(sys.argv[-1] + "\n")
print({out!r})
'''


class MacDesktopTest(FakeHomeTest):
    def setUp(self):
        super().setUp()
        self.app = self.tmp / "Apps" / "Claude.app"
        self.write("Contents/MacOS/Claude", "", base=self.app)
        self.write("Contents/Resources/electron.icns", "icon", base=self.app)
        self.work = profiles.Profile("work", str(self.tmp / "work-cfg"))
        self.personal = profiles.Profile("personal")
        self.sign_log = self.tmp / "codesign.log"
        self.pystub("osacompile", OSACOMPILE)
        self.pystub("codesign", CODESIGN.format(log=str(self.sign_log)))
        patcher = mock.patch.object(desktop, "_mac_app_dirs", return_value=[self.tmp / "Apps"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_find_app(self):
        self.assertEqual(desktop.mac_app(), self.app)
        self.assertEqual(desktop.mac_app({"UMBRELLA_DESKTOP_APP": str(self.app)}), self.app)
        self.assertIsNone(desktop.mac_app({"UMBRELLA_DESKTOP_APP": str(self.tmp)}))
        self.assertEqual(desktop._mac_app_dirs.return_value, [self.tmp / "Apps"])

    def test_default_app_dirs(self):
        self.assertEqual(REAL_MAC_APP_DIRS(), [Path("/Applications"), self.home / "Applications"])

    def test_commands(self):
        self.assertEqual(desktop.mac_command(self.personal, self.app), ["/usr/bin/open", "-a", str(self.app)])
        cmd = desktop.mac_command(self.work, self.app)
        self.assertEqual(cmd, [str(self.app / "Contents/MacOS/Claude"),
                               "--user-data-dir=" + str(self.home / ".umbrella/desktop/work")])

    def test_environment(self):
        env = desktop.mac_environment(self.work, {"CLAUDE_CONFIG_DIR": "/old", "X": "1"})
        self.assertEqual(env, {"CLAUDE_CONFIG_DIR": str(self.work.config_dir), "UMBRELLA_PROFILE": "work", "X": "1"})
        self.assertNotIn("CLAUDE_CONFIG_DIR", desktop.mac_environment(self.personal, {"CLAUDE_CONFIG_DIR": "/x"}))
        self.assertIn("HOME", desktop.mac_environment(self.personal))

    def test_plan_launch(self):
        launch = desktop.plan_launch(self.work, "macos")
        self.assertTrue(launch.first_run)
        self.assertEqual(oct(launch.data_dir.stat().st_mode & 0o777), "0o700")
        self.assertFalse(desktop.plan_launch(self.work, "macos").first_run)
        launch = desktop.plan_launch(self.personal, "macos")
        self.assertIsNone(launch.data_dir)
        self.assertFalse(launch.first_run)
        with mock.patch.object(paths, "detect_platform", return_value="macos"):
            self.assertEqual(desktop.plan_launch(self.personal).platform, "macos")

    def test_plan_launch_errors(self):
        with self.assertRaises(UmbrellaError) as cm:
            desktop.plan_launch(self.work, "linux")
        self.assertIn("WSL2", cm.exception.hint)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.plan_launch(self.work, "macos", {"UMBRELLA_DESKTOP_APP": "/nope"})
        self.assertIn("/Applications", str(cm.exception))

    def test_start_detaches(self):
        launch = desktop.plan_launch(self.work, "macos")
        with mock.patch("subprocess.Popen") as popen:
            desktop.start(launch)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], launch.command)
        self.assertTrue(kwargs["start_new_session"])
        self.assertIsNone(kwargs["cwd"])
        self.assertEqual(kwargs["env"]["CLAUDE_CONFIG_DIR"], str(self.work.config_dir))

    def test_launcher(self):
        target = desktop.create_shortcut(self.work, "macos")
        self.assertEqual(target, self.home / "Applications" / "Claude (work).app")
        info = plistlib.loads((target / "Contents/Info.plist").read_bytes())
        self.assertEqual(info["CFBundleExecutable"], "applet")
        self.assertEqual(info["CFBundleName"], "Claude (work)")
        self.assertEqual(info["CFBundleIdentifier"], "dev.umbrella.claude.work")
        self.assertTrue(info["LSUIElement"])
        self.assertEqual((target / "Contents/Resources/applet.icns").read_text(), "icon")
        script = (target / "Contents/Resources/script.txt").read_text()
        self.assertTrue(script.startswith('do shell script "export CLAUDE_CONFIG_DIR='))
        self.assertIn("--user-data-dir=", script)
        self.assertEqual(self.sign_log.read_text(), "--force --sign - {}\n".format(target))
        # Recreating replaces it; without Claude's icon the applet keeps its own.
        (self.app / "Contents/Resources/electron.icns").unlink()
        desktop.create_shortcut(self.work, "macos")
        self.assertEqual((target / "Contents/Resources/applet.icns").read_text(), "applet icon")

    def test_applescript_quoting(self):
        self.assertEqual(desktop._applescript_string('a "b" \\c'), '"a \\"b\\" \\\\c"')

    def test_launch_shell(self):
        self.assertEqual(desktop.mac_launch_shell(self.personal, self.app),
                         "export UMBRELLA_PROFILE='personal'; /usr/bin/nohup '/usr/bin/open' '-a' '{}' "
                         ">/dev/null 2>&1 &".format(self.app))

    def test_launch_shell_really_starts_claude(self):
        """The launcher's shell command starts the app binary with the right settings and returns."""
        record = self.tmp / "record"
        binary = self.app / "Contents/MacOS/Claude"
        binary.write_text('#!/bin/sh\necho "$CLAUDE_CONFIG_DIR|$UMBRELLA_PROFILE|$*" > {}\n'.format(record))
        binary.chmod(0o755)
        subprocess.run(["/bin/sh", "-c", desktop.mac_launch_shell(self.work, self.app)], check=True)
        for _ in range(100):
            if record.exists() and record.read_text():
                break
            time.sleep(0.05)
        self.assertEqual(record.read_text().strip(), "{}|work|--user-data-dir={}".format(
            self.work.config_dir, self.home / ".umbrella/desktop/work"))

    def test_launcher_tool_failures(self):
        os.environ["FAIL_CODESIGN"] = "1"
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "macos")
        self.assertIn("bad signature", str(cm.exception))
        (self.bin / "codesign").write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "macos")
        self.assertIn("unknown error", str(cm.exception))
        (self.bin / "osacompile").unlink()
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "macos")
        self.assertIn("osacompile", str(cm.exception))

    def test_shortcut_errors(self):
        with self.assertRaises(UmbrellaError):
            desktop.create_shortcut(self.work, "macos", {"UMBRELLA_DESKTOP_APP": "/nope"})
        with self.assertRaises(UmbrellaError):
            desktop.create_shortcut(self.work, "linux")

    def test_remove_artifacts(self):
        desktop.plan_launch(self.work, "macos")
        desktop.create_shortcut(self.work, "macos")
        removed = desktop.remove_artifacts(self.work, "macos")
        self.assertEqual(removed, ["~/.umbrella/desktop/work", "~/Applications/Claude (work).app"])
        self.assertEqual(desktop.remove_artifacts(self.work, "macos"), [])
        self.assertEqual(desktop.remove_artifacts(self.personal, "macos"), [])
        self.assertEqual(desktop.remove_artifacts(self.work, "linux"), [])
        self.assertIsInstance(desktop.remove_artifacts(self.work), list)


class WindowsDesktopTest(FakeHomeTest):
    def setUp(self):
        super().setUp()
        self.c = self.tmp / "c"
        self.local = self.c / "Users/me/AppData/Local"
        self.values = {"LOCALAPPDATA": r"C:\Users\me\AppData\Local", "APPDATA": r"C:\Users\me\AppData\Roaming"}
        self.pystub("wslpath", WSLPATH.format(root=str(self.c)))
        self.set_cmd(self.values)
        self.work = profiles.Profile("work", "/home/me/.umbrella/profiles/work")
        self.personal = profiles.Profile("personal")

    def set_cmd(self, values):
        self.pystub("cmd.exe", CMD.format(values=values))

    def install_app(self, *relpaths):
        for rel in relpaths:
            self.write(rel, "exe", base=self.local)

    def test_helpers(self):
        self.assertEqual(desktop.windows_env("LOCALAPPDATA"), self.values["LOCALAPPDATA"])
        self.assertIsNone(desktop.windows_env("NOPE"))
        self.assertEqual(desktop.to_wsl(r"C:\x\y"), str(self.c / "x/y"))
        self.assertEqual(desktop.to_windows(self.c / "x"), r"C:\x")
        self.assertIsNone(desktop.to_windows("/elsewhere"))
        self.assertEqual(str(desktop.win_umbrella_root()), r"C:\Users\me\AppData\Local\Umbrella")
        self.assertEqual(str(desktop.win_data_dir(self.work)), r"C:\Users\me\AppData\Local\Umbrella\desktop\work")
        self.assertEqual(str(desktop.win_config_dir(self.work)), r"C:\Users\me\AppData\Local\Umbrella\claude\work")

    def test_run_failures(self):
        (self.bin / "cmd.exe").unlink()
        self.assertIsNone(desktop.windows_env("LOCALAPPDATA"))
        with self.assertRaises(UmbrellaError) as cm:
            desktop.win_umbrella_root()
        self.assertIn("interop", cm.exception.hint)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(desktop.to_wsl("C:\\x"))
        with mock.patch("os.path.isdir", return_value=True), mock.patch("subprocess.run") as run, \
                mock.patch.object(desktop.shutil, "which", return_value="/bin/wslpath"):
            run.return_value = subprocess.CompletedProcess([], 0, stdout="C:\\x\n")
            self.assertEqual(desktop.to_windows("/c/x"), "C:\\x")
        self.assertEqual(run.call_args[1]["cwd"], "/mnt/c")

    def test_find_app_prefers_newest_version(self):
        self.assertIsNone(desktop.windows_app())
        self.install_app("AnthropicClaude/claude.exe")
        self.assertEqual(desktop.windows_app(), self.local / "AnthropicClaude/claude.exe")
        self.install_app("AnthropicClaude/app-0.9.1/claude.exe", "AnthropicClaude/app-0.10.0/claude.exe",
                         "AnthropicClaude/app-x/claude.exe")
        self.assertEqual(desktop.windows_app(), self.local / "AnthropicClaude/app-0.10.0/claude.exe")

    def test_find_app_other_layouts_and_override(self):
        self.install_app("Microsoft/WindowsApps/Claude.exe")
        self.assertEqual(desktop.windows_app(), self.local / "Microsoft/WindowsApps/Claude.exe")
        exe = self.write("claude.exe", "x", base=self.tmp)
        self.assertEqual(desktop.windows_app({"UMBRELLA_DESKTOP_APP": str(exe)}), exe)
        self.assertIsNone(desktop.windows_app({"UMBRELLA_DESKTOP_APP": str(self.tmp / "none.exe")}))
        self.set_cmd({"LOCALAPPDATA": "D:\\unmapped"})
        self.assertIsNone(desktop.windows_app())
        self.set_cmd({})
        self.assertIsNone(desktop.windows_app())

    def test_environment(self):
        env = desktop.windows_environment(self.work, {"WSLENV": "FOO/p:CLAUDE_CONFIG_DIR/p", "CLAUDE_CONFIG_DIR": "/x"})
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], r"C:\Users\me\AppData\Local\Umbrella\claude\work")
        self.assertEqual(env["WSLENV"], "FOO/p:CLAUDE_CONFIG_DIR:UMBRELLA_PROFILE")
        env = desktop.windows_environment(self.personal, {"CLAUDE_CONFIG_DIR": "/x"})
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["WSLENV"], "UMBRELLA_PROFILE")
        self.assertIn("HOME", desktop.windows_environment(self.personal))

    def test_plan_launch(self):
        self.install_app("AnthropicClaude/claude.exe")
        exe = str(self.local / "AnthropicClaude/claude.exe")
        launch = desktop.plan_launch(self.work, "wsl")
        self.assertEqual(launch.command, [exe, r"--user-data-dir=C:\Users\me\AppData\Local\Umbrella\desktop\work"])
        self.assertTrue(launch.first_run)
        (self.local / "Umbrella/desktop/work").mkdir(parents=True)
        self.assertFalse(desktop.plan_launch(self.work, "wsl").first_run)
        launch = desktop.plan_launch(self.personal, "wsl")
        self.assertEqual(launch.command, [exe])
        self.assertIsNone(launch.data_dir)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.plan_launch(self.work, "wsl", {"UMBRELLA_DESKTOP_APP": "/none"})
        self.assertIn("Windows", str(cm.exception))

    def test_start_uses_windows_cwd(self):
        self.install_app("AnthropicClaude/claude.exe")
        launch = desktop.plan_launch(self.work, "wsl")
        with mock.patch("os.path.isdir", return_value=True), mock.patch("subprocess.Popen") as popen:
            desktop.start(launch)
        self.assertEqual(popen.call_args[1]["cwd"], "/mnt/c")

    def test_shortcut(self):
        log = self.tmp / "ps.log"
        self.pystub("powershell.exe", POWERSHELL.format(log=str(log), out="ok"))
        self.install_app("AnthropicClaude/claude.exe")
        link = desktop.create_shortcut(self.work, "wsl")
        self.assertEqual(str(link), r"C:\Users\me\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Claude (work).lnk")
        vbs = (self.local / "Umbrella/launchers/claude-work.vbs").read_text()
        self.assertIn('env("CLAUDE_CONFIG_DIR") = "C:\\Users\\me\\AppData\\Local\\Umbrella\\claude\\work"', vbs)
        self.assertIn('shell.Run """C:\\Users\\me\\AppData\\Local\\AnthropicClaude\\claude.exe"" '
                      '""--user-data-dir=C:\\Users\\me\\AppData\\Local\\Umbrella\\desktop\\work""", 1, False', vbs)
        self.assertIn(b"\r\n", (self.local / "Umbrella/launchers/claude-work.vbs").read_bytes())
        ps = log.read_text()
        self.assertIn("CreateShortcut('C:\\Users\\me\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\"
                      "Claude (work).lnk')", ps)
        self.assertIn("$s.TargetPath = 'wscript.exe'", ps)
        self.assertIn("Umbrella profile ''work''", ps)

        desktop.create_shortcut(self.personal, "wsl")
        vbs = (self.local / "Umbrella/launchers/claude-personal.vbs").read_text()
        self.assertNotIn("CLAUDE_CONFIG_DIR", vbs)
        self.assertIn('shell.Run """C:\\Users\\me\\AppData\\Local\\AnthropicClaude\\claude.exe""", 1, False', vbs)

    def test_shortcut_failures(self):
        self.install_app("AnthropicClaude/claude.exe")
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "wsl")  # no powershell.exe
        self.assertIn("umbrella desktop work", cm.exception.hint)
        with self.assertRaises(UmbrellaError):
            desktop.create_shortcut(self.work, "wsl", {"UMBRELLA_DESKTOP_APP": "/none"})
        exe = self.write("claude.exe", "x", base=self.tmp)  # outside the mapped drive: no Windows path
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "wsl", {"UMBRELLA_DESKTOP_APP": str(exe)})
        self.assertIn("Windows path", str(cm.exception))
        self.values["LOCALAPPDATA"] = "D:\\unmapped"
        self.set_cmd(self.values)
        exe = self.write("claude.exe", "x", base=self.c)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "wsl", {"UMBRELLA_DESKTOP_APP": str(exe)})
        self.assertIn("from WSL", str(cm.exception))

    def test_remove_artifacts(self):
        self.pystub("powershell.exe", POWERSHELL.format(log=str(self.tmp / "ps.log"), out="ok"))
        self.install_app("AnthropicClaude/claude.exe")
        desktop.create_shortcut(self.work, "wsl")
        (self.local / "Umbrella/desktop/work").mkdir(parents=True)
        self.write("Microsoft/Windows/Start Menu/Programs/Claude (work).lnk", "lnk", base=self.c / "Users/me/AppData/Roaming")
        removed = desktop.remove_artifacts(self.work, "wsl")
        self.assertEqual(len(removed), 3)
        self.assertFalse((self.local / "Umbrella/desktop/work").exists())
        self.assertEqual(desktop.remove_artifacts(self.work, "wsl"), [])
        self.set_cmd({"LOCALAPPDATA": self.values["LOCALAPPDATA"]})
        self.assertEqual(desktop.remove_artifacts(self.work, "wsl"), [])
        self.set_cmd({})
        self.assertEqual(desktop.remove_artifacts(self.work, "wsl"), [])


class DesktopCliTest(CliTest):
    def setUp(self):
        super().setUp()
        self.app = self.tmp / "Apps" / "Claude.app"
        self.write("Contents/MacOS/Claude", "", base=self.app)
        os.environ["UMBRELLA_DESKTOP_APP"] = str(self.app)
        self.pystub("osacompile", OSACOMPILE)
        self.pystub("codesign", CODESIGN.format(log=str(self.tmp / "codesign.log")))
        popen = mock.patch("umbrella.desktop.start")
        self.popen = popen.start()
        self.addCleanup(popen.stop)
        plat = mock.patch.object(paths, "detect_platform", return_value="macos")
        plat.start()
        self.addCleanup(plat.stop)

    def test_open_profiles(self):
        work = self.setup_two_profiles()
        code, out, _ = self.run_cli("desktop", "work")
        self.assertEqual(code, 0)
        self.assertIn("Opening Claude Desktop as profile 'work'", out)
        self.assertIn("~/.umbrella/desktop/work", out)
        self.assertIn("First time?", out)
        self.assertEqual(self.popen.call_args[0][0].env["CLAUDE_CONFIG_DIR"], str(work.config_dir))
        self.assertNotIn("First time?", self.run_cli("desktop", "work")[1])
        code, out, _ = self.run_cli("desktop")
        self.assertIn("your normal Claude app (profile 'personal')", out)
        code, out, _ = self.run_cli("desktop", env={"CLAUDE_CONFIG_DIR": str(work.config_dir)})
        self.assertIn("profile 'work'", out)

    def test_open_on_windows(self):
        self.setup_two_profiles()
        with mock.patch.object(paths, "detect_platform", return_value="wsl"), \
                mock.patch("umbrella.desktop.plan_launch") as plan, \
                mock.patch("umbrella.desktop.win_config_dir", return_value=PureWindowsPath(r"C:\U\claude\work")):
            plan.return_value = desktop.Launch("wsl", ["x"], {}, PureWindowsPath(r"C:\U\desktop\work"), False)
            code, out, _ = self.run_cli("desktop", "work")
        self.assertIn(r"C:\U\desktop\work", out)
        self.assertIn("separate from the CLI in WSL", out)

    def test_no_profile_to_open(self):
        (self.home / ".claude").mkdir()
        self.run_cli("init", "--name", "personal")
        self.run_cli("add", "work")
        self.run_cli("remove", "personal", "--yes")
        code, _, err = self.run_cli("desktop")
        self.assertEqual(code, 1)
        self.assertIn("umbrella desktop work", err)

    def test_shortcut(self):
        self.setup_two_profiles()
        code, out, _ = self.run_cli("desktop", "work", "--shortcut")
        self.assertEqual(code, 0)
        self.assertIn("Created 'Claude (work)' in ~/Applications", out)
        self.popen.assert_not_called()
        with mock.patch.object(paths, "detect_platform", return_value="wsl"), \
                mock.patch("umbrella.desktop.create_shortcut", return_value=PureWindowsPath(r"C:\x.lnk")):
            self.assertIn("Start Menu", self.run_cli("desktop", "work", "--shortcut")[1])

    def test_remove_cleans_desktop(self):
        self.setup_two_profiles()
        self.run_cli("desktop", "work", "--shortcut")
        self.run_cli("desktop", "work")
        code, out, _ = self.run_cli("remove", "work", "--yes", "--no-backup")
        self.assertIn("Removed its Desktop app data: ~/.umbrella/desktop/work", out)
        self.assertFalse((self.home / "Applications" / "Claude (work).app").exists())

    def test_doctor_reports_desktop(self):
        self.assertIn("Claude Desktop app found", self.run_cli("doctor")[1])
        os.environ["UMBRELLA_DESKTOP_APP"] = "/nope"
        self.assertIn("Couldn't find the Claude Desktop app", self.run_cli("doctor")[1])
        with mock.patch.object(paths, "detect_platform", return_value="linux"):
            self.assertNotIn("Desktop", self.run_cli("doctor")[1])
