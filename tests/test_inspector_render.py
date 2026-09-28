import io
import json
import os
import time
from unittest import mock

from helpers import FakeHomeTest, TTYStringIO
from umbrella import catalog, inspector, profiles, render
from umbrella.render import Style


class CatalogTest(FakeHomeTest):
    def test_lookup(self):
        self.assertEqual(catalog.lookup("projects").category, catalog.CONVERSATIONS)
        self.assertEqual(catalog.lookup("policy-limits.json.stamp.json").title, "Policy limits")
        self.assertEqual(catalog.lookup(".credentials.json").sensitivity, catalog.SECRET)
        self.assertIsNone(catalog.lookup("mystery.bin"))
        self.assertEqual(catalog.lookup("cache").to_dict()["safe_to_delete"], True)


class ScanTest(FakeHomeTest):
    def test_scan_tree(self):
        self.assertEqual(inspector.scan_tree(self.tmp / "missing"), (0, 0, 0))
        f = self.write("d/a.txt", "12345")
        self.assertEqual(inspector.scan_tree(f)[:2], (5, 1))
        self.write("d/sub/b.txt", "123")
        os.symlink(str(self.home / "d" / "sub"), str(self.home / "d" / "linkdir"))
        size, files, newest = inspector.scan_tree(self.home / "d")
        self.assertEqual(files, 3)  # a.txt, b.txt and the link itself (not followed)
        self.assertGreaterEqual(size, 8)
        self.assertGreater(newest, 0)

    def test_scan_tree_skips_vanished_files(self):
        self.write("d/a.txt", "1")
        real_lstat = os.lstat

        def flaky(path, *a, **kw):
            if path.endswith("a.txt"):
                raise OSError("gone")
            return real_lstat(path, *a, **kw)

        with mock.patch("os.lstat", side_effect=flaky):
            self.assertEqual(inspector.scan_tree(self.home / "d")[1], 0)


