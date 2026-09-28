"""Launching Claude Code under a profile, and looking at the macOS Keychain."""

import hashlib
import os
import shutil
import subprocess
import unicodedata

from umbrella import paths
from umbrella.errors import UmbrellaError

KEYCHAIN_SERVICE = "Claude Code-credentials"


def find_claude(env=None):
    """Path to the `claude` executable, or None if it isn't installed."""
    env = os.environ if env is None else env
    found = shutil.which("claude", path=env.get("PATH", ""))
    if found:
        return found
    candidates = [paths.home() / ".claude" / "local" / "claude", paths.home() / ".local" / "bin" / "claude"]
    # The Claude desktop app on macOS bundles its own copy of Claude Code; prefer the newest.
    bundled = paths.home() / "Library" / "Application Support" / "Claude" / "claude-code"
    candidates += sorted(bundled.glob("*/claude.app/Contents/MacOS/claude"), key=_version_key, reverse=True)
    for candidate in candidates:
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def _version_key(path):
    version = path.parents[3].name
    return tuple(int(part) if part.isdigit() else 0 for part in version.split("."))


def profile_environment(profile, base=None):
    env = dict(os.environ if base is None else base)
    for name, value in profile.env().items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


def require_claude(env=None, platform=None):
    """Path to `claude`, or a friendly error explaining how to install it here."""
    claude = find_claude(env)
    if claude is not None:
        return claude
    if (platform or paths.detect_platform()) == "wsl":
        raise UmbrellaError(
            "Claude Code isn't installed inside WSL.",
            hint="Umbrella's terminal commands need Claude Code in WSL itself (a Windows install can't be "
                 "used from here). Install it with: curl -fsSL https://claude.ai/install.sh | bash")
    raise UmbrellaError(
        "Couldn't find the 'claude' command.",
        hint="Install Claude Code with: curl -fsSL https://claude.ai/install.sh | bash "
             "(or see https://docs.claude.com/claude-code), then make sure 'claude' is on your PATH.")


def run_claude(profile, args, env=None):
    """Run Claude Code as ``profile`` and return its exit code."""
    base = os.environ if env is None else env
    claude = require_claude(base)
    return subprocess.call([claude] + list(args), env=profile_environment(profile, base))


def keychain_service(profile):
    """The Keychain item Claude Code uses for this profile's login on macOS.

    When CLAUDE_CONFIG_DIR is set, Claude Code appends the first 8 hex digits of the SHA-256 of
    that directory (NFC-normalized), so each profile gets its own Keychain item.
    """
    if profile.uses_default_dir:
        return KEYCHAIN_SERVICE
    digest = hashlib.sha256(unicodedata.normalize("NFC", profile.path).encode("utf-8")).hexdigest()[:8]
    return "{}-{}".format(KEYCHAIN_SERVICE, digest)


def _security(*args):
    if shutil.which("security") is None:
        return None
    return subprocess.run(["security"] + list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def keychain_has_login(profile):
    """True/False on macOS; None when there's no Keychain to ask."""
    code = _security("find-generic-password", "-s", keychain_service(profile))
    return None if code is None else code == 0


def keychain_delete_login(profile):
    return _security("delete-generic-password", "-s", keychain_service(profile)) == 0
