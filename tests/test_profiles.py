import json
import os

from helpers import FakeHomeTest
from umbrella import paths, profiles
from umbrella.errors import UmbrellaError


class ProfileTest(FakeHomeTest):
    def test_default_dir_profile(self):
        p = profiles.Profile("personal")
        self.assertTrue(p.uses_default_dir)
        self.assertEqual(p.config_dir, self.home / ".claude")
        self.assertEqual(p.state_file, self.home / ".claude.json")
        self.assertEqual(p.env(), {"CLAUDE_CONFIG_DIR": None, "UMBRELLA_PROFILE": "personal"})

    def test_managed_profile(self):
        p = profiles.Profile("work", "/x/work", "2026")
        self.assertFalse(p.uses_default_dir)
        self.assertEqual(str(p.state_file), "/x/work/.claude.json")
        self.assertEqual(p.env()["CLAUDE_CONFIG_DIR"], "/x/work")
        self.assertEqual(p.to_dict(), {"path": "/x/work", "created": "2026"})

    def test_account(self):
        p = profiles.Profile("personal")
        self.assertIsNone(p.account())  # missing file
        self.write(".claude.json", "not json")
        self.assertIsNone(p.account())
        self.write(".claude.json", [1, 2])
        self.assertIsNone(p.account())
        self.write(".claude.json", {"oauthAccount": "weird"})
        self.assertIsNone(p.account())
        self.write(".claude.json", {"oauthAccount": {"emailAddress": ""}})
        self.assertIsNone(p.account())
        self.sign_in(self.home / ".claude.json", "a@b.c")
        self.assertEqual(p.account()["emailAddress"], "a@b.c")

    def test_validate_name(self):
        for good in ("work", "a", "my-work_2"):
            profiles.validate_name(good)
        for bad in ("", None, "Work", "-x", "a b", "x" * 33, "../x"):
            with self.assertRaises(UmbrellaError) as cm:
                profiles.validate_name(bad)
            self.assertIn("lowercase", cm.exception.hint)


class RegistryTest(FakeHomeTest):
    def test_new_registry_is_empty(self):
        reg = profiles.Registry()
        self.assertFalse(reg.exists)
        self.assertEqual(reg.names(), [])
        with self.assertRaises(UmbrellaError):
            reg.require_initialized()

    def test_add_save_load(self):
        reg = profiles.Registry()
        reg.add("personal")
        reg.add("work", self.tmp / "w")
        self.assertEqual(reg.default, "personal")
        reg.save()
        reg.require_initialized()
        self.assertEqual(oct(os.stat(str(reg.file)).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(str(reg.file.parent)).st_mode & 0o777), "0o700")

        again = profiles.Registry(reg.file)
        self.assertEqual(again.names(), ["personal", "work"])
        self.assertEqual(again.get("work").path, str(self.tmp / "w"))
        self.assertIsNone(again.get("personal").path)
        self.assertEqual(again.default, "personal")
        self.assertEqual(again.default_dir_profile().name, "personal")

    def test_add_errors(self):
        reg = profiles.Registry()
        reg.add("personal")
        with self.assertRaises(UmbrellaError):
            reg.add("personal", "/x")
        with self.assertRaises(UmbrellaError) as cm:
            reg.add("other")
        self.assertIn("personal", str(cm.exception))
        with self.assertRaises(UmbrellaError):
            reg.add("Bad")

    def test_get_unknown(self):
        reg = profiles.Registry()
        with self.assertRaises(UmbrellaError) as cm:
            reg.get("work")
        self.assertIn("none yet", cm.exception.hint)
        reg.add("personal")
        with self.assertRaises(UmbrellaError) as cm:
            reg.get("work")
        self.assertIn("personal", cm.exception.hint)

    def test_remove_and_default(self):
        reg = profiles.Registry()
        reg.add("personal")
        reg.add("work", "/w")
        reg.set_default("work")
        self.assertEqual(reg.default, "work")
        reg.remove("personal")
        self.assertEqual(reg.default, "work")
        reg.remove("work")
        self.assertIsNone(reg.default)
        self.assertIsNone(reg.default_dir_profile())

    def test_damaged_registry(self):
        for content in ("nope", "{}", '{"profiles": []}', '{"profiles": {"a": 1}}'):
            self.write(".umbrella/profiles.json", content)
            with self.assertRaises(UmbrellaError) as cm:
                profiles.Registry()
            self.assertIn("damaged", str(cm.exception))

    def test_current_and_find_by_dir(self):
        reg = profiles.Registry()
        self.assertIsNone(reg.current({}))
        reg.add("personal")
        work_dir = self.tmp / "w"
        work_dir.mkdir()
        reg.add("work", work_dir)
        self.assertEqual(reg.current({}).name, "personal")
        self.assertEqual(reg.current({"CLAUDE_CONFIG_DIR": str(work_dir) + "/"}).name, "work")
        self.assertIsNone(reg.current({"CLAUDE_CONFIG_DIR": "/elsewhere"}))
        os.environ["CLAUDE_CONFIG_DIR"] = str(work_dir)
        self.assertEqual(reg.current().name, "work")
        self.assertEqual(reg.find_by_dir(str(self.home / ".claude")).name, "personal")


class ProfileDirTest(FakeHomeTest):
    def test_create_profile_dir(self):
        target = profiles.create_profile_dir("work")
        self.assertEqual(target, paths.profiles_dir() / "work")
        self.assertEqual(oct(target.stat().st_mode & 0o777), "0o700")
        self.assertEqual(profiles.create_profile_dir("work"), target)  # empty dir is reusable
        self.write("x", "1", base=target)
        with self.assertRaises(UmbrellaError):
            profiles.create_profile_dir("work")

    def test_parse_share(self):
        self.assertEqual(profiles.parse_share(None), [])
        self.assertEqual(profiles.parse_share("skills, settings,"), ["skills", "settings"])
        self.assertEqual(profiles.parse_share("all"), sorted(profiles.SHAREABLE))
        with self.assertRaises(UmbrellaError) as cm:
            profiles.parse_share("skills,history")
        self.assertIn("history", str(cm.exception))

    def test_share_items(self):
        self.write(".claude/settings.json", "{}")
        self.write(".claude/skills/a/SKILL.md", "x")
        target = self.tmp / "w"
        target.mkdir()
        (target / "agents").mkdir()
        self.write(".claude/agents/a.md", "x")
        p = profiles.Profile("work", str(target))
        linked, missing = profiles.share_items(p, ["settings", "skills", "commands", "agents"])
        self.assertEqual(linked, ["settings.json", "skills"])
        self.assertEqual(missing, ["commands", "agents"])
        self.assertTrue((target / "skills").is_symlink())
        # Running again doesn't clobber the links.
        self.assertEqual(profiles.share_items(p, ["skills"]), ([], ["skills"]))
        self.assertEqual(json.loads((target / "settings.json").read_text()), {})