class InspectTest(FakeHomeTest):
    def test_full_report(self):
        root = self.claude_tree()
        self.sign_in(self.home / ".claude.json")
        old = time.time() - 200 * 86400
        for path in (root / "projects" / "-Users-me-old").rglob("*"):
            os.utime(str(path), (old, old))
        os.utime(str(root / "projects" / "-Users-me-old"), (old, old))
        report = inspector.inspect_profile(profiles.Profile("personal"), platform="linux")

        self.assertTrue(report["exists"])
        keys = [s["key"] for s in report["sections"]]
        self.assertEqual(keys, ["conversations", "customizations", "account", "runtime", "unknown"])
        names = {i["name"] for s in report["sections"] for i in s["items"]}
        self.assertIn("~/.claude.json", names)
        self.assertIn("mystery.bin", names)

        app, old_project = report["projects"]
        self.assertEqual(app["path"], "/Users/me/code/app")
        self.assertTrue(app["path_exact"])
        self.assertEqual(app["memory_files"], 1)
        self.assertEqual(old_project["path"], "/Users/me/old")
        self.assertFalse(old_project["path_exact"])
        self.assertTrue(old_project["inactive"])

        state = report["state"]
        self.assertEqual(state["account"]["email"], "me@example.com")
        self.assertEqual(state["known_projects"], 2)
        self.assertEqual(state["mcp_servers"], ["github"])
        self.assertNotIn("SHOULD-NEVER-BE-SHOWN", json.dumps(report))
        self.assertEqual(report["credentials"], {"where": "none"})

        settings = next(i for s in report["sections"] for i in s["items"] if i["name"] == "settings.json")
        self.assertEqual(settings["details"]["keys"], ["model", "theme"])
        plugins = next(i for s in report["sections"] for i in s["items"] if i["name"] == "plugins")
        self.assertEqual(plugins["details"]["names"], ["fmt@market"])
        hints = " ".join(report["hints"])
        self.assertIn("1 project not used", hints)
        self.assertIn("1 item not recognized", hints)

    def test_missing_directory(self):
        report = inspector.inspect_profile(profiles.Profile("work", str(self.tmp / "nope")), platform="linux")
        self.assertFalse(report["exists"])
        self.assertEqual(report["sections"], [])
        self.assertFalse(report["state"]["exists"])
        self.assertEqual(report["hints"], [])

    def test_credentials_file_and_keychain(self):
        root = self.tmp / "w"
        self.write(".credentials.json", "{}", base=root, mode=0o644)
        p = profiles.Profile("work", str(root))
        report = inspector.inspect_profile(p, platform="wsl")
        self.assertEqual(report["credentials"]["where"], "file")
        self.assertTrue(report["credentials"]["too_open"])
        self.assertIn("chmod 600", report["hints"][0])
        os.remove(str(root / ".credentials.json"))
        report = inspector.inspect_profile(p, platform="macos")
        self.assertEqual(report["credentials"]["where"], "keychain")
        self.assertIsNone(report["credentials"]["present"])
        self.assertIsNotNone(inspector.inspect_profile(p)["platform"])

    def test_details_edge_cases(self):
        root = self.tmp / "w"
        self.write("settings.json", "[1]", base=root)
        self.write("plugins/installed_plugins.json", "[]", base=root)
        self.write("agents/.hidden", "", base=root)
        self.write("agents/reviewer.md", "", base=root)
        self.write("projects/stray.txt", "", base=root)
        os.symlink(str(root / "agents"), str(root / "projects" / "linked"))
        report = inspector.inspect_profile(profiles.Profile("w", str(root)), platform="linux")
        items = {i["name"]: i for s in report["sections"] for i in s["items"]}
        self.assertEqual(items["settings.json"]["details"], {"keys": []})
        self.assertEqual(items["plugins"]["details"], {"names": []})
        self.assertEqual(items["agents"]["details"], {"names": ["reviewer"]})
        self.assertEqual(report["projects"], [])

    def test_shared_link_and_big_cache(self):
        root = self.tmp / "w"
        self.write(".claude/skills/a/SKILL.md", "x")
        root.mkdir()
        os.symlink(str(self.home / ".claude" / "skills"), str(root / "skills"))
        self.write("cache/big", "", base=root)
        with mock.patch.object(inspector, "BIG_CACHE_BYTES", 0):
            report = inspector.inspect_profile(profiles.Profile("w", str(root)), platform="linux")
        skills = next(i for s in report["sections"] for i in s["items"] if i["name"] == "skills")
        self.assertEqual(skills["link_to"], str(self.home / ".claude" / "skills"))
        self.assertFalse(skills["is_dir"])
        self.assertIn("Runtime files and caches", report["hints"][0])

    def test_unreadable_state_file(self):
        self.write(".claude.json", "garbage")
        (self.home / ".claude").mkdir()
        report = inspector.inspect_profile(profiles.Profile("p"), platform="linux")
        self.assertEqual(report["state"]["readable"], False)
        self.assertTrue(report["state"]["exists"])

    def test_state_with_odd_types(self):
        self.write(".claude.json", {"oauthAccount": [], "projects": [], "mcpServers": None})
        summary = inspector.summarize_state(self.home / ".claude.json")
        self.assertEqual((summary["account"], summary["known_projects"], summary["mcp_servers"]), ({}, 0, []))

    def test_project_path_unreadable_transcript(self):
        folder = self.home / "projects" / "-a-b"
        self.write("x.jsonl", "", base=folder)
        with mock.patch("pathlib.Path.open", side_effect=OSError):
            self.assertEqual(inspector._project_path(folder), ("/a/b", False))


class StyleTest(FakeHomeTest):
    def test_for_stream(self):
        self.assertFalse(Style.for_stream(io.StringIO(), {}).color)
        self.assertTrue(Style.for_stream(TTYStringIO(), {}).color)
        self.assertFalse(Style.for_stream(TTYStringIO(), {"NO_COLOR": "1"}).color)
        self.assertFalse(Style.for_stream(TTYStringIO(), {"TERM": "dumb"}).color)
        self.assertFalse(Style.for_stream(io.StringIO(), {}).unicode)
        self.assertTrue(Style.for_stream(TTYStringIO()).unicode)
        self.assertFalse(Style.for_stream(object(), {}).color)

    def test_paint_and_symbols(self):
        plain = Style(color=False, unicode=False)
        self.assertEqual(plain.paint("x", "bold"), "x")
        self.assertEqual(plain.ok("done"), "OK done")
        self.assertEqual(plain.sym("secret"), "[secret]")
        fancy = Style(color=True, unicode=True)
        self.assertEqual(fancy.paint("x", "red", "bold"), "\033[31;1mx\033[0m")
        self.assertEqual(fancy.paint("x"), "x")
        self.assertIn("✗", fancy.fail("no"))
        self.assertIn("!", fancy.warn("hm"))
        self.assertEqual(Style(width=10).width, 60)
        self.assertEqual(Style(width=500).width, 110)

    def test_table(self):
        s = Style()
        out = render.table([["a", ("bb", "BB!")]], ["H1", "Header2"], s)
        self.assertEqual(out.splitlines(), ["H1  Header2", "a   BB!"])


