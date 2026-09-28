"""The `umbrella` command."""

import argparse
import json
import os
import shutil
import sys

from umbrella import __version__, backup, desktop, inspector, paths, profiles, prompt, runner, shell
from umbrella.errors import UmbrellaError
from umbrella.fmt import human_size, plural
from umbrella.render import Style, render_profiles, render_report

DESCRIPTION = """\
Switch between Claude Code accounts, and see what Claude keeps on your computer.

Each profile is a separate Claude Code config directory with its own login,
history and settings. Quick start:

  umbrella init              register your current Claude setup
  umbrella add work          make a second profile
  umbrella login work        sign in to it (type /login inside Claude)
  umbrella use work          switch this shell (needs the shell hook)
  umbrella run personal      or launch Claude once as another profile
  umbrella inspect           see everything Claude stores, explained
"""


class Context:
    def __init__(self, stdout, stderr, stdin, env):
        self.stdout = stdout
        self.stderr = stderr
        self.stdin = stdin
        self.env = env
        self.style = Style.for_stream(stdout, env)
        self.err_style = Style.for_stream(stderr, env)

    def say(self, text=""):
        self.stdout.write(text + "\n")

    def confirm(self, question, args, default=False):
        return prompt.confirm(question, assume_yes=getattr(args, "yes", False), default=default,
                              stdin=self.stdin, stdout=self.stdout)


def _registry():
    return profiles.Registry()


def _resolve(ctx, name):
    """The profile to act on: the one named, else this shell's, else the default, else Claude's own dir."""
    reg = _registry()
    if name:
        reg.require_initialized()
        return reg.get(name)
    current = reg.current(ctx.env)
    if current:
        return current
    if reg.default:
        return reg.get(reg.default)
    if ctx.env.get("CLAUDE_CONFIG_DIR"):
        return profiles.Profile("(CLAUDE_CONFIG_DIR)", ctx.env["CLAUDE_CONFIG_DIR"])
    return profiles.Profile("default", None)


# ----- commands ---------------------------------------------------------------------------------

def cmd_init(ctx, args):
    reg = _registry()
    s = ctx.style
    if reg.exists and reg.profiles:
        ctx.say(s.ok("Umbrella is already set up with {}.".format(plural(len(reg.profiles), "profile"))))
        ctx.say("  Run 'umbrella list' to see them.")
        return 0
    existing = profiles.Profile("_", None)
    account = existing.account()
    if existing.config_dir.is_dir() or account:
        who = " (signed in as {})".format(account["emailAddress"]) if account else ""
        ctx.say("Found your existing Claude Code setup in {}{}.".format(paths.pretty(existing.config_dir), who))
        name = args.name or prompt.ask("What should this profile be called?", "personal", ctx.stdin, ctx.stdout)
        reg.add(name)
        ctx.say(s.ok("Registered it as profile '{}'. Nothing was moved or changed.".format(name)))
    else:
        ctx.say("No existing Claude Code setup found. That's fine: add profiles with 'umbrella add <name>'.")
    reg.save()
    ctx.say(s.ok("Saved profile list to {}".format(paths.pretty(reg.file))))

    sh = args.shell or shell.detect_shell(ctx.env)
    if shell.hook_installed(sh):
        ctx.say(s.ok("Shell hook already in {}".format(paths.pretty(shell.rc_file(sh)))))
    elif ctx.confirm("Add the Umbrella hook to {} so 'umbrella use' can switch your shell?".format(
            paths.pretty(shell.rc_file(sh))), args):
        shell.install_hook(sh)
        ctx.say(s.ok("Added. Open a new terminal (or run: source {}) to start using it.".format(
            paths.pretty(shell.rc_file(sh)))))
    else:
        ctx.say("  To add it yourself, put this line in {}:\n    {}".format(
            paths.pretty(shell.rc_file(sh)), shell.hook_line(sh)))
    ctx.say("")
    ctx.say(s.heading("Next steps"))
    ctx.say("  umbrella add work        create a profile for your other account")
    ctx.say("  umbrella login work      sign in to it")
    return 0


