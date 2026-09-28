"""Terminal output: color when it helps, plain text when piped, always readable."""

import os
import shutil
import textwrap

from umbrella import catalog, paths
from umbrella.fmt import human_age, human_size, plural

CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33", "blue": "34", "cyan": "36"}


class Style:
    def __init__(self, color=False, unicode=True, width=80):
        self.color = color
        self.unicode = unicode
        self.width = max(60, min(width, 110))

    @classmethod
    def for_stream(cls, stream, env=None):
        env = os.environ if env is None else env
        isatty = hasattr(stream, "isatty") and stream.isatty()
        color = isatty and "NO_COLOR" not in env and env.get("TERM") != "dumb"
        encoding = (getattr(stream, "encoding", None) or "").lower()
        return cls(color=color, unicode="utf" in encoding,
                   width=shutil.get_terminal_size((80, 24)).columns if isatty else 80)

    def paint(self, text, *styles):
        if not self.color or not styles:
            return text
        return "\033[{}m{}\033[0m".format(";".join(CODES[s] for s in styles), text)

    def sym(self, name):
        fancy = {"ok": "✓", "warn": "!", "fail": "✗", "arrow": "→", "active": "▸", "bullet": "•",
                 SECRET: "🔒", PRIVATE: "◆", HARMLESS: "·"}
        plain = {"ok": "OK", "warn": "!", "fail": "X", "arrow": "->", "active": ">", "bullet": "-",
                 SECRET: "[secret]", PRIVATE: "[private]", HARMLESS: " "}
        return (fancy if self.unicode else plain)[name]

    def ok(self, text):
        return "{} {}".format(self.paint(self.sym("ok"), "green", "bold"), text)

    def warn(self, text):
        return "{} {}".format(self.paint(self.sym("warn"), "yellow", "bold"), text)

    def fail(self, text):
        return "{} {}".format(self.paint(self.sym("fail"), "red", "bold"), text)

    def heading(self, text):
        return self.paint(text, "bold")

    def muted(self, text):
        return self.paint(text, "dim")

    def wrap(self, text, indent="", subsequent=None):
        return textwrap.fill(text, width=self.width, initial_indent=indent,
                             subsequent_indent=indent if subsequent is None else subsequent)


SECRET, PRIVATE, HARMLESS = catalog.SECRET, catalog.PRIVATE, catalog.HARMLESS
SENSITIVITY_COLORS = {SECRET: ("red", "bold"), PRIVATE: ("yellow",), HARMLESS: ("dim",)}


