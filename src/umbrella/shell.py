"""Shell integration: the hook that lets `umbrella use` change the current shell."""

import os
import shlex
from pathlib import Path

from umbrella import paths
from umbrella.errors import UmbrellaError

SHELLS = ("bash", "zsh", "fish")
HOOK_MARKER = "# Added by Umbrella (Claude Code profile switcher)"

POSIX_HOOK = """\
# Umbrella shell hook: lets `umbrella use <profile>` switch this shell.
umbrella() {
  case "$1" in
    use|off) eval "$(command umbrella __env --shell %(shell)s "$@")" ;;
    *) command umbrella "$@" ;;
  esac
}
# New shells start on your default profile.
if [ -z "${UMBRELLA_PROFILE:-}" ] && [ -z "${CLAUDE_CONFIG_DIR:-}" ]; then
  eval "$(command umbrella __env --shell %(shell)s --startup)"
fi
"""

FISH_HOOK = """\
# Umbrella shell hook: lets `umbrella use <profile>` switch this shell.
function umbrella
  switch "$argv[1]"
    case use off
      command umbrella __env --shell fish $argv | source
    case '*'
      command umbrella $argv
  end
end
# New shells start on your default profile.
if not set -q UMBRELLA_PROFILE; and not set -q CLAUDE_CONFIG_DIR
  command umbrella __env --shell fish --startup | source
end
"""


def detect_shell(env=None):
    env = os.environ if env is None else env
    name = os.path.basename(env.get("SHELL", ""))
    return name if name in SHELLS else "bash"


def check_shell(shell):
    if shell not in SHELLS:
        raise UmbrellaError(
            "Umbrella doesn't know the '{}' shell.".format(shell),
            hint="Supported shells: {}.".format(", ".join(SHELLS)),
        )
    return shell


def hook_script(shell):
    check_shell(shell)
    if shell == "fish":
        return FISH_HOOK
    return POSIX_HOOK % {"shell": shell}


def hook_line(shell):
    """The one line to put in a shell's startup file."""
    if check_shell(shell) == "fish":
        return "umbrella shell-init fish | source"
    return 'eval "$(umbrella shell-init {})"'.format(shell)


def rc_file(shell):
    if check_shell(shell) == "fish":
        return paths.home() / ".config" / "fish" / "config.fish"
    return paths.home() / ".{}rc".format(shell)


def hook_installed(shell):
    try:
        return "umbrella shell-init" in rc_file(shell).read_text()
    except OSError:
        return False


def install_hook(shell):
    """Append the hook line to the shell's rc file. Returns False if it was already there."""
    if hook_installed(shell):
        return False
    target = rc_file(shell)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text() if target.exists() else ""
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with target.open("a") as fh:
        fh.write("{}\n{}\n{}\n".format(prefix, HOOK_MARKER, hook_line(shell)))
    return True


def _fish_quote(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def env_script(changes, shell):
    """Shell code that applies ``changes`` ({name: value or None to unset})."""
    check_shell(shell)
    lines = []
    for name, value in sorted(changes.items()):
        if shell == "fish":
            lines.append("set -e {}".format(name) if value is None else "set -gx {} {}".format(name, _fish_quote(value)))
        else:
            lines.append("unset {}".format(name) if value is None else "export {}={}".format(name, shlex.quote(value)))
    return "\n".join(lines) + "\n"


def is_on_path(program, env=None):
    env = os.environ if env is None else env
    for folder in env.get("PATH", "").split(os.pathsep):
        candidate = Path(folder or ".") / program
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return True
    return False