def cmd_add(ctx, args):
    reg = _registry()
    reg.require_initialized()
    profiles.validate_name(args.name)
    keys = profiles.parse_share(args.share)
    if args.path:
        target = os.path.abspath(os.path.expanduser(args.path))
        os.makedirs(target, exist_ok=True)
    else:
        if args.name in reg.profiles:
            raise UmbrellaError("A profile called '{}' already exists.".format(args.name))
        target = str(profiles.create_profile_dir(args.name))
    profile = reg.add(args.name, target)
    reg.save()
    s = ctx.style
    ctx.say(s.ok("Created profile '{}' in {}".format(profile.name, paths.pretty(target))))
    if keys:
        linked, missing = profiles.share_items(profile, keys)
        if linked:
            ctx.say(s.ok("Sharing with your default setup: {}".format(", ".join(linked))))
        if missing:
            ctx.say(s.warn("Not shared (missing in {}, or already present): {}".format(
                paths.pretty(paths.default_claude_dir()), ", ".join(missing))))
    ctx.say("")
    ctx.say("Next, sign in: umbrella login {}".format(profile.name))
    return 0


def cmd_login(ctx, args):
    reg = _registry()
    reg.require_initialized()
    profile = reg.get(args.name)
    s = ctx.style
    runner.require_claude(ctx.env)  # explain a missing install before promising to open anything
    ctx.say("Opening Claude Code as profile '{}'.".format(profile.name))
    ctx.say("  Type /login and sign in with the account you want for this profile, then /exit.")
    code = runner.run_claude(profile, [], ctx.env)
    account = profile.account()
    if account:
        ctx.say(s.ok("Profile '{}' is signed in as {}".format(profile.name, account["emailAddress"])))
    else:
        ctx.say(s.warn("Profile '{}' isn't signed in yet. Run 'umbrella login {}' again when you're ready."
                       .format(profile.name, profile.name)))
    return code


def cmd_run(ctx, args):
    reg = _registry()
    reg.require_initialized()
    extra = args.claude_args[1:] if args.claude_args[:1] == ["--"] else args.claude_args
    return runner.run_claude(reg.get(args.name), extra, ctx.env)


def _profile_rows(reg, env):
    current = reg.current(env)
    rows = []
    for name in reg.names():
        p = reg.profiles[name]
        account = p.account()
        rows.append({
            "name": name,
            "active": current is not None and current.name == name,
            "default": reg.default == name,
            "account": {"email": account["emailAddress"], "organization": account.get("organizationName")}
            if account else None,
            "dir": str(p.config_dir),
            "size": inspector.scan_tree(p.config_dir)[0],
        })
    return rows


def cmd_list(ctx, args):
    reg = _registry()
    reg.require_initialized()
    rows = _profile_rows(reg, ctx.env)
    if args.json:
        ctx.say(json.dumps(rows, indent=2))
        return 0
    if not rows:
        ctx.say("No profiles yet. Create one with 'umbrella add <name>'.")
        return 0
    ctx.say(render_profiles(rows, ctx.style))
    if not any(r["active"] for r in rows):
        ctx.say(ctx.style.muted("\nThis shell isn't using any of these profiles right now."))
    return 0


def cmd_current(ctx, args):
    reg = _registry()
    s = ctx.style
    profile = reg.current(ctx.env) if reg.exists else None
    if profile is None:
        custom = ctx.env.get("CLAUDE_CONFIG_DIR")
        if custom:
            ctx.say(s.warn("This shell uses CLAUDE_CONFIG_DIR={}, which isn't an Umbrella profile.".format(custom)))
        else:
            ctx.say("This shell uses Claude's default directory ({}), which isn't registered with Umbrella."
                    .format(paths.pretty(paths.default_claude_dir())))
            ctx.say("  Run 'umbrella init' to register it.")
        return 1
    account = profile.account()
    ctx.say("{} {}".format(s.heading("Profile:"), profile.name))
    ctx.say("{} {}".format(s.heading("Account:"), account["emailAddress"] if account else "not signed in"))
    ctx.say("{} {}".format(s.heading("Claude data:"), paths.pretty(profile.config_dir)))
    return 0


