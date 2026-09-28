"""What each thing in a Claude Code config directory is, in plain language."""

import fnmatch

CONVERSATIONS = "conversations"
CUSTOMIZATIONS = "customizations"
ACCOUNT = "account"
RUNTIME = "runtime"
UNKNOWN = "unknown"

SECTIONS = [
    (CONVERSATIONS, "Your conversations", "What you and Claude said and did. Private: contains your prompts and code."),
    (CUSTOMIZATIONS, "Your customizations", "Settings and add-ons you (or your plugins) set up."),
    (ACCOUNT, "Account & login", "Who you're signed in as, and Claude's main state file."),
    (RUNTIME, "Runtime & caches", "Working files Claude recreates on its own. Safe to delete while Claude isn't running."),
    (UNKNOWN, "Not recognized", "Umbrella doesn't know these. They may come from a newer Claude Code or another tool."),
]

SECRET = "secret"
PRIVATE = "private"
HARMLESS = "harmless"


class Entry:
    def __init__(self, pattern, category, sensitivity, title, description, safe_to_delete=False):
        self.pattern = pattern
        self.category = category
        self.sensitivity = sensitivity
        self.title = title
        self.description = description
        self.safe_to_delete = safe_to_delete

    def to_dict(self):
        return {
            "category": self.category,
            "sensitivity": self.sensitivity,
            "title": self.title,
            "description": self.description,
            "safe_to_delete": self.safe_to_delete,
        }