class RenderReportTest(FakeHomeTest):
    def make_report(self, **kw):
        root = self.claude_tree()
        self.sign_in(self.home / ".claude.json")
        self.write(".credentials.json", "{}", base=root, mode=0o600)
        os.symlink(str(root / "skills"), str(root / "agents"))
        return inspector.inspect_profile(profiles.Profile("personal"), platform="macos", **kw)

    def test_render_plain(self):
        report = self.make_report()
        out = render.render_report(report, Style(color=False, unicode=False))
        for expected in ("Claude Code data for profile 'personal'", "me@example.com (Example Org)",
                         "Your conversations", "~/.claude.json", "Settings in use: model, theme",
                         "Installed: fmt@market", "/Users/me/code/app", "(approx.)", "Worth knowing",
                         "Not recognized", "safe to delete", "shared from ~/.claude/skills",
                         "MCP servers: github", "Launches", "(blank)", "Login stored  in ~/.claude/.credentials.json"):
            self.assertIn(expected, out)
        self.assertNotIn("SHOULD-NEVER-BE-SHOWN", out)
        self.assertNotIn("\033[", out)

    def test_render_colored_and_project_limit(self):
        report = self.make_report()
        report["projects"] = report["projects"] * 7
        report["projects"][0]["inactive"] = True
        out = render.render_report(report, Style(color=True, unicode=True))
        self.assertIn("\033[", out)
        self.assertIn("...and 4 more", out)
        self.assertNotIn("...and", render.render_report(report, Style(), show_all=True))

    def test_render_missing_dir(self):
        report = inspector.inspect_profile(profiles.Profile("w", str(self.tmp / "nope")), platform="linux")
        self.assertIn("doesn't exist yet", render.render_report(report, Style()))

    def test_render_without_state_or_hints(self):
        (self.home / ".claude").mkdir()
        self.write(".claude/.last-cleanup", "1")
        report = inspector.inspect_profile(profiles.Profile("p"), platform="linux")
        out = render.render_report(report, Style())
        self.assertIn("not signed in", out)
        self.assertIn("no saved login found", out)
        self.assertNotIn("Worth knowing", out)

    def test_credentials_lines(self):
        s = Style(color=True)
        line = render._credentials_line({"where": "file", "path": "/x", "mode": "0o644", "too_open": True}, s)
        self.assertIn("\033[31m", line)
        for present, word in ((True, "found"), (False, "not found"), (None, "couldn't check")):
            line = render._credentials_line({"where": "keychain", "service": "svc", "present": present}, s)
            self.assertIn("({})".format(word), line)

    def test_render_state_minimal(self):
        out = render._render_state({"path": "/s", "account": {}, "known_projects": 0, "mcp_servers": []}, Style())
        self.assertNotIn("MCP", out)
        self.assertNotIn("Launches", out)

    def test_account_line(self):
        self.assertEqual(render._account_line(None), "not signed in")
        self.assertEqual(render._account_line({"emailAddress": "a@b"}), "a@b")
        self.assertEqual(render._account_line({"email": "a@b", "organization": "Org"}), "a@b (Org)")

    def test_render_profiles(self):
        rows = [
            {"name": "personal", "active": True, "default": True, "account": {"email": "a@b", "organization": None},
             "dir": str(self.home / ".claude"), "size": 2048},
            {"name": "work", "active": False, "default": False, "account": None, "dir": "/w", "size": 0},
        ]
        out = render.render_profiles(rows, Style(color=False, unicode=True))
        lines = out.splitlines()
        self.assertIn("Profile", lines[0])
        self.assertTrue(lines[1].startswith("▸  personal (default)"))
        self.assertIn("not signed in", lines[2])
        self.assertIn("~/.claude", lines[1])
        self.assertIn("\033[", render.render_profiles(rows, Style(color=True)))