def cmd_use(ctx, args):
    # Only reached when the shell hook isn't loaded (the hook sends `use` to `__env`).
    reg = _registry()
    reg.require_initialized()
    profile = reg.get(args.name)
    if args.default:
        reg.set_default(profile.name)
        reg.save()
        ctx.say(ctx.style.ok("New shells will start on '{}'.".format(profile.name)))
    sh = shell.detect_shell(ctx.env)
    ctx.say(ctx.style.warn("This shell can't be switched because the Umbrella shell hook isn't loaded."))
    ctx.say("  Set it up once:  umbrella init   (or add this to {}: {})".format(
        paths.pretty(shell.rc_file(sh)), shell.hook_line(sh)))
    ctx.say("  Switch just now: {}".format(
        "umbrella __env --shell fish use {} | source".format(profile.name) if sh == "fish"
        else 'eval "$(umbrella __env use {})"'.format(profile.name)))
    ctx.say("  Or run Claude once as this profile: umbrella run {}".format(profile.name))
    return 1


def cmd_off(ctx, args):
    # Only reached when the shell hook isn't loaded.
    ctx.say(ctx.style.warn("This shell can't be switched because the Umbrella shell hook isn't loaded."))
    ctx.say("  To stop using a profile right now: unset CLAUDE_CONFIG_DIR UMBRELLA_PROFILE")
    return 1


def cmd_env(ctx, args):
    """Hidden: print shell code for the hook. Messages go to stderr so `eval` only sees code."""
    sh = shell.check_shell(args.shell or shell.detect_shell(ctx.env))
    reg = _registry()
    err = ctx.err_style
    if args.startup:
        if reg.exists and reg.default in reg.profiles and not ctx.env.get("CLAUDE_CONFIG_DIR"):
            ctx.stdout.write(shell.env_script(reg.profiles[reg.default].env(), sh))
        return 0
    words = args.words
    if words[:1] == ["off"]:
        ctx.stdout.write(shell.env_script({"CLAUDE_CONFIG_DIR": None, "UMBRELLA_PROFILE": None}, sh))
        ctx.stderr.write(err.ok("Back to Claude's default directory ({})".format(
            paths.pretty(paths.default_claude_dir()))) + "\n")
        return 0
    if len(words) < 2 or words[0] != "use":
        raise UmbrellaError("Tell me which profile to use.", hint="Example: umbrella use work")
    reg.require_initialized()
    profile = reg.get(words[1])
    if "--default" in words[2:]:
        reg.set_default(profile.name)
        reg.save()
    ctx.stdout.write(shell.env_script(profile.env(), sh))
    account = profile.account()
    ctx.stderr.write(err.ok("Switched to '{}'{}".format(
        profile.name, " ({})".format(account["emailAddress"]) if account else " (not signed in yet)")) + "\n")
    return 0


def cmd_shell_init(ctx, args):
    ctx.stdout.write(shell.hook_script(args.shell or shell.detect_shell(ctx.env)))
    return 0


def cmd_desktop(ctx, args):
    reg = _registry()
    reg.require_initialized()
    profile = reg.get(args.name) if args.name else (reg.current(ctx.env) or (reg.default and reg.get(reg.default)))
    if not profile:
        raise UmbrellaError("Which profile should Claude Desktop open?", hint="Example: umbrella desktop work")
    s = ctx.style
    platform = paths.detect_platform()
    if args.shortcut:
        where = desktop.create_shortcut(profile, platform, ctx.env)
        if platform == "macos":
            ctx.say(s.ok("Created 'Claude ({})' in {}".format(profile.name, paths.pretty(where.parent))))
            ctx.say("  Open it from Spotlight or Launchpad, or drag it to your Dock.")
        else:
            ctx.say(s.ok("Added 'Claude ({})' to the Windows Start Menu.".format(profile.name)))
        return 0
    launch = desktop.plan_launch(profile, platform, ctx.env)
    desktop.start(launch)
    if profile.uses_default_dir:
        ctx.say(s.ok("Opening your normal Claude app (profile '{}').".format(profile.name)))
        return 0
    ctx.say(s.ok("Opening Claude Desktop as profile '{}'.".format(profile.name)))
    ctx.say(s.muted("  Its login and app data live in {}".format(
        paths.pretty(launch.data_dir) if platform == "macos" else launch.data_dir)))
    if platform == "wsl":
        ctx.say(s.muted("  Its Code tab keeps its own history in {} (separate from the CLI in WSL).".format(
            desktop.win_config_dir(profile))))
    if launch.first_run:
        ctx.say(s.wrap(desktop.FIRST_RUN_TIP.format(name=profile.name), "  "))
        ctx.say("  Tip: 'umbrella desktop {} --shortcut' makes a launcher you can click.".format(profile.name))
    return 0


