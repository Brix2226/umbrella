"""Back up a profile to a .tar.gz, and restore one safely.

Archive layout:
    umbrella-manifest.json   what this is, where it came from
    config/...               the profile's Claude config directory
    state/.claude.json       Claude's main state file for that profile
"""

import io
import json
import os
import stat
import tarfile
import time
from pathlib import Path, PurePosixPath

from umbrella import __version__, paths
from umbrella.errors import UmbrellaError

MANIFEST = "umbrella-manifest.json"
SECRET_FILES = {".credentials.json"}


class BackupResult:
    def __init__(self, path, files, size, skipped_secrets, unreadable):
        self.path = path
        self.files = files
        self.size = size
        self.skipped_secrets = skipped_secrets
        self.unreadable = unreadable


def default_backup_path(profile, now=None):
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    candidate = paths.backups_dir() / "umbrella-{}-{}.tar.gz".format(profile.name, stamp)
    counter = 2
    while candidate.exists():
        candidate = paths.backups_dir() / "umbrella-{}-{}-{}.tar.gz".format(profile.name, stamp, counter)
        counter += 1
    return candidate


def _open_private(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise UmbrellaError("{} already exists.".format(paths.pretty(path)), hint="Choose another --output path.")
    return os.fdopen(fd, "wb")


def create_backup(profile, output, include_secrets=False, platform=None):
    output = Path(output)
    root = profile.config_dir
    files = size = 0
    skipped, unreadable = [], []
    with _open_private(output) as raw, tarfile.open(fileobj=raw, mode="w:gz") as tar:
        manifest = {
            "tool": "umbrella", "version": __version__, "profile": profile.name,
            "created": time.time(), "source": str(root), "include_secrets": include_secrets,
            "platform": platform or paths.detect_platform(),
        }
        data = json.dumps(manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(MANIFEST)
        info.size, info.mtime, info.mode = len(data), int(time.time()), 0o600
        tar.addfile(info, io.BytesIO(data))

        sources = []
        if root.is_dir():
            for folder, dirs, names in os.walk(str(root)):
                dirs.sort()
                rel_dir = os.path.relpath(folder, str(root))
                for name in sorted(names) + [d for d in dirs if os.path.islink(os.path.join(folder, d))]:
                    rel = os.path.normpath(os.path.join(rel_dir, name))
                    if rel in SECRET_FILES and not include_secrets:
                        skipped.append(rel)
                        continue
                    sources.append((os.path.join(folder, name), "config/" + rel.replace(os.sep, "/")))
        if profile.state_file.is_file():
            sources.append((str(profile.state_file), "state/.claude.json"))

        for source, arcname in sources:
            mode = os.lstat(source).st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                continue
            try:
                tar.add(source, arcname=arcname, recursive=False)
            except OSError:
                unreadable.append(arcname)
                continue
            files += 1
            size += os.lstat(source).st_size
    return BackupResult(output, files, size, skipped, unreadable)


class ArchiveInfo:
    def __init__(self, manifest, files, links, size):
        self.manifest = manifest
        self.files = files
        self.links = links
        self.size = size


def _check_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name:
        raise UmbrellaError("This archive contains an unsafe path ('{}'), so Umbrella won't restore it.".format(name))
    if name != MANIFEST and path.parts[0] not in ("config", "state"):
        raise UmbrellaError("This archive has an unexpected entry ('{}'). Is it an Umbrella backup?".format(name))


def read_archive(archive):
    """Check an archive is a safe Umbrella backup before touching anything."""
    archive = Path(archive)
    try:
        tar = tarfile.open(str(archive), mode="r:gz")
    except (OSError, tarfile.TarError):
        raise UmbrellaError("Couldn't open {} as an Umbrella backup.".format(paths.pretty(archive)),
                            hint="Backups are .tar.gz files made by 'umbrella backup'.")
    with tar:
        files, links, size, manifest = [], [], 0, None
        for member in tar.getmembers():
            _check_name(member.name)
            if member.name == MANIFEST:
                try:
                    manifest = json.loads(tar.extractfile(member).read().decode("utf-8"))
                except ValueError:
                    manifest = None
            elif member.issym() or member.islnk():
                links.append((member.name, member.linkname))
            elif member.isfile():
                files.append(member.name)
                size += member.size
            elif not member.isdir():
                raise UmbrellaError("This archive contains a special file ('{}'), so Umbrella won't restore it."
                                    .format(member.name))
    if not isinstance(manifest, dict) or manifest.get("tool") != "umbrella":
        raise UmbrellaError("{} isn't an Umbrella backup (no manifest).".format(paths.pretty(archive)))
    return ArchiveInfo(manifest, files, links, size)


def _target_for(name, profile):
    parts = PurePosixPath(name).parts
    if parts[0] == "state":
        return profile.state_file
    return profile.config_dir.joinpath(*parts[1:])


def restore_backup(archive, profile):
    """Restore regular files into ``profile``. Links are skipped and reported, never recreated.

    Files in the backup replace current copies; anything not in the backup is left alone.
    Files that would land outside the profile (for example inside a folder shared from another
    profile by a symlink) are skipped. Returns (restored count, links skipped, files skipped).
    """
    info = read_archive(archive)
    wanted = set(info.files)
    root = os.path.realpath(str(profile.config_dir))
    state = os.path.realpath(str(profile.state_file))
    restored, outside = 0, []
    with tarfile.open(str(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name not in wanted:
                continue
            target = _target_for(member.name, profile)
            real = os.path.realpath(str(target))
            if real != state and not real.startswith(root + os.sep):
                outside.append(member.name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_symlink():
                target.unlink()
            data = tar.extractfile(member).read()
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, (member.mode & 0o777) | 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.utime(str(target), (member.mtime, member.mtime))
            restored += 1
    return restored, info.links, outside
