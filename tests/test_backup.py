import hashlib
import io
import json
import os
import tarfile
from unittest import mock

from helpers import FakeHomeTest
from umbrella import backup, paths, profiles
from umbrella.errors import UmbrellaError


def digest_tree(root):
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            out[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


class BackupTest(FakeHomeTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / "work"
        self.claude_tree(self.root)
        self.write(".credentials.json", '{"token":"secret"}', base=self.root, mode=0o600)
        self.write(".claude.json", {"oauthAccount": {"emailAddress": "w@x"}}, base=self.root)
        os.symlink(str(self.root / "skills"), str(self.root / "shared-skills"))
        os.mkfifo(str(self.root / "a-fifo"))
        self.profile = profiles.Profile("work", str(self.root))

    def test_default_backup_path(self):
        path = backup.default_backup_path(self.profile, now=0)
        self.assertEqual(path.parent, paths.backups_dir())
        self.assertRegex(path.name, r"^umbrella-work-\d{8}-\d{6}\.tar\.gz$")
        path.parent.mkdir(parents=True)
        path.write_text("")
        second = backup.default_backup_path(self.profile, now=0)
        self.assertTrue(second.name.endswith("-2.tar.gz"))
        second.write_text("")
        self.assertTrue(backup.default_backup_path(self.profile, now=0).name.endswith("-3.tar.gz"))

    def test_round_trip_without_secrets(self):
        out = self.tmp / "b.tar.gz"
        result = backup.create_backup(self.profile, out, platform="linux")
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
        self.assertEqual(result.skipped_secrets, [".credentials.json"])
        self.assertEqual(result.unreadable, [])
        self.assertGreater(result.size, 0)

        info = backup.read_archive(out)
        self.assertEqual(info.manifest["profile"], "work")
        self.assertIn("config/settings.json", info.files)
        self.assertNotIn("config/.credentials.json", info.files)
        self.assertEqual(info.links, [("config/shared-skills", str(self.root / "skills"))])

        target_root = self.tmp / "restored"
        target = profiles.Profile("copy", str(target_root))
        restored, links, outside = backup.restore_backup(out, target)
        self.assertEqual(restored, len(info.files))
        self.assertEqual(outside, [])
        expected = digest_tree(self.root)
        expected.pop(".credentials.json")
        self.assertEqual(digest_tree(target_root), expected)
        self.assertFalse((target_root / "shared-skills").exists())

    def test_include_secrets(self):
        out = self.tmp / "b.tar.gz"
        result = backup.create_backup(self.profile, out, include_secrets=True)
        self.assertEqual(result.skipped_secrets, [])
        self.assertIn("config/.credentials.json", backup.read_archive(out).files)

    def test_default_profile_includes_home_state_file(self):
        self.claude_tree()
        self.sign_in(self.home / ".claude.json")
        out = self.tmp / "d.tar.gz"
        backup.create_backup(profiles.Profile("personal"), out)
        info = backup.read_archive(out)
        self.assertIn("state/.claude.json", info.files)
        # Restoring it writes ~/.claude.json back.
        os.remove(str(self.home / ".claude.json"))
        backup.restore_backup(out, profiles.Profile("personal"))
        self.assertIn("me@example.com", (self.home / ".claude.json").read_text())

    def test_backup_of_missing_profile(self):
        out = self.tmp / "e.tar.gz"
        result = backup.create_backup(profiles.Profile("x", str(self.tmp / "none")), out)
        self.assertEqual(result.files, 0)

    def test_refuses_to_overwrite(self):
        out = self.tmp / "b.tar.gz"
        out.write_text("")
        with self.assertRaises(UmbrellaError):
            backup.create_backup(self.profile, out)

    def test_unreadable_file_is_reported(self):
        real_add = tarfile.TarFile.add

        def add(tar, name, *a, **kw):
            if name.endswith("mystery.bin"):
                raise PermissionError("nope")
            return real_add(tar, name, *a, **kw)

        with mock.patch.object(tarfile.TarFile, "add", add):
            result = backup.create_backup(self.profile, self.tmp / "u.tar.gz")
        self.assertEqual(result.unreadable, ["config/mystery.bin"])

    def test_restore_replaces_symlink_and_skips_outside(self):
        out = self.tmp / "b.tar.gz"
        backup.create_backup(self.profile, out)
        target_root = self.tmp / "t"
        target_root.mkdir()
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        os.symlink(str(elsewhere), str(target_root / "skills"))           # dir shared from elsewhere
        inside = target_root / "real-settings.json"
        inside.write_text("{}")
        os.symlink(str(inside), str(target_root / "settings.json"))       # link that stays inside
        restored, links, outside = backup.restore_backup(out, profiles.Profile("t", str(target_root)))
        self.assertEqual(outside, ["config/skills/pdf/SKILL.md"])
        self.assertEqual(list(elsewhere.iterdir()), [])
        self.assertFalse((target_root / "settings.json").is_symlink())
        self.assertEqual(json.loads((target_root / "settings.json").read_text()), {"model": "opus", "theme": "dark"})


class ArchiveValidationTest(FakeHomeTest):
    def make(self, members, manifest=True):
        path = self.tmp / "evil.tar.gz"
        with tarfile.open(str(path), "w:gz") as tar:
            if manifest is not None:
                data = json.dumps({"tool": "umbrella"}).encode() if manifest is True else manifest
                info = tarfile.TarInfo(backup.MANIFEST)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            for info in members:
                tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
        return path

    def member(self, name, kind=tarfile.REGTYPE, size=1, link=""):
        info = tarfile.TarInfo(name)
        info.type, info.size, info.linkname = kind, size if kind == tarfile.REGTYPE else 0, link
        return info

    def assert_rejected(self, path, text):
        with self.assertRaises(UmbrellaError) as cm:
            backup.read_archive(path)
        self.assertIn(text, str(cm.exception))

    def test_rejects_unsafe_paths(self):
        for name in ("/etc/passwd", "config/../../x", "config\\x"):
            self.assert_rejected(self.make([self.member(name)]), "unsafe path")

    def test_rejects_unexpected_entries(self):
        self.assert_rejected(self.make([self.member("other/x")]), "unexpected entry")

    def test_rejects_special_files(self):
        self.assert_rejected(self.make([self.member("config/dev", tarfile.CHRTYPE)]), "special file")

    def test_requires_manifest(self):
        self.assert_rejected(self.make([self.member("config/a")], manifest=None), "isn't an Umbrella backup")
        self.assert_rejected(self.make([], manifest=b"not json"), "isn't an Umbrella backup")
        self.assert_rejected(self.make([], manifest=b'{"tool": "other"}'), "isn't an Umbrella backup")

    def test_not_a_tarball(self):
        bad = self.write("bad.tar.gz", "hello")
        self.assert_rejected(bad, "Couldn't open")
        self.assert_rejected(self.tmp / "missing.tar.gz", "Couldn't open")

    def test_hard_links_and_dirs(self):
        path = self.make([self.member("config/d", tarfile.DIRTYPE), self.member("config/h", tarfile.LNKTYPE, link="x")])
        info = backup.read_archive(path)
        self.assertEqual(info.files, [])
        self.assertEqual(info.links, [("config/h", "x")])
