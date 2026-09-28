"""Asking the user questions, without ever hanging a script."""

import sys


def _interactive(stdin):
    return hasattr(stdin, "isatty") and stdin.isatty()


def confirm(question, assume_yes=False, default=False, stdin=None, stdout=None):
    """Ask a yes/no question. Returns False when nobody's there to answer (unless --yes)."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if assume_yes:
        return True
    if not _interactive(stdin):
        stdout.write("{}\n  Not running in a terminal, so not doing it. Re-run with --yes to confirm.\n".format(question))
        return False
    stdout.write("{} {} ".format(question, "[Y/n]" if default else "[y/N]"))
    stdout.flush()
    answer = stdin.readline().strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def ask(question, default, stdin=None, stdout=None):
    """Ask for a value; non-interactive runs get the default."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if not _interactive(stdin):
        return default
    stdout.write("{} [{}] ".format(question, default))
    stdout.flush()
    return stdin.readline().strip() or default
