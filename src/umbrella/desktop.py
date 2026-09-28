"""Running the Claude Desktop app as a profile.

The Desktop app is an Electron app. Two things make a fully separate copy of it:

* ``--user-data-dir=<dir>`` gives it its own claude.ai login, settings and caches, so it can
  run alongside your normal Claude window.
* ``CLAUDE_CONFIG_DIR`` makes its Code tab keep sessions, settings and history in the profile.

On macOS we launch the app binary directly. On Windows we launch the Windows app from WSL2
through WSL's Windows interop, with profile folders on the Windows side under
%LOCALAPPDATA%\\Umbrella, because the Windows app's Code tab runs as a Windows program.

A profile that uses Claude's default directory (like "personal") is simply your normal app.
"""

import os
import plistlib
import shutil
import subprocess
from pathlib import Path, PureWindowsPath

from umbrella import paths
from umbrella.errors import UmbrellaError

MAC_APP_NAMES = ("Claude.app",)
LAUNCHER_ID_PREFIX = "dev.umbrella.claude."

FIRST_RUN_TIP = ("First time? Sign in with the account you want for '{name}'. If signing in sends you to "
                 "your browser and back, quit your other Claude windows first so the sign-in lands in this one.")


def app_override(env=None):
    env = os.environ if env is None else env
    return env.get("UMBRELLA_DESKTOP_APP") or None


# ----- macOS ------------------------------------------------------------------------------------

def mac_app(env=None):
    """Path to Claude.app, or None."""
    override = app_override(env)
    candidates = [Path(override)] if override else [
        root / name for root in _mac_app_dirs() for name in MAC_APP_NAMES]
    for app in candidates:
        if (app / "Contents" / "MacOS" / "Claude").is_file():
            return app
    return None


def _mac_app_dirs():
    return [Path("/Applications"), paths.home() / "Applications"]


def mac_data_dir(profile):
    return paths.umbrella_dir() / "desktop" / profile.name


def mac_launcher_path(profile):
    return paths.home() / "Applications" / "Claude ({}).app".format(profile.name)


def mac_command(profile, app):
    binary = str(app / "Contents" / "MacOS" / "Claude")
    if profile.uses_default_dir:
        return ["/usr/bin/open", "-a", str(app)]
    return [binary, "--user-data-dir={}".format(mac_data_dir(profile))]


def mac_environment(profile, base=None):
    env = dict(os.environ if base is None else base)
    env.pop("CLAUDE_CONFIG_DIR", None)
    if not profile.uses_default_dir:
        env["CLAUDE_CONFIG_DIR"] = str(profile.config_dir)
    env["UMBRELLA_PROFILE"] = profile.name
    return env


def _sh_quote(value):
    return "'" + value.replace("'", "'\"'\"'") + "'"


def mac_launch_shell(profile, app):
    """The shell command a launcher runs: set the profile's environment, start Claude, return at once."""
    exports = "export UMBRELLA_PROFILE={}; ".format(_sh_quote(profile.name))
    if not profile.uses_default_dir:
        exports = "export CLAUDE_CONFIG_DIR={}; ".format(_sh_quote(str(profile.config_dir))) + exports
    command = " ".join(_sh_quote(part) for part in mac_command(profile, app))
    return "{}/usr/bin/nohup {} >/dev/null 2>&1 &".format(exports, command)


def _applescript_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tool(args, what):
    if shutil.which(args[0]) is None:
        raise UmbrellaError("Couldn't find macOS's '{}' tool, which is needed to {}.".format(args[0], what))
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, universal_newlines=True)
    if result.returncode != 0:
        raise UmbrellaError("macOS couldn't {}: {}".format(what, result.stderr.strip() or "unknown error"))