ENTRIES = [
    # Conversations
    Entry("projects", CONVERSATIONS, PRIVATE, "Conversation transcripts",
          "Every session, saved per project folder. Includes your prompts, Claude's replies and file contents "
          "Claude read. Also holds Claude's per-project memory notes."),
    Entry("history.jsonl", CONVERSATIONS, PRIVATE, "Prompt history",
          "Everything you typed at the prompt; it's what the up-arrow recalls."),
    Entry("todos", CONVERSATIONS, PRIVATE, "Task lists", "To-do lists Claude kept while working.", True),
    Entry("plans", CONVERSATIONS, PRIVATE, "Plans", "Plans written in plan mode."),
    Entry("file-history", CONVERSATIONS, PRIVATE, "File snapshots",
          "Copies of files from before Claude edited them, used by rewind/undo.", True),
    Entry("tasks", CONVERSATIONS, PRIVATE, "Tasks", "Task lists and background task records.", True),
    Entry("teams", CONVERSATIONS, PRIVATE, "Agent teams", "Records of multi-agent team sessions.", True),
    Entry("jobs", CONVERSATIONS, PRIVATE, "Background jobs", "Records of background and scheduled jobs.", True),
    Entry("shares", CONVERSATIONS, PRIVATE, "Shared sessions", "Sessions you shared.", True),
    Entry("uploads", CONVERSATIONS, PRIVATE, "Uploads", "Files you attached to sessions.", True),
    Entry("file-transfers", CONVERSATIONS, PRIVATE, "File transfers", "Files moved to or from remote sessions.", True),
    Entry("loop.md", CONVERSATIONS, PRIVATE, "Loop notes", "Notes kept by the /loop command."),
    Entry("session-memory", CONVERSATIONS, PRIVATE, "Session memory", "Notes Claude kept during sessions.", True),
    # Customizations
    Entry("settings.json", CUSTOMIZATIONS, HARMLESS, "Settings", "Your personal Claude Code settings."),
    Entry("settings.local.json", CUSTOMIZATIONS, HARMLESS, "Local settings", "Machine-specific settings overrides."),
    Entry("CLAUDE.md", CUSTOMIZATIONS, PRIVATE, "Personal instructions",
          "Instructions Claude reads at the start of every session, in every project."),
    Entry("skills", CUSTOMIZATIONS, HARMLESS, "Skills", "Skills available in every project."),
    Entry("agents", CUSTOMIZATIONS, HARMLESS, "Subagents", "Custom subagents available in every project."),
    Entry("commands", CUSTOMIZATIONS, HARMLESS, "Slash commands", "Your custom /commands."),
    Entry("output-styles", CUSTOMIZATIONS, HARMLESS, "Output styles", "Custom output styles."),
    Entry("hooks", CUSTOMIZATIONS, HARMLESS, "Hook scripts", "Scripts run by hooks in your settings."),
    Entry("plugins", CUSTOMIZATIONS, HARMLESS, "Plugins", "Installed plugins and the marketplaces they came from."),
    Entry("keybindings.json", CUSTOMIZATIONS, HARMLESS, "Keyboard shortcuts", "Your custom key bindings."),
    Entry("statusline*", CUSTOMIZATIONS, HARMLESS, "Status line", "Your custom status line script."),
    # Account
    Entry(".credentials.json", ACCOUNT, SECRET, "Login tokens",
          "Your sign-in tokens. Anyone with a copy of this file can use your Claude account."),
    Entry(".device-keys.json", ACCOUNT, SECRET, "Device keys",
          "Keys that identify this computer to your Claude account. Don't share."),
    Entry(".session_ingress_token", ACCOUNT, SECRET, "Session token", "A short-lived token for remote sessions."),
    Entry("hfi-auth.json", ACCOUNT, SECRET, "Extra sign-in data", "Additional sign-in tokens."),
    Entry(".config.json", ACCOUNT, PRIVATE, "Old state file", "An older-format state file; Claude still reads it."),
    Entry(".claude.json", ACCOUNT, PRIVATE, "Main state file",
          "Your account details, per-project trust settings and MCP servers, and assorted caches."),
    Entry(".claude.json.backup*", ACCOUNT, PRIVATE, "State file backup", "An automatic copy of the main state file.", True),
    Entry("backups", ACCOUNT, PRIVATE, "State file backups", "Automatic copies of the main state file.", True),
    # Runtime
    Entry("sessions", RUNTIME, PRIVATE, "Session bookkeeping", "Records of running and recent sessions.", True),
    Entry("session-env", RUNTIME, HARMLESS, "Session environments", "Per-session environment scratch space.", True),
    Entry("shell-snapshots", RUNTIME, PRIVATE, "Shell snapshots",
          "A copy of your shell setup (aliases, functions, PATH) so Claude's commands behave like your terminal.", True),
    Entry("ide", RUNTIME, HARMLESS, "IDE connections", "Lock files that let editor extensions find Claude.", True),
    Entry("cache", RUNTIME, HARMLESS, "Cache", "Downloaded data Claude can fetch again.", True),
    Entry("paste-cache", RUNTIME, PRIVATE, "Paste cache", "Large pastes you made, kept for the session.", True),
    Entry("image-cache", RUNTIME, PRIVATE, "Image cache", "Images you pasted or attached.", True),
    Entry("statsig", RUNTIME, HARMLESS, "Feature flags", "Cached feature-flag data.", True),
    Entry("telemetry", RUNTIME, HARMLESS, "Telemetry queue", "Usage events waiting to be sent.", True),
    Entry("debug", RUNTIME, PRIVATE, "Debug logs", "Diagnostic logs; may include snippets of your sessions.", True),
    Entry("logs", RUNTIME, PRIVATE, "Logs", "Diagnostic logs.", True),
    Entry("stats-cache.json", RUNTIME, HARMLESS, "Usage stats cache", "Cached usage statistics.", True),
    Entry("policy-limits.json*", RUNTIME, HARMLESS, "Policy limits", "Cached limits set by your organization.", True),
    Entry("remote-settings.json", RUNTIME, HARMLESS, "Organization settings",
          "Settings pushed by your organization, cached locally.", True),
    Entry(".last-cleanup", RUNTIME, HARMLESS, "Cleanup marker", "When Claude last cleaned up old files.", True),
    Entry("state", RUNTIME, HARMLESS, "Internal state", "Small bookkeeping files Claude keeps between sessions.", True),
    Entry("usage-data", RUNTIME, HARMLESS, "Usage data", "Local usage and activity statistics.", True),
    Entry("active-time.json", RUNTIME, HARMLESS, "Active time", "How long sessions were active.", True),
    Entry("feedback*", RUNTIME, PRIVATE, "Feedback", "Feedback reports you started or sent, with session excerpts.", True),
    Entry("dump-prompts", RUNTIME, PRIVATE, "Prompt dumps", "Debug copies of prompts sent to the model.", True),
    Entry("api-dumps", RUNTIME, PRIVATE, "API dumps", "Debug copies of API requests.", True),
    Entry("traces", RUNTIME, PRIVATE, "Traces", "Performance traces for debugging.", True),
    Entry("startup-perf", RUNTIME, HARMLESS, "Startup timing", "Measurements of how fast Claude starts.", True),
    Entry("mcp-*cache*", RUNTIME, HARMLESS, "MCP cache", "Cached information about MCP servers.", True),
    Entry("mcp-skill-archives", RUNTIME, HARMLESS, "MCP skill archives", "Skills downloaded from MCP servers.", True),
    Entry("gh-pr-status-cache.json", RUNTIME, HARMLESS, "PR status cache", "Cached GitHub pull request status.", True),
    Entry("server-sessions.json", RUNTIME, HARMLESS, "Server sessions", "Sessions running in server mode.", True),
    Entry("daemon", RUNTIME, HARMLESS, "Background service", "Files for Claude's background service."),
    Entry("bridge-spawn", RUNTIME, HARMLESS, "Bridge", "Files for connecting to remote sessions.", True),
    Entry("ccr", RUNTIME, HARMLESS, "Remote sessions", "Files for cloud/remote sessions.", True),
    Entry("chrome", RUNTIME, HARMLESS, "Browser integration", "Files for the Chrome integration."),
    Entry("downloads", RUNTIME, HARMLESS, "Downloads", "Claude Code update downloads.", True),
    Entry("scratch", RUNTIME, PRIVATE, "Scratch space", "Temporary working files.", True),
    Entry("storage-v2", RUNTIME, PRIVATE, "Local storage", "Claude's local key-value storage."),
    Entry("local", RUNTIME, HARMLESS, "Local install", "A copy of Claude Code installed just for you."),
    Entry("*.lock", RUNTIME, HARMLESS, "Lock file", "Stops two Claude processes writing at once.", True),
]


def lookup(name):
    """The catalog entry for a top-level name in a Claude config dir, or None."""
    for entry in ENTRIES:
        if fnmatch.fnmatchcase(name, entry.pattern):
            return entry
    return None


SENSITIVITY_LABELS = {
    SECRET: "Secret: never share",
    PRIVATE: "Private content",
    HARMLESS: "Harmless",
}