def cmd_remove(ctx, args):
    reg = _registry()
    reg.require_initialized()
    profile = reg.get(args.name)
    s = ctx.style
    delete_files = not profile.uses_default_dir and not args.keep_files and profile.config_dir.exists()
    size = inspector.scan_tree(profile.config_dir)[0] if delete_files else 0
    what = ("Remove profile '{}' and delete its {} of Claude data in {}?".format(
        profile.name, human_size(size), paths.pretty(profile.config_dir))
        if delete_files else "Remove profile '{}' from Umbrella? (Its files stay where they are.)".format(profile.name))
    if not ctx.confirm(what, args):
        ctx.say("Nothing changed.")
        return 1
    if delete_files and not args.no_backup:
        result = backup.create_backup(profile, backup.default_backup_path(profile))
        ctx.say(s.ok("Backed up first to {}".format(paths.pretty(result.path))))
    reg.remove(profile.name)
    reg.save()
    if delete_files:
        shutil.rmtree(str(profile.config_dir))
        for item in desktop.remove_artifacts(profile):
            ctx.say(s.ok("Removed its Desktop app data: {}".format(item)))
        if runner.keychain_has_login(profile):
            runner.keychain_delete_login(profile)
            ctx.say(s.ok("Removed its saved login from the macOS Keychain."))
    ctx.say(s.ok("Removed profile '{}'.".format(profile.name)))
    return 0


def cmd_inspect(ctx, args):
    profile = _resolve(ctx, args.profile)
    report = inspector.inspect_profile(profile)
    if args.json:
        ctx.say(json.dumps(report, indent=2, default=str))
    else:
        ctx.stdout.write(render_report(report, ctx.style, show_all=args.all))
    return 0


def cmd_backup(ctx, args):
    profile = _resolve(ctx, args.profile)
    output = args.output or backup.default_backup_path(profile)
    result = backup.create_backup(profile, output, include_secrets=args.include_secrets)
    s = ctx.style
    ctx.say(s.ok("Backed up profile '{}': {}, {}".format(profile.name, plural(result.files, "file"),
                                                         human_size(result.size))))
    ctx.say("  Saved to {} (only you can read it)".format(paths.pretty(result.path)))
    if result.skipped_secrets:
        ctx.say("  Left out your login tokens ({}). Pass --include-secrets to keep them.".format(
            ", ".join(result.skipped_secrets)))
    if args.include_secrets:
        ctx.say(s.warn("This backup contains your login tokens. Anyone with it can use your account."))
    if result.unreadable:
        ctx.say(s.warn("Couldn't read {}: {}".format(plural(len(result.unreadable), "file"),
                                                     ", ".join(result.unreadable))))
    ctx.say(s.muted("  It includes your conversation transcripts, which contain your code and prompts."))
    return 0


def cmd_restore(ctx, args):
    info = backup.read_archive(args.archive)
    profile = _resolve(ctx, args.profile)
    s = ctx.style
    m = info.manifest
    ctx.say(s.heading("Backup of profile '{}'".format(m.get("profile", "?"))))
    ctx.say("  {} ({}){}".format(plural(len(info.files), "file"), human_size(info.size),
                                 ", includes login tokens" if m.get("include_secrets") else ""))
    ctx.say("  Restoring into '{}' at {}".format(profile.name, paths.pretty(profile.config_dir)))
    ctx.say(s.muted("  Files in the backup replace your current copies. Other files are left alone."))
    if not ctx.confirm("Restore now?", args):
        ctx.say("Nothing changed.")
        return 1
    safety = backup.create_backup(profile, backup.default_backup_path(profile), include_secrets=True)
    ctx.say(s.ok("Saved your current state first to {}".format(paths.pretty(safety.path))))
    restored, links, outside = backup.restore_backup(args.archive, profile)
    ctx.say(s.ok("Restored {}.".format(plural(restored, "file"))))
    if links:
        ctx.say(s.warn("Skipped {} (not recreated, for safety): {}".format(
            plural(len(links), "link"), ", ".join("{} -> {}".format(a, b) for a, b in links))))
        ctx.say("  To share folders again, use 'umbrella add --share'.")
    if outside:
        ctx.say(s.warn("Skipped {} that would land outside this profile: {}".format(
            plural(len(outside), "file"), ", ".join(outside))))
    return 0


