"""Walk a Claude config directory and describe everything in it."""

import json
import os
import stat
import time

from umbrella import catalog, paths, runner
from umbrella.fmt import human_size, plural

INACTIVE_DAYS = 90
BIG_CACHE_BYTES = 50 * 1024 * 1024
# Fields from .claude.json that are safe and useful to show. Nothing else is ever read out.
ACCOUNT_FIELDS = (
    ("emailAddress", "email"),
    ("displayName", "name"),
    ("organizationName", "organization"),
    ("organizationRole", "role"),
    ("billingType", "billing"),
)


def scan_tree(path):
    """(bytes, files, newest mtime) for a file or directory, without following symlinks."""
    try:
        info = os.lstat(str(path))
    except OSError:
        return 0, 0, 0
    if not stat.S_ISDIR(info.st_mode):
        return info.st_size, 1, info.st_mtime
    size, files, newest = 0, 0, info.st_mtime
    for root, dirs, names in os.walk(str(path), onerror=lambda err: None):
        for name in names + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            try:
                entry = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            size += entry.st_size
            files += 1
            newest = max(newest, entry.st_mtime)
    return size, files, newest


def _read_json(path):
    try:
        with open(str(path)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _project_path(folder):
    """Recover a project's real path from the `cwd` recorded in its transcripts."""
    for transcript in sorted(folder.glob("*.jsonl")):
        try:
            with transcript.open() as fh:
                for _, line in zip(range(50), fh):
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and record.get("cwd"):
                        return record["cwd"], True
        except OSError:
            continue
    # Claude names the folder after the path with '/' (and '.') turned into '-'; undo it approximately.
    return folder.name.replace("-", "/"), False


def summarize_projects(projects_dir, now):
    projects = []
    if not projects_dir.is_dir():
        return projects
    for folder in sorted(p for p in projects_dir.iterdir() if p.is_dir() and not p.is_symlink()):
        size, files, newest = scan_tree(folder)
        real_path, exact = _project_path(folder)
        memory = folder / "memory"
        projects.append({
            "folder": folder.name,
            "path": real_path,
            "path_exact": exact,
            "sessions": len(list(folder.glob("*.jsonl"))),
            "memory_files": len([m for m in memory.glob("*.md")]) if memory.is_dir() else 0,
            "size": size,
            "files": files,
            "last_used": newest,
            "inactive": now - newest > INACTIVE_DAYS * 86400,
        })
    projects.sort(key=lambda p: p["last_used"], reverse=True)
    return projects


def summarize_state(state_file):
    """Only the non-secret, human-meaningful parts of Claude's state file."""
    data = _read_json(state_file)
    if not isinstance(data, dict):
        return {"exists": state_file.exists(), "readable": False, "path": str(state_file)}
    raw = data.get("oauthAccount") if isinstance(data.get("oauthAccount"), dict) else {}
    account = {label: raw[key] for key, label in ACCOUNT_FIELDS if raw.get(key)}
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    mcp = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else {}
    return {
        "exists": True,
        "readable": True,
        "path": str(state_file),
        "size": scan_tree(state_file)[0],
        "account": account,
        "known_projects": len(projects),
        "mcp_servers": sorted(mcp),
        "first_start": data.get("firstStartTime"),
        "startups": data.get("numStartups"),
        "keys": len(data),
    }


def _item_details(path, entry_name):
    details = {}
    if entry_name.startswith("settings") and entry_name.endswith(".json"):
        data = _read_json(path)
        details["keys"] = sorted(data) if isinstance(data, dict) else []
    elif entry_name in ("skills", "agents", "commands", "output-styles") and path.is_dir():
        details["names"] = sorted(p.stem for p in path.iterdir() if not p.name.startswith("."))
    elif entry_name == "plugins" and path.is_dir():
        installed = _read_json(path / "installed_plugins.json")
        plugins = installed.get("plugins") if isinstance(installed, dict) else None
        details["names"] = sorted(plugins) if isinstance(plugins, dict) else []
    elif entry_name == ".credentials.json":
        details["mode"] = oct(stat.S_IMODE(os.lstat(str(path)).st_mode))
        details["too_open"] = bool(stat.S_IMODE(os.lstat(str(path)).st_mode) & 0o077)
    return details


def inspect_profile(profile, platform=None, now=None):
    now = time.time() if now is None else now
    platform = platform or paths.detect_platform()
    root = profile.config_dir
    items = []
    if root.is_dir():
        for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            entry = catalog.lookup(child.name)
            size, files, newest = scan_tree(child)
            items.append({
                "name": child.name,
                "path": str(child),
                "is_dir": child.is_dir() and not child.is_symlink(),
                "link_to": os.readlink(str(child)) if child.is_symlink() else None,
                "size": size,
                "files": files,
                "modified": newest,
                "entry": entry.to_dict() if entry else None,
                "details": _item_details(child, child.name) if entry else {},
            })

    state = summarize_state(profile.state_file)
    if profile.uses_default_dir and state["exists"]:
        # The default profile's state file lives beside ~/.claude, not in it; show it with the account items.
        entry = catalog.lookup(".claude.json")
        size, files, newest = scan_tree(profile.state_file)
        items.append({
            "name": paths.pretty(profile.state_file), "path": str(profile.state_file), "is_dir": False,
            "link_to": None, "size": size, "files": files, "modified": newest,
            "entry": entry.to_dict(), "details": {},
        })

    sections = []
    for key, title, blurb in catalog.SECTIONS:
        members = [i for i in items if (i["entry"]["category"] if i["entry"] else catalog.UNKNOWN) == key]
        if members:
            sections.append({
                "key": key, "title": title, "blurb": blurb, "items": members,
                "size": sum(i["size"] for i in members),
            })

    projects = summarize_projects(root / "projects", now)
    report = {
        "profile": profile.name,
        "config_dir": str(root),
        "exists": root.is_dir(),
        "platform": platform,
        "generated": now,
        "total_size": sum(i["size"] for i in items),
        "total_files": sum(i["files"] for i in items),
        "state": state,
        "credentials": credentials_location(profile, platform, items),
        "sections": sections,
        "projects": projects,
    }
    report["hints"] = hints(report)
    return report


def credentials_location(profile, platform, items):
    cred = next((i for i in items if i["name"] == ".credentials.json"), None)
    if cred:
        return {"where": "file", "path": cred["path"], "too_open": cred["details"]["too_open"],
                "mode": cred["details"]["mode"]}
    if platform == "macos":
        return {"where": "keychain", "service": runner.keychain_service(profile),
                "present": runner.keychain_has_login(profile)}
    return {"where": "none"}


def hints(report):
    tips = []
    creds = report["credentials"]
    if creds["where"] == "file" and creds["too_open"]:
        tips.append("Your login tokens file can be read by other users on this machine (mode {}). "
                    "Fix it with: chmod 600 {}".format(creds["mode"], paths.pretty(creds["path"])))
    runtime = next((s for s in report["sections"] if s["key"] == catalog.RUNTIME), None)
    if runtime and runtime["size"] >= BIG_CACHE_BYTES:
        tips.append("Runtime files and caches take {}. You can delete them while Claude isn't running "
                    "to free space.".format(human_size(runtime["size"])))
    inactive = [p for p in report["projects"] if p["inactive"]]
    if inactive:
        tips.append("{} not used in over {} days ({}). Their transcripts can be backed up and removed.".format(
            plural(len(inactive), "project"), INACTIVE_DAYS, human_size(sum(p["size"] for p in inactive))))
    unknown = next((s for s in report["sections"] if s["key"] == catalog.UNKNOWN), None)
    if unknown:
        tips.append("{} not recognized; see 'Not recognized' above. Nothing is hidden.".format(
            plural(len(unknown["items"]), "item")))
    if report["projects"]:
        tips.append("Transcripts contain your code and prompts. Treat backups of this directory like source code.")
    return tips
