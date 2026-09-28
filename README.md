# Umbrella

Switch between Claude Code accounts, and see exactly what Claude keeps on your computer.

- **Two (or more) accounts, no juggling.** Each profile is its own Claude Code config directory,
  with its own login, history, settings and plugins. Switch a terminal with `umbrella use work`, or
  run both accounts side by side in different terminals.
- **Never touches your tokens.** Umbrella doesn't read, copy or store login credentials. Each profile
  signs in once through Claude's own `/login`; Umbrella just points `CLAUDE_CONFIG_DIR` at the right place.
- **A plain-language tour of `~/.claude`.** `umbrella inspect` explains every file and folder:
  what it is, how big, how old, whether it's private, and whether it's safe to delete.
- **Backups you can trust.** `umbrella backup` / `umbrella restore`, with login tokens left out by
  default and archives checked before anything is written.

Works on **macOS, Linux and Windows (WSL2)**. The only requirement is `python3` (3.8+), which
macOS and most Linux distributions already have. No pip, no sudo, no other dependencies.

## Install

```sh
cd ~/workspace/umbrella
./install.sh
```

This copies Umbrella to `~/.local/share/umbrella` and puts the `umbrella` command in `~/.local/bin`.
Remove it with `./install.sh --uninstall` (your profiles in `~/.umbrella` are kept).

## Quick start

```sh
umbrella init            # registers your current Claude setup as "personal" and offers to add the shell hook
umbrella add work        # creates a second, empty profile
umbrella login work      # opens Claude as "work"; type /login and sign in with your other account
umbrella list            # shows both profiles and who each is signed in as
```

Then, in any terminal:

```sh
umbrella use work        # this shell now runs Claude as your work account
claude
umbrella use personal    # and back
umbrella off             # stop using a profile; Claude falls back to ~/.claude
```

`umbrella use` changes your current shell, so it needs the one-line shell hook. `umbrella init`
offers to add it for you, or you can add it to `~/.zshrc` / `~/.bashrc` yourself:

```sh
eval "$(umbrella shell-init)"
```

(For fish: `umbrella shell-init fish | source` in `~/.config/fish/config.fish`.)

With the hook, new terminals start on your default profile. Change the default with
`umbrella use work --default`.

To use another account once without switching the shell:

```sh
umbrella run work                 # interactive Claude as "work"
umbrella run work -- -p "hello"   # anything after -- goes to claude
```

## Commands

| Command | What it does |
|---|---|
| `umbrella init` | Set up Umbrella and register your existing `~/.claude` (nothing is moved). |
| `umbrella add <name> [--share ...] [--path DIR]` | Create a profile. `--share settings,skills,...` or `--share all` links your customizations from `~/.claude`, so both accounts behave the same while history and logins stay separate. |
| `umbrella login <name>` | Open Claude as that profile so you can `/login`. |
| `umbrella list [--json]` | Profiles, their accounts, where their data lives and how big it is. `▸` marks this shell's profile. |
| `umbrella use <name> [--default]` / `umbrella off` | Switch this shell (needs the hook). |
| `umbrella run <name> [-- args]` | Run Claude once as a profile. |
| `umbrella current` | Which profile and account this shell is on. |
| `umbrella inspect [--profile X] [--all] [--json]` | Explain everything Claude stores for a profile. |
| `umbrella backup [--profile X] [-o FILE] [--include-secrets]` | Save a profile to a private `.tar.gz`. |
| `umbrella restore FILE [--profile X] [--yes]` | Restore a backup, after showing what's in it and saving your current state. |
| `umbrella remove <name> [--yes] [--keep-files] [--no-backup]` | Delete a profile. It's backed up first unless you say otherwise. Your original `~/.claude` is never deleted. |
| `umbrella doctor` | Check Python, Claude, the shell hook, and each profile's sign-in and permissions. |

## What `inspect` shows

The report groups everything into sections people care about:

1. **Your conversations:** transcripts per project, with the real project path (read from the
   transcripts), number of sessions, Claude's memory notes, size and when you last used it.
   Also covers prompt history, plans, to-do lists and file snapshots.
2. **Your customizations:** settings (which keys are set, never their values), `CLAUDE.md`,
   skills, subagents, slash commands and plugins.
3. **Account & login:** who you're signed in as, where the login is stored (macOS Keychain or
   `.credentials.json`), and a summary of Claude's main state file `.claude.json`.
4. **Runtime & caches:** files Claude recreates on its own, each marked *safe to delete*.
5. **Not recognized:** anything Umbrella doesn't know yet, so nothing is hidden.

Every item carries a sensitivity marker (🔒 secret, ◆ private content, · harmless), and the report
finishes with practical tips, such as a credentials file other users can read, large caches, or
projects you haven't opened in 90 days. Secret values are never printed. `--json` gives the same
data for scripts. Colors are off when output is piped or `NO_COLOR` is set.

## How it works

Claude Code reads everything from `~/.claude` (plus `~/.claude.json`) unless the environment
variable `CLAUDE_CONFIG_DIR` points somewhere else. In that case it keeps its settings, history
and state file (`$CLAUDE_CONFIG_DIR/.claude.json`) in that directory, and stores the login
separately:

| Platform | Where each profile's login lives |
|---|---|
| macOS | Keychain item `Claude Code-credentials` for `~/.claude`; `Claude Code-credentials-<hash>` for other profiles (the first 8 hex digits of the SHA-256 of the directory path) |
| Linux / WSL2 | `.credentials.json` inside the profile's directory |

Umbrella keeps a small registry at `~/.umbrella/profiles.json` (mode 600). New profiles go in
`~/.umbrella/profiles/<name>` (mode 700), and backups in `~/.umbrella/backups`. Set `UMBRELLA_DIR`
to keep all of that somewhere else.

**On WSL2:** run Claude Code and Umbrella inside the Linux distribution. Profiles live in the Linux
home directory, not on the Windows drive.

## Backups and safety

- Archives are created with mode 600 and contain the profile directory plus its `.claude.json`.
- `.credentials.json` is left out unless you pass `--include-secrets`. On macOS the login is in the
  Keychain and is never part of a backup.
- `restore` refuses archives with absolute paths, `..`, or device files. It never recreates
  symlinks, and it skips files that would land outside the target profile. It saves your current
  state before writing anything. Files in the backup replace current copies; other files are left alone.
- Restoring one profile's backup into another copies its account details (`.claude.json`) too, but
  not its login. Run `umbrella login` afterwards if you need that account there.

## Development

```sh
make test       # run the test suite with the system python3
make coverage   # run it under coverage.py (installed into .venv); fails below 100% line + branch coverage
```

The tests build a fake home directory for every test, so they never touch your real `~/.claude`.
The `claude` and `security` commands are replaced with stub scripts on a temporary `PATH`.
The only development dependency is `coverage`.