def cmd_doctor(ctx, args):
    s = ctx.style
    checks = []  # (level, message, fix)
    v = sys.version_info
    checks.append(("ok" if v >= (3, 8) else "fail", "Python {}.{}".format(v[0], v[1]),
                   None if v >= (3, 8) else "Umbrella needs Python 3.8 or newer."))
    platform = paths.detect_platform()
    checks.append(("ok", "Platform: {}".format(paths.platform_name(platform)), None))
    try:
        claude = runner.require_claude(ctx.env, platform)
        checks.append(("ok", "Claude Code found at {}".format(paths.pretty(claude)), None))
    except UmbrellaError as exc:
        checks.append(("fail", str(exc), exc.hint))
    if platform in ("macos", "wsl"):
        try:
            desktop.plan_launch(profiles.Profile("_"), platform, ctx.env)
            checks.append(("ok", "Claude Desktop app found", None))
        except UmbrellaError as exc:
            checks.append(("warn", str(exc), exc.hint))
    checks.append(("ok", "'umbrella' is on your PATH", None) if shell.is_on_path("umbrella", ctx.env) else
                  ("warn", "'umbrella' isn't on your PATH", "Run ./install.sh, or add ~/.local/bin to PATH."))
    sh = shell.detect_shell(ctx.env)
    checks.append(("ok", "Shell hook is in {}".format(paths.pretty(shell.rc_file(sh))), None)
                  if shell.hook_installed(sh) else
                  ("warn", "Shell hook isn't set up for {}".format(sh),
                   "Add to {}: {}".format(paths.pretty(shell.rc_file(sh)), shell.hook_line(sh))))
    reg = _registry()
    if not reg.exists:
        checks.append(("warn", "Umbrella isn't set up yet", "Run 'umbrella init'."))
    for name in reg.names():
        p = reg.profiles[name]
        if not p.config_dir.is_dir():
            checks.append(("warn", "Profile '{}': {} doesn't exist yet".format(name, paths.pretty(p.config_dir)),
                           "It's created when you run 'umbrella login {}'.".format(name)))
            continue
        mode = os.stat(str(p.config_dir)).st_mode & 0o777
        if not p.uses_default_dir and mode & 0o077:
            checks.append(("warn", "Profile '{}' can be read by other users (mode {})".format(name, oct(mode)),
                           "chmod 700 {}".format(paths.pretty(p.config_dir))))
        account = p.account()
        checks.append(("ok", "Profile '{}' is signed in as {}".format(name, account["emailAddress"]), None)
                      if account else
                      ("warn", "Profile '{}' isn't signed in".format(name), "umbrella login {}".format(name)))

    for level, message, fix in checks:
        ctx.say(getattr(s, level)(message))
        if fix:
            ctx.say(s.muted("    " + fix))
    problems = [c for c in checks if c[0] == "fail"]
    warnings = [c for c in checks if c[0] == "warn"]
    ctx.say("")
    if problems:
        ctx.say(s.fail("{} to fix.".format(plural(len(problems), "problem"))))
    elif warnings:
        ctx.say(s.ok("Umbrella works. {} above {} worth a look.".format(
            plural(len(warnings), "suggestion"), "is" if len(warnings) == 1 else "are")))
    else:
        ctx.say(s.ok("All good."))
    return 1 if problems else 0


