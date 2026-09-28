import json
import os
import plistlib
import sys
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
import json, sys
conf = json.load(open({conf!r}))
open({log!r}, "a").write(sys.argv[-1] + "\n")
if conf.get("fail"):
    sys.exit(1)
if "Get-AppxPackage" in sys.argv[-1]:
    print(conf.get("family", ""))
else:
    print("ok")
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
    """Mirrors a real WSL2 setup: Windows folders are NOT on WSL's PATH, and Claude is a Store app."""

    USER = "C:\\Users\\TaeOn"
    LOCAL = USER + "\\AppData\\Local"
    STORE_ALIAS = LOCAL + "\\Microsoft\\WindowsApps\\Claude_pzs8sxrjxfjjc\\claude-desktop.exe"

    def setUp(self):
        super().setUp()
        self.c = self.tmp / "c"
        self.local = self.c / "Users/TaeOn/AppData/Local"
        self.values = {"USERPROFILE": self.USER, "LOCALAPPDATA": self.LOCAL,
                       "APPDATA": self.USER + "\\AppData\\Roaming"}
        self.ps_log = self.tmp / "ps.log"
        self.ps_conf = self.tmp / "ps.json"
        self.set_powershell()
        self.pystub("wslpath", WSLPATH.format(root=str(self.c)))
        self.set_cmd(self.values)
        self.work = profiles.Profile("work", "/home/tai/.umbrella/profiles/work")
        self.personal = profiles.Profile("personal")

    def windows_stub(self, relpath, code):
        path = self.c / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!{}\n{}\n".format(sys.executable, code))
        path.chmod(0o755)
        return path

    def set_cmd(self, values):
        self.windows_stub("Windows/System32/cmd.exe", CMD.format(values=values))

    def set_powershell(self, **conf):
        self.ps_conf.write_text(json.dumps(conf))
        self.windows_stub("Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                          POWERSHELL.format(conf=str(self.ps_conf), log=str(self.ps_log)))

    def install(self, *relpaths):
        for rel in relpaths:
            self.write(rel, "exe", base=self.local)

    def install_store_app(self):
        self.install("Microsoft/WindowsApps/claude-desktop.exe",
                     "Microsoft/WindowsApps/Claude_pzs8sxrjxfjjc/claude-desktop.exe")

    # --- reaching Windows ------------------------------------------------------------------------

    def test_windows_tools_found_off_path(self):
        self.assertEqual(desktop.windows_tool("cmd.exe"), str(self.c / "Windows/System32/cmd.exe"))
        self.assertEqual(desktop.windows_env("LOCALAPPDATA"), self.LOCAL)
        self.assertIsNone(desktop.windows_env("NOPE"))
        self.assertEqual(desktop.powershell("'hi'"), "ok")

    def test_windows_tools_on_path_win(self):
        on_path = self.pystub("cmd.exe", CMD.format(values={"LOCALAPPDATA": "C:\\elsewhere"}))
        self.assertEqual(desktop.windows_tool("cmd.exe"), str(on_path))
        self.assertEqual(desktop.windows_env("LOCALAPPDATA"), "C:\\elsewhere")

    def test_windows_unreachable(self):
        (self.c / "Windows/System32/cmd.exe").unlink()
        self.assertIsNone(desktop.windows_tool("cmd.exe"))
        self.assertIsNone(desktop.windows_env("LOCALAPPDATA"))
        with self.assertRaises(UmbrellaError) as cm:
            desktop.win_umbrella_root()
        self.assertIn("Couldn't reach Windows from WSL", str(cm.exception))
        self.assertIn("System32", cm.exception.hint)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.plan_launch(self.work, "wsl")
        self.assertIn("%LOCALAPPDATA%", str(cm.exception))
        (self.bin / "wslpath").unlink()
        self.assertIsNone(desktop.to_wsl("C:\\x"))

    def test_capture_failures_and_cwd(self):
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertIsNone(desktop.to_wsl("C:\\x"))
        with mock.patch("subprocess.run", side_effect=OSError):
            self.assertIsNone(desktop.to_wsl("C:\\x"))
        with mock.patch("os.path.isdir", return_value=True), mock.patch("subprocess.run") as run, \
                mock.patch.object(desktop.shutil, "which", return_value="/usr/bin/wslpath"):
            run.return_value = subprocess.CompletedProcess([], 0, stdout="C:\\x\n")
            self.assertEqual(desktop.to_windows("/c/x"), "C:\\x")
        self.assertEqual(run.call_args[1]["cwd"], "/mnt/c")
        self.assertEqual(desktop.to_windows(self.c / "x"), "C:\\x")
        self.assertIsNone(desktop.to_windows("/elsewhere"))

    def test_profile_folders_avoid_appdata(self):
        self.assertEqual(str(desktop.win_umbrella_root()), self.USER + "\\.umbrella")
        self.assertEqual(str(desktop.win_data_dir(self.work)), self.USER + "\\.umbrella\\desktop\\work")
        self.assertEqual(str(desktop.win_config_dir(self.work)), self.USER + "\\.umbrella\\claude\\work")

    # --- finding the app -------------------------------------------------------------------------

    def test_finds_store_app_alias(self):
        self.install_store_app()
        self.assertEqual(desktop.windows_app(), self.STORE_ALIAS)

    def test_finds_top_level_store_alias_even_if_unstatable(self):
        aliases = self.local / "Microsoft/WindowsApps"
        aliases.mkdir(parents=True)
        os.symlink(str(self.tmp / "nowhere"), str(aliases / "claude-desktop.exe"))  # like an app alias
        self.assertEqual(desktop.windows_app(), self.LOCAL + "\\Microsoft\\WindowsApps\\claude-desktop.exe")

    def test_asks_windows_for_store_package(self):
        self.set_powershell(family="Claude_pzs8sxrjxfjjc")
        self.assertEqual(desktop.windows_app(), self.STORE_ALIAS)
        self.assertIn("Get-AppxPackage", self.ps_log.read_text())

    def test_classic_installer_preferred_newest(self):
        self.install_store_app()
        self.install("AnthropicClaude/claude.exe", "AnthropicClaude/app-0.9.1/claude.exe",
                     "AnthropicClaude/app-0.10.0/claude.exe", "AnthropicClaude/app-x/claude.exe")
        self.assertEqual(desktop.windows_app(), self.LOCAL + "\\AnthropicClaude\\app-0.10.0\\claude.exe")

    def test_other_layouts(self):
        self.install("Programs/Claude/Claude.exe")
        self.assertEqual(desktop.windows_app(), self.LOCAL + "\\Programs\\Claude\\Claude.exe")

    def test_not_found(self):
        self.assertIsNone(desktop.windows_app())
        with self.assertRaises(UmbrellaError) as cm:
            desktop.plan_launch(self.work, "wsl")
        self.assertIn("claude-desktop.exe", cm.exception.hint)
        self.set_cmd({"LOCALAPPDATA": "D:\\unmapped"})
        self.assertIsNone(desktop.windows_app())
        self.set_cmd({})
        self.assertIsNone(desktop.windows_app())

    def test_override(self):
        self.assertEqual(desktop.windows_app({"UMBRELLA_DESKTOP_APP": "D:\\Apps\\claude.exe"}), "D:\\Apps\\claude.exe")
        exe = self.write("Tools/claude.exe", "x", base=self.c)
        self.assertEqual(desktop.windows_app({"UMBRELLA_DESKTOP_APP": str(exe)}), "C:\\Tools\\claude.exe")
        self.assertIsNone(desktop.windows_app({"UMBRELLA_DESKTOP_APP": str(self.c / "none.exe")}))

    # --- launching -------------------------------------------------------------------------------

    def test_command_uses_start_process(self):
        cmd = desktop.windows_command(self.work, self.STORE_ALIAS)
        self.assertEqual(cmd[0], str(self.c / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
        self.assertEqual(cmd[-1], "Start-Process -FilePath '{}' -ArgumentList '\"--user-data-dir={}\"'".format(
            self.STORE_ALIAS, self.USER + "\\.umbrella\\desktop\\work"))
        self.assertEqual(desktop.windows_command(self.personal, "C:\\it's.exe")[-1],
                         "Start-Process -FilePath 'C:\\it''s.exe'")
        (self.c / "Windows/System32/WindowsPowerShell/v1.0/powershell.exe").unlink()
        with self.assertRaises(UmbrellaError) as cm:
            desktop.windows_command(self.work, self.STORE_ALIAS)
        self.assertIn("powershell.exe", str(cm.exception))

    def test_environment(self):
        env = desktop.windows_environment(self.work, {"WSLENV": "FOO/p:CLAUDE_CONFIG_DIR/p", "CLAUDE_CONFIG_DIR": "/x"})
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], self.USER + "\\.umbrella\\claude\\work")
        self.assertEqual(env["WSLENV"], "FOO/p:CLAUDE_CONFIG_DIR:UMBRELLA_PROFILE")
        env = desktop.windows_environment(self.personal, {"CLAUDE_CONFIG_DIR": "/x"})
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)
        self.assertEqual(env["WSLENV"], "UMBRELLA_PROFILE")
        self.assertIn("HOME", desktop.windows_environment(self.personal))

    def test_plan_launch(self):
        self.install_store_app()
        launch = desktop.plan_launch(self.work, "wsl")
        self.assertIn(self.STORE_ALIAS, launch.command[-1])
        self.assertEqual(str(launch.data_dir), self.USER + "\\.umbrella\\desktop\\work")
        self.assertTrue(launch.first_run)
        (self.c / "Users/TaeOn/.umbrella/desktop/work").mkdir(parents=True)
        self.assertFalse(desktop.plan_launch(self.work, "wsl").first_run)
        launch = desktop.plan_launch(self.personal, "wsl")
        self.assertNotIn("ArgumentList", launch.command[-1])
        self.assertIsNone(launch.data_dir)

    def test_start_uses_windows_cwd(self):
        self.install_store_app()
        launch = desktop.plan_launch(self.work, "wsl")
        with mock.patch("os.path.isdir", return_value=True), mock.patch("subprocess.Popen") as popen:
            desktop.start(launch)
        self.assertEqual(popen.call_args[1]["cwd"], "/mnt/c")
        self.assertEqual(popen.call_args[1]["env"]["WSLENV"], "CLAUDE_CONFIG_DIR:UMBRELLA_PROFILE")

    # --- Start Menu shortcut ---------------------------------------------------------------------

    def test_shortcut(self):
        self.install_store_app()
        link = desktop.create_shortcut(self.work, "wsl")
        self.assertEqual(str(link), self.USER + "\\AppData\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\"
                                                "Claude (work).lnk")
        vbs_file = self.c / "Users/TaeOn/.umbrella/launchers/claude-work.vbs"
        vbs = vbs_file.read_text()
        self.assertIn('env("CLAUDE_CONFIG_DIR") = "{}"'.format(self.USER + "\\.umbrella\\claude\\work"), vbs)
        self.assertIn('shell.Run """{}"" ""--user-data-dir={}""", 1, False'.format(
            self.STORE_ALIAS, self.USER + "\\.umbrella\\desktop\\work"), vbs)
        self.assertIn(b"\r\n", vbs_file.read_bytes())
        ps = self.ps_log.read_text()
        self.assertIn("CreateShortcut('{}')".format(link), ps)
        self.assertIn("$s.TargetPath = 'wscript.exe'", ps)
        self.assertIn("Umbrella profile ''work''", ps)

        desktop.create_shortcut(self.personal, "wsl")
        vbs = (self.c / "Users/TaeOn/.umbrella/launchers/claude-personal.vbs").read_text()
        self.assertNotIn("CLAUDE_CONFIG_DIR", vbs)
        self.assertIn('shell.Run """{}""", 1, False'.format(self.STORE_ALIAS), vbs)

    def test_shortcut_failures(self):
        self.install_store_app()
        self.set_powershell(fail=True)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "wsl")
        self.assertIn("umbrella desktop work", cm.exception.hint)
        self.set_powershell()
        with self.assertRaises(UmbrellaError):
            desktop.create_shortcut(self.work, "wsl", {"UMBRELLA_DESKTOP_APP": str(self.c / "none.exe")})
        self.values["USERPROFILE"] = "D:\\unmapped"
        self.set_cmd(self.values)
        with self.assertRaises(UmbrellaError) as cm:
            desktop.create_shortcut(self.work, "wsl")
        self.assertIn("from WSL", str(cm.exception))

    def test_remove_artifacts(self):
        self.install_store_app()
        desktop.create_shortcut(self.work, "wsl")
        self.write("Microsoft/Windows/Start Menu/Programs/Claude (work).lnk", "lnk",
                   base=self.c / "Users/TaeOn/AppData/Roaming")
        for sub in ("desktop/work", "claude/work"):
            (self.c / "Users/TaeOn/.umbrella" / sub).mkdir(parents=True)
        removed = desktop.remove_artifacts(self.work, "wsl")
        self.assertEqual(len(removed), 4)
        self.assertFalse((self.c / "Users/TaeOn/.umbrella/desktop/work").exists())
        self.assertEqual(desktop.remove_artifacts(self.work, "wsl"), [])
        self.set_cmd({"USERPROFILE": self.USER})  # no %APPDATA%: skip the shortcut
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
        self.assertIn("Couldn't find Claude.app", self.run_cli("doctor")[1])
        with mock.patch.object(paths, "detect_platform", return_value="linux"):
            self.assertNotIn("Desktop", self.run_cli("doctor")[1])