def write_mac_launcher(profile, app):
    """Create ~/Applications/Claude (<name>).app, a tiny AppleScript app that opens this profile.

    macOS won't open unsigned script bundles, so we build a standard AppleScript applet with
    osacompile, give it Claude's icon, and re-sign it locally (ad hoc) after changing it.
    """
    target = mac_launcher_path(profile)
    if target.exists():
        shutil.rmtree(str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    script = "do shell script " + _applescript_string(mac_launch_shell(profile, app))
    _tool(["osacompile", "-o", str(target), "-e", script], "create the launcher")
    icon = app / "Contents" / "Resources" / "electron.icns"
    applet_icon = target / "Contents" / "Resources" / "applet.icns"
    if icon.is_file():
        shutil.copyfile(str(icon), str(applet_icon))
    info_file = target / "Contents" / "Info.plist"
    with info_file.open("rb") as fh:
        info = plistlib.load(fh)
    info.update({
        "CFBundleName": "Claude ({})".format(profile.name),
        "CFBundleDisplayName": "Claude ({})".format(profile.name),
        "CFBundleIdentifier": LAUNCHER_ID_PREFIX + profile.name,
        "LSUIElement": True,  # the launcher itself never shows in the Dock; Claude does
    })
    with info_file.open("wb") as fh:
        plistlib.dump(info, fh)
    _tool(["codesign", "--force", "--sign", "-", str(target)], "sign the launcher")
    return target


# ----- Windows via WSL --------------------------------------------------------------------------

# Where Windows keeps these, for WSL setups that don't put Windows folders on PATH.
WINDOWS_TOOLS = {
    "cmd.exe": r"C:\Windows\System32\cmd.exe",
    "powershell.exe": r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
}
STORE_ALIAS = "claude-desktop.exe"


def _capture(args):
    """Run a program and return its stripped stdout, or None if it failed or printed nothing."""
    cwd = "/mnt/c" if os.path.isdir("/mnt/c") else None
    try:
        result = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                universal_newlines=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else None


def _wslpath(flag, value):
    if shutil.which("wslpath") is None:
        return None
    return _capture(["wslpath", flag, str(value)])


def to_wsl(win_path):
    return _wslpath("-u", win_path)


def to_windows(wsl_path):
    return _wslpath("-w", wsl_path)


def windows_tool(name):
    """WSL path to a Windows program, found on PATH or at its standard location."""
    found = shutil.which(name)
    if found:
        return found
    local = to_wsl(WINDOWS_TOOLS[name])
    return local if local and os.path.isfile(local) else None


def _run_windows(name, args):
    tool = windows_tool(name)
    return None if tool is None else _capture([tool] + list(args))


def windows_env(name):
    value = _run_windows("cmd.exe", ["/d", "/c", "echo %{}%".format(name)])
    return None if value is None or value == "%{}%".format(name) else value


def powershell(script):
    return _run_windows("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script])


def _interop_error(what):
    return UmbrellaError(
        "Couldn't reach Windows from WSL ({}).".format(what),
        hint="Umbrella looks for cmd.exe and powershell.exe on your PATH and under C:\\Windows\\System32. "
             "Check that running '/mnt/c/Windows/System32/cmd.exe /c ver' works in this shell.")


def _require_windows_env(name):
    value = windows_env(name)
    if value is None:
        raise _interop_error("asking for %{}%".format(name))
    return value


def win_umbrella_root():
    """Umbrella's folder on the Windows side, as a Windows path.

    It's deliberately not under AppData: Windows redirects what Microsoft Store apps write inside
    AppData to a private folder, which would hide the profile's data from Umbrella.
    """
    return PureWindowsPath(_require_windows_env("USERPROFILE")) / ".umbrella"


def win_data_dir(profile):
    return win_umbrella_root() / "desktop" / profile.name


def win_config_dir(profile):
    """CLAUDE_CONFIG_DIR for the Windows app's Code tab (a Windows path)."""
    return win_umbrella_root() / "claude" / profile.name


def _version_key(path):
    return tuple(int(p) if p.isdigit() else 0 for p in path.name[len("app-"):].split("."))


def windows_app(env=None):
    """The Windows Claude app as a Windows path (str), or None.

    Looks for, in order: UMBRELLA_DESKTOP_APP, the classic installer (%LOCALAPPDATA%\\AnthropicClaude),
    and the Microsoft Store app's 'claude-desktop.exe' alias.
    """
    override = app_override(env)
    if override:
        if PureWindowsPath(override).drive:
            return override
        return to_windows(override) if Path(override).is_file() else None
    local = windows_env("LOCALAPPDATA")
    local_wsl = to_wsl(local) if local else None
    if not local_wsl:
        return None
    base, win_base = Path(local_wsl), PureWindowsPath(local)
    squirrel = base / "AnthropicClaude"
    found = [v / "claude.exe" for v in sorted(squirrel.glob("app-*"), key=_version_key, reverse=True)]
    found += [squirrel / "claude.exe", base / "Programs" / "Claude" / "Claude.exe"]
    aliases = base / "Microsoft" / "WindowsApps"
    # Store aliases are special files WSL can list but not always stat, so check with lexists.
    found += sorted(aliases.glob("Claude_*/" + STORE_ALIAS)) + [aliases / STORE_ALIAS]
    for candidate in found:
        if os.path.lexists(str(candidate)):
            return str(win_base.joinpath(*candidate.relative_to(base).parts))
    family = powershell("(Get-AppxPackage -Name Claude | Select-Object -First 1).PackageFamilyName")
    if family:
        return str(win_base / "Microsoft" / "WindowsApps" / family / STORE_ALIAS)
    return None


def windows_args(profile):
    return [] if profile.uses_default_dir else ["--user-data-dir={}".format(win_data_dir(profile))]


def windows_command(profile, app):
    """Start the app via PowerShell's Start-Process, which also handles Store app aliases."""
    shell = windows_tool("powershell.exe")
    if shell is None:
        raise _interop_error("looking for powershell.exe")
    script = "Start-Process -FilePath {}".format(_ps_quote(app))
    args = windows_args(profile)
    if args:
        script += " -ArgumentList {}".format(_ps_quote(" ".join('"{}"'.format(a) for a in args)))
    return [shell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script]


def windows_environment(profile, base=None):
    """Environment for the Windows app. WSLENV lists the variables WSL passes through to Windows."""
    env = dict(os.environ if base is None else base)
    passthrough = [v for v in env.get("WSLENV", "").split(":") if v and v.split("/")[0] not in
                   ("CLAUDE_CONFIG_DIR", "UMBRELLA_PROFILE")]
    env.pop("CLAUDE_CONFIG_DIR", None)
    if not profile.uses_default_dir:
        env["CLAUDE_CONFIG_DIR"] = str(win_config_dir(profile))
        passthrough.append("CLAUDE_CONFIG_DIR")
    env["UMBRELLA_PROFILE"] = profile.name
    passthrough.append("UMBRELLA_PROFILE")
    env["WSLENV"] = ":".join(passthrough)
    return env


def _vbs_quote(value):
    return '"' + value.replace('"', '""') + '"'


def _ps_quote(value):
    return "'" + value.replace("'", "''") + "'"


def _start_menu_link(profile):
    appdata = _require_windows_env("APPDATA")
    return PureWindowsPath(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / \
        "Claude ({}).lnk".format(profile.name)


def _launcher_script(profile):
    return win_umbrella_root() / "launchers" / "claude-{}.vbs".format(profile.name)


def write_windows_launcher(profile, app):
    """A Start Menu shortcut "Claude (<name>)" that opens this profile without a console window.

    Windows shortcuts can't set environment variables, so the shortcut runs a tiny VBScript
    that sets CLAUDE_CONFIG_DIR and starts Claude.
    """
    link = _start_menu_link(profile)
    script_win = _launcher_script(profile)
    script_local = to_wsl(script_win)
    if script_local is None:
        raise UmbrellaError("Couldn't reach Umbrella's Windows folder ({}) from WSL.".format(script_win.parent))
    command_line = " ".join('"{}"'.format(part) for part in [app] + windows_args(profile))
    lines = ['Set shell = CreateObject("WScript.Shell")',
             'Set env = shell.Environment("PROCESS")',
             'env("UMBRELLA_PROFILE") = {}'.format(_vbs_quote(profile.name))]
    if not profile.uses_default_dir:
        lines.append('env("CLAUDE_CONFIG_DIR") = {}'.format(_vbs_quote(str(win_config_dir(profile)))))
    lines.append("shell.Run {}, 1, False".format(_vbs_quote(command_line)))
    Path(script_local).parent.mkdir(parents=True, exist_ok=True)
    Path(script_local).write_text("' Made by Umbrella: opens Claude as profile '{}'.\r\n".format(profile.name)
                                  + "\r\n".join(lines) + "\r\n")

    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut({link}); "
          "$s.TargetPath = 'wscript.exe'; $s.Arguments = {args}; $s.IconLocation = {icon}; "
          "$s.Description = {desc}; $s.Save(); 'ok'").format(
        link=_ps_quote(str(link)), args=_ps_quote('"{}"'.format(script_win)),
        icon=_ps_quote(app + ",0"), desc=_ps_quote("Claude as Umbrella profile '{}'".format(profile.name)))
    if powershell(ps) is None:
        raise UmbrellaError("Windows didn't let Umbrella create the Start Menu shortcut.",
                            hint="You can still open the profile with 'umbrella desktop {}'.".format(profile.name))
    return link


# ----- both -------------------------------------------------------------------------------------

class Launch:
    """Everything needed to start the Desktop app as a profile on this platform."""

    def __init__(self, platform, command, env, data_dir, first_run):
        self.platform = platform
        self.command = command
        self.env = env
        self.data_dir = data_dir
        self.first_run = first_run


def _not_found(platform):
    if platform == "macos":
        return UmbrellaError("Couldn't find Claude.app in /Applications or ~/Applications.",
                             hint="Install it from https://claude.ai/download, or set UMBRELLA_DESKTOP_APP "
                                  "to the path of Claude.app.")
    return UmbrellaError(
        "Couldn't find the Claude Desktop app on Windows.",
        hint="Checked %LOCALAPPDATA%\\AnthropicClaude and the Microsoft Store app (claude-desktop.exe). "
             "Install it from https://claude.ai/download, or set UMBRELLA_DESKTOP_APP to its Windows path.")


def _require_windows_app(env):
    _require_windows_env("LOCALAPPDATA")  # a clear error if WSL can't reach Windows at all
    app = windows_app(env)
    if app is None:
        raise _not_found("wsl")
    return app


def plan_launch(profile, platform=None, env=None):
    platform = platform or paths.detect_platform()
    if platform == "macos":
        app = mac_app(env)
        if app is None:
            raise _not_found(platform)
        data = None if profile.uses_default_dir else mac_data_dir(profile)
        first = data is not None and not data.exists()
        if data is not None:
            data.mkdir(parents=True, exist_ok=True)
            os.chmod(str(data), 0o700)
        return Launch(platform, mac_command(profile, app), mac_environment(profile, env), data, first)
    if platform == "wsl":
        app = _require_windows_app(env)
        data = None
        first = False
        if not profile.uses_default_dir:
            data = win_data_dir(profile)
            data_wsl = to_wsl(data)
            first = bool(data_wsl) and not Path(data_wsl).exists()
        return Launch(platform, windows_command(profile, app), windows_environment(profile, env), data, first)
    raise UmbrellaError("The Claude Desktop app runs on macOS and Windows, not on {}.".format(paths.platform_name(platform)),
                        hint="On Windows, run Umbrella inside WSL2 and it will open the Windows app for you.")


def start(launch):
    """Start the app detached from this terminal."""
    cwd = "/mnt/c" if launch.platform == "wsl" and os.path.isdir("/mnt/c") else None
    subprocess.Popen(launch.command, env=launch.env, cwd=cwd, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def create_shortcut(profile, platform=None, env=None):
    platform = platform or paths.detect_platform()
    if platform == "macos":
        app = mac_app(env)
        if app is None:
            raise _not_found(platform)
        return write_mac_launcher(profile, app)
    if platform == "wsl":
        return write_windows_launcher(profile, _require_windows_app(env))
    raise UmbrellaError("Desktop shortcuts are only available on macOS and Windows (WSL2).")


def remove_artifacts(profile, platform=None):
    """Delete a profile's Desktop data and launcher. Returns what was removed (for display)."""
    platform = platform or paths.detect_platform()
    removed = []
    if profile.uses_default_dir:
        return removed
    if platform == "macos":
        for target in (mac_data_dir(profile), mac_launcher_path(profile)):
            if target.exists():
                shutil.rmtree(str(target))
                removed.append(paths.pretty(target))
    elif platform == "wsl" and windows_env("USERPROFILE"):
        targets = [win_data_dir(profile), win_config_dir(profile), _launcher_script(profile)]
        if windows_env("APPDATA"):
            targets.append(_start_menu_link(profile))
        for target in targets:
            local = to_wsl(target)
            if local and os.path.lexists(local):
                shutil.rmtree(local) if os.path.isdir(local) else os.remove(local)
                removed.append(str(target))
    return removed