def table(rows, headers, style):
    """Left-aligned columns sized to their content. ``rows`` are lists of (plain, painted) pairs or strings."""
    def cell(value):
        return value if isinstance(value, tuple) else (value, value)

    grid = [[cell(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in grid:
        for i, (plain, _) in enumerate(row):
            widths[i] = max(widths[i], len(plain))
    lines = ["  ".join(style.heading(h.ljust(widths[i])) for i, h in enumerate(headers)).rstrip()]
    for row in grid:
        lines.append("  ".join(painted + " " * (widths[i] - len(plain)) for i, (plain, painted) in enumerate(row))
                     .rstrip())
    return "\n".join(lines)


def _account_line(account):
    if not account:
        return "not signed in"
    text = account.get("email") or account.get("emailAddress")
    org = account.get("organization") or account.get("organizationName")
    return "{} ({})".format(text, org) if org else text


def render_report(report, style, show_all=False):
    out = []
    add = out.append
    now = report["generated"]
    state = report["state"]

    add(style.heading("Claude Code data for profile '{}'".format(report["profile"])))
    add(style.muted(paths.pretty(report["config_dir"]) + " on " + paths.platform_name(report["platform"])))
    add("")
    if not report["exists"]:
        add(style.warn("This directory doesn't exist yet. Claude creates it the first time it runs with this profile."))
        return "\n".join(out) + "\n"

    add("  {:<14}{}".format("Account", _account_line(state.get("account"))))
    add("  {:<14}{} in {}".format("Total", human_size(report["total_size"]), plural(report["total_files"], "file")))
    add("  {:<14}{}".format("Projects", plural(len(report["projects"]), "project")
                          + (", {} known to Claude".format(state["known_projects"]) if state.get("readable") else "")))
    add("  {:<14}{}".format("Login stored", _credentials_line(report["credentials"], style)))
    add("")
    add(style.muted("Legend: {} secret   {} private content   {} harmless    "
                    "'safe to delete' means Claude recreates it.".format(
                        style.sym(SECRET), style.sym(PRIVATE), style.sym(HARMLESS) if style.unicode else "(blank)")))

    for section in report["sections"]:
        add("")
        add(style.heading("{}  {}".format(section["title"], style.muted(human_size(section["size"])))))
        add(style.muted(style.wrap(section["blurb"], "  ")))
        for item in section["items"]:
            add(_render_item(item, style, now))
        if section["key"] == catalog.CONVERSATIONS and report["projects"]:
            add(_render_projects(report["projects"], style, now, show_all))
        if section["key"] == catalog.ACCOUNT and state.get("readable"):
            add(_render_state(state, style))

    if report["hints"]:
        add("")
        add(style.heading("Worth knowing"))
        for tip in report["hints"]:
            add(style.wrap(tip, "  {} ".format(style.sym("bullet")), "    "))
    return "\n".join(out) + "\n"


def _credentials_line(creds, style):
    if creds["where"] == "file":
        text = "in {} (mode {})".format(paths.pretty(creds["path"]), creds["mode"])
        return style.paint(text, "red") if creds["too_open"] else text
    if creds["where"] == "keychain":
        state = {True: "found", False: "not found", None: "couldn't check"}[creds["present"]]
        return "macOS Keychain, item '{}' ({})".format(creds["service"], state)
    return "no saved login found"


def _render_item(item, style, now):
    entry = item["entry"]
    sensitivity = entry["sensitivity"] if entry else HARMLESS
    marker = style.paint(style.sym(sensitivity), *SENSITIVITY_COLORS[sensitivity])
    name = item["name"] + ("/" if item["is_dir"] else "")
    title = entry["title"] if entry else "Unknown"
    meta = [human_size(item["size"])]
    if item["is_dir"]:
        meta.append(plural(item["files"], "file"))
    meta.append("changed " + human_age(item["modified"], now))
    meta = style.muted(" · ".join(meta))
    if entry and entry["safe_to_delete"]:
        meta += style.muted(" · ") + style.paint("safe to delete", "green")
    lines = ["  {} {}  {}  {}".format(marker, style.paint(name, "cyan"), title, meta)]
    if item["link_to"]:
        lines.append(style.muted("      {} shared from {}".format(style.sym("arrow"), paths.pretty(item["link_to"]))))
    if entry:
        lines.append(style.muted(style.wrap(entry["description"], "      ")))
    details = item["details"]
    if details.get("keys"):
        lines.append(style.wrap("Settings in use: " + ", ".join(details["keys"]), "      "))
    if details.get("names"):
        lines.append(style.wrap("Installed: " + ", ".join(details["names"]), "      "))
    return "\n".join(lines)


def _render_projects(projects, style, now, show_all, limit=10):
    shown = projects if show_all else projects[:limit]
    rows = []
    for p in shown:
        path = paths.pretty(p["path"]) + ("" if p["path_exact"] else " (approx.)")
        used = human_age(p["last_used"], now)
        rows.append([
            "    " + path,
            plural(p["sessions"], "session"),
            str(p["memory_files"]) if p["memory_files"] else "-",
            human_size(p["size"]),
            (used, style.paint(used, "yellow")) if p["inactive"] else used,
        ])
    text = "\n" + table(rows, ["    Project", "Sessions", "Memory", "Size", "Last used"], style)
    if len(projects) > len(shown):
        text += "\n" + style.muted("    ...and {} more. Use --all to see every project.".format(len(projects) - len(shown)))
    return text


def _render_state(state, style):
    lines = [style.muted("      Main state file ({}):".format(paths.pretty(state["path"])))]
    for label, value in sorted(state["account"].items()):
        lines.append("        {:<13}{}".format(label.capitalize(), value))
    lines.append("        {:<13}{}".format("Projects", state["known_projects"]))
    if state["mcp_servers"]:
        lines.append(style.wrap("MCP servers: " + ", ".join(state["mcp_servers"]), "        "))
    if state.get("startups"):
        lines.append("        {:<13}{}".format("Launches", state["startups"]))
    return "\n".join(lines)


def render_profiles(rows, style):
    """rows: dicts with name, active, default, account, dir, size."""
    table_rows = []
    for r in rows:
        marker = style.paint(style.sym("active"), "green", "bold") if r["active"] else " "
        name = r["name"] + (" (default)" if r["default"] else "")
        account = _account_line(r["account"])
        table_rows.append([
            (" " if not r["active"] else style.sym("active"), marker),
            (name, style.paint(name, "bold") if r["active"] else name),
            (account, style.paint(account, "dim") if not r["account"] else account),
            paths.pretty(r["dir"]),
            human_size(r["size"]),
        ])
    return table(table_rows, [" ", "Profile", "Account", "Claude data", "Size"], style)