# ----- argument parsing -------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(prog="umbrella", description=DESCRIPTION,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version="umbrella " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def add(name, func, help_text, **kw):
        p = sub.add_parser(name, help=help_text, description=help_text, **kw)
        p.set_defaults(func=func)
        return p

    p = add("init", cmd_init, "Set up Umbrella and register your current Claude setup.")
    p.add_argument("--name", help="name for your existing Claude setup (default: ask, or 'personal')")
    p.add_argument("--shell", choices=shell.SHELLS, help="shell to add the hook to (default: your $SHELL)")
    p.add_argument("-y", "--yes", action="store_true", help="add the shell hook without asking")

    p = add("add", cmd_add, "Create a new profile.")
    p.add_argument("name")
    p.add_argument("--share", metavar="ITEMS",
                   help="link these from your default setup: {} or 'all'".format(",".join(sorted(profiles.SHAREABLE))))
    p.add_argument("--path", help="use this directory instead of ~/.umbrella/profiles/<name>")

    p = add("login", cmd_login, "Open Claude Code as a profile so you can sign in.")
    p.add_argument("name")

    p = add("run", cmd_run, "Run Claude Code once as a profile, without switching your shell.")
    p.add_argument("name")
    p.add_argument("claude_args", nargs=argparse.REMAINDER, help="arguments passed to claude")

    p = add("list", cmd_list, "Show your profiles and who each is signed in as.")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    add("current", cmd_current, "Show which profile this shell is using.")

    p = add("use", cmd_use, "Switch this shell to a profile (needs the shell hook).")
    p.add_argument("name")
    p.add_argument("--default", action="store_true", help="also make it the profile new shells start with")

    p = add("desktop", cmd_desktop, "Open the Claude Desktop app as a profile (macOS, or Windows from WSL2).")
    p.add_argument("name", nargs="?", help="profile to open (default: this shell's, else your default)")
    p.add_argument("--shortcut", action="store_true",
                   help="create a clickable 'Claude (<name>)' launcher instead of opening it now")

    add("off", cmd_off, "Stop using a profile in this shell (needs the shell hook).")

    p = add("remove", cmd_remove, "Remove a profile (backs it up first).")
    p.add_argument("name")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    p.add_argument("--keep-files", action="store_true", help="unregister only; leave its files on disk")
    p.add_argument("--no-backup", action="store_true", help="don't make a backup before deleting")

    p = add("inspect", cmd_inspect, "Explain everything Claude Code stores for a profile.")
    p.add_argument("--profile", help="profile to inspect (default: this shell's)")
    p.add_argument("--all", action="store_true", help="list every project, not just the 10 most recent")
    p.add_argument("--json", action="store_true", help="machine-readable output")

    p = add("backup", cmd_backup, "Save a profile's Claude data to a .tar.gz file.")
    p.add_argument("--profile", help="profile to back up (default: this shell's)")
    p.add_argument("-o", "--output", help="where to write the backup")
    p.add_argument("--include-secrets", action="store_true", help="also include login tokens (.credentials.json)")

    p = add("restore", cmd_restore, "Restore a backup into a profile.")
    p.add_argument("archive")
    p.add_argument("--profile", help="profile to restore into (default: this shell's)")
    p.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")

    add("doctor", cmd_doctor, "Check that everything is set up correctly.")

    p = add("shell-init", cmd_shell_init, "Print the shell hook (put `eval \"$(umbrella shell-init)\"` in your rc).")
    p.add_argument("shell", nargs="?", choices=shell.SHELLS)

    p = sub.add_parser("__env")  # used by the shell hook
    p.set_defaults(func=cmd_env)
    p.add_argument("--shell")
    p.add_argument("--startup", action="store_true")
    p.add_argument("words", nargs=argparse.REMAINDER)
    return parser


def main(argv=None, stdout=None, stderr=None, stdin=None, env=None):
    ctx = Context(stdout or sys.stdout, stderr or sys.stderr, stdin or sys.stdin,
                  os.environ if env is None else env)
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not getattr(args, "func", None):
        ctx.stdout.write(parser.format_help())
        return 0
    try:
        return args.func(ctx, args)
    except UmbrellaError as exc:
        ctx.stderr.write(ctx.err_style.fail(str(exc)) + "\n")
        if exc.hint:
            ctx.stderr.write("  " + exc.hint + "\n")
        return 1
    except KeyboardInterrupt:
        ctx.stderr.write("\nCancelled.\n")
        return 130


def main_entry():
    sys.exit(main())
