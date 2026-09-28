"""The profile registry: which accounts exist and where each one keeps its Claude data.

A profile is a Claude Code config directory. The special profile whose ``path`` is None
is Claude's own default (~/.claude + ~/.claude.json); every other profile is a
directory that Claude Code uses when CLAUDE_CONFIG_DIR points at it.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from umbrella import paths
from umbrella.errors import UmbrellaError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# What `umbrella add --share` can link from Claude's default directory.
SHAREABLE = {
    "settings": "settings.json",
    "claude-md": "CLAUDE.md",
    "skills": "skills",
    "agents": "agents",
    "commands": "commands",
    "keybindings": "keybindings.json",
    "output-styles": "output-styles",
}


class Profile:
    def __init__(self, name, path=None, created=None):
        self.name = name
        self.path = path
        self.created = created

    @property
    def uses_default_dir(self):
        return self.path is None

    @property
    def config_dir(self):
        return paths.default_claude_dir() if self.path is None else Path(self.path)

    @property
    def state_file(self):
        return paths.default_state_file() if self.path is None else Path(self.path) / ".claude.json"

    def env(self):
        """Environment changes that make Claude Code use this profile. None means 'unset'."""
        return {
            "CLAUDE_CONFIG_DIR": None if self.path is None else self.path,
            "UMBRELLA_PROFILE": self.name,
        }

    def account(self):
        """The signed-in account from Claude's state file, or None if not signed in."""
        try:
            data = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return None
        account = data.get("oauthAccount") if isinstance(data, dict) else None
        if not isinstance(account, dict) or not account.get("emailAddress"):
            return None
        return account

    def to_dict(self):
        return {"path": self.path, "created": self.created}


def validate_name(name):
    if not NAME_RE.match(name or ""):
        raise UmbrellaError(
            "'{}' isn't a valid profile name.".format(name),
            hint="Use 1-32 lowercase letters, digits, '-' or '_', starting with a letter or digit (e.g. 'work').",
        )


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Registry:
    def __init__(self, file=None):
        self.file = Path(file) if file else paths.registry_file()
        self.profiles = {}
        self.default = None
        self.exists = self.file.exists()
        if self.exists:
            self._load()

    def _load(self):
        try:
            data = json.loads(self.file.read_text())
            profiles = data["profiles"]
            self.profiles = {
                name: Profile(name, info.get("path"), info.get("created")) for name, info in profiles.items()
            }
            self.default = data.get("default")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            raise UmbrellaError(
                "Umbrella's profile list at {} is damaged and can't be read.".format(paths.pretty(self.file)),
                hint="Fix the JSON by hand, or move the file aside and run 'umbrella init' again.",
            )

    def save(self):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(str(self.file.parent), 0o700)
        data = {
            "version": 1,
            "default": self.default,
            "profiles": {name: p.to_dict() for name, p in sorted(self.profiles.items())},
        }
        tmp = self.file.with_name(self.file.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(str(tmp), str(self.file))
        self.exists = True

    def require_initialized(self):
        if not self.exists:
            raise UmbrellaError("Umbrella isn't set up yet.", hint="Run 'umbrella init' first.")

    def names(self):
        return sorted(self.profiles)

    def get(self, name):
        try:
            return self.profiles[name]
        except KeyError:
            known = ", ".join(self.names()) or "none yet"
            raise UmbrellaError(
                "There's no profile called '{}'.".format(name),
                hint="Known profiles: {}. Create one with 'umbrella add {}'.".format(known, name),
            )

    def default_dir_profile(self):
        for profile in self.profiles.values():
            if profile.uses_default_dir:
                return profile
        return None

    def add(self, name, path=None):
        validate_name(name)
        if name in self.profiles:
            raise UmbrellaError("A profile called '{}' already exists.".format(name))
        if path is None and self.default_dir_profile() is not None:
            raise UmbrellaError(
                "Profile '{}' already uses Claude's default directory.".format(self.default_dir_profile().name)
            )
        profile = Profile(name, None if path is None else str(path), _now())
        self.profiles[name] = profile
        if self.default is None:
            self.default = name
        return profile

    def remove(self, name):
        profile = self.get(name)
        del self.profiles[name]
        if self.default == name:
            self.default = None
        return profile

    def set_default(self, name):
        self.default = self.get(name).name

    def find_by_dir(self, config_dir):
        """The profile whose CLAUDE_CONFIG_DIR is ``config_dir`` (None = unset)."""
        if not config_dir:
            return self.default_dir_profile()
        target = os.path.realpath(os.path.expanduser(config_dir))
        for profile in self.profiles.values():
            if os.path.realpath(str(profile.config_dir)) == target:
                return profile
        return None

    def current(self, env=None):
        """The profile this environment is using, or None if it isn't one we manage."""
        env = os.environ if env is None else env
        return self.find_by_dir(env.get("CLAUDE_CONFIG_DIR"))


def create_profile_dir(name):
    """Make a private directory for a new managed profile."""
    target = paths.profiles_dir() / name
    if target.exists() and any(target.iterdir()):
        raise UmbrellaError(
            "{} already exists and isn't empty.".format(paths.pretty(target)),
            hint="Pick another name, or pass --path to use an existing Claude directory.",
        )
    target.mkdir(parents=True, exist_ok=True)
    os.chmod(str(paths.umbrella_dir()), 0o700)
    os.chmod(str(target), 0o700)
    return target


def parse_share(value):
    """Turn '--share settings,skills' (or 'all') into a list of SHAREABLE keys."""
    if not value:
        return []
    keys = [part.strip() for part in value.split(",") if part.strip()]
    if keys == ["all"]:
        return sorted(SHAREABLE)
    unknown = [k for k in keys if k not in SHAREABLE]
    if unknown:
        raise UmbrellaError(
            "Can't share: {}.".format(", ".join(unknown)),
            hint="Choose from: {}, or 'all'.".format(", ".join(sorted(SHAREABLE))),
        )
    return keys


def share_items(profile, keys):
    """Symlink customizations from Claude's default directory into ``profile``.

    Returns (linked, missing): items linked, and items skipped because the source doesn't exist
    or the profile already has its own copy.
    """
    linked, missing = [], []
    source_root = paths.default_claude_dir()
    for key in keys:
        filename = SHAREABLE[key]
        source = source_root / filename
        dest = profile.config_dir / filename
        if not source.exists() or dest.exists() or dest.is_symlink():
            missing.append(filename)
            continue
        os.symlink(str(source), str(dest))
        linked.append(filename)
    return linked, missing
