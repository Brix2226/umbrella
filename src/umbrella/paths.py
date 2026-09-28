"""Where things live on disk, and which platform we're on."""

import os
import platform
from pathlib import Path


def home():
    return Path.home()


def umbrella_dir():
    """Umbrella's own data: the profile registry, managed profile dirs and backups."""
    override = os.environ.get("UMBRELLA_DIR")
    return Path(override) if override else home() / ".umbrella"


def registry_file():
    return umbrella_dir() / "profiles.json"


def profiles_dir():
    return umbrella_dir() / "profiles"


def backups_dir():
    return umbrella_dir() / "backups"


def default_claude_dir():
    """The directory Claude Code uses when CLAUDE_CONFIG_DIR is not set."""
    return home() / ".claude"


def default_state_file():
    """Claude Code's main state file when CLAUDE_CONFIG_DIR is not set."""
    return home() / ".claude.json"


def _read_proc_version():
    try:
        return Path("/proc/version").read_text()
    except OSError:
        return ""


def detect_platform(system=None, proc_version=None):
    """Return 'macos', 'wsl', 'linux', or the lowercased OS name for anything else."""
    system = system or platform.system()
    if system == "Darwin":
        return "macos"
    if system == "Linux":
        text = _read_proc_version() if proc_version is None else proc_version
        return "wsl" if "microsoft" in text.lower() else "linux"
    return system.lower()


PLATFORM_NAMES = {"macos": "macOS", "wsl": "Windows (WSL2)", "linux": "Linux"}


def platform_name(key):
    return PLATFORM_NAMES.get(key, key)


def pretty(path):
    """Show a path with the home directory abbreviated to ~."""
    path = str(path)
    home_str = str(home())
    if path == home_str:
        return "~"
    if path.startswith(home_str + os.sep):
        return "~" + path[len(home_str):]
    return path
