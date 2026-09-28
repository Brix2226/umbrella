import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TTYStringIO(io.StringIO):
    """A StringIO that claims to be a terminal."""

    encoding = "utf-8"

    def isatty(self):
        return True


class FakeHomeTest(unittest.TestCase):
    """Every test gets an empty temporary HOME, so nothing touches the real ~/.claude."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="umbrella-test-")).resolve()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        env = {"HOME": str(self.home), "PATH": str(self.bin), "SHELL": "/bin/zsh"}
        self._env = mock.patch.dict(os.environ, env)
        self._env.start()
        for name in ("UMBRELLA_DIR", "CLAUDE_CONFIG_DIR", "UMBRELLA_PROFILE", "NO_COLOR"):
            os.environ.pop(name, None)

    def tearDown(self):
        self._env.stop()
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    # --- builders -------------------------------------------------------------------------------

    def write(self, relpath, content="", base=None, mode=None):
        path = (base or self.home) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            content = json.dumps(content)
        path.write_text(content)
        if mode is not None:
            os.chmod(str(path), mode)
        return path

    def sign_in(self, state_file, email="me@example.com", org="Example Org"):
        account = {"emailAddress": email, "displayName": "Me", "organizationName": org,
                   "billingType": "stripe_subscription", "accessToken": "SHOULD-NEVER-BE-SHOWN"}
        state = {"oauthAccount": account, "projects": {"/a": {}, "/b": {}}, "mcpServers": {"github": {}},
                 "numStartups": 7, "firstStartTime": "2026-01-01"}
        return self.write(state_file, state, base=Path("/"))

    def stub(self, name, script):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + script + "\n")
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
        return path

    def pystub(self, name, code):
        """A stub command written in Python (so it doesn't depend on the test PATH)."""
        path = self.bin / name
        path.write_text("#!{}\n{}\n".format(sys.executable, code))
        path.chmod(0o755)
        return path

    def claude_tree(self, root=None):
        """A realistic Claude config directory."""
        root = root or self.home / ".claude"
        project = root / "projects" / "-Users-me-code-app"
        self.write("s1.jsonl", '{"type":"summary"}\nnot json\n{"cwd": "/Users/me/code/app"}\n', base=project)
        self.write("memory/notes.md", "remember", base=project)
        self.write("projects/-Users-me-old/x.jsonl", '{"type":"x"}\n', base=root)
        self.write("settings.json", {"model": "opus", "theme": "dark"}, base=root)
        self.write("skills/pdf/SKILL.md", "skill", base=root)
        self.write("plugins/installed_plugins.json", {"plugins": {"fmt@market": {}}}, base=root)
        self.write("cache/blob", "x" * 100, base=root)
        self.write("mystery.bin", "?", base=root)
        return root
