"""Simulated external-drive situations: other file systems, read-only, drive vanishing, coarse timestamps.
Real external drives are NOT available in CI; these tests simulate what can be simulated."""
import errno, hashlib, os, shutil, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import core
from folderecho.core import FolderEchoError


NP = os.path.normpath
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def w(p, d, mtime=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(d if isinstance(d, bytes) else d.encode())
    if mtime is not None: os.utime(p, (mtime, mtime))


def digest(p):
    with open(p, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def tree(root):
    return {os.path.relpath(os.path.join(d, f), root): (digest(os.path.join(d, f)), os.stat(os.path.join(d, f)).st_mtime_ns)
            for d, _, fs in os.walk(root) for f in fs}


class Base(unittest.TestCase):
    def setUp(self):
        self.base = os.path.realpath(tempfile.mkdtemp(prefix="drv", dir=os.environ.get("FOLDERECHO_TEST_TMP"))); self.addCleanup(shutil.rmtree, self.base, True)
        self.a = os.path.join(self.base, "A"); os.makedirs(self.a)


class OtherFileSystem(Base):
    def setUp(self):
        super().setUp()
        shm = "/dev/shm"
        if os.name == "nt" or not os.path.isdir(shm) or not os.access(shm, os.W_OK) or os.stat(shm).st_dev == os.stat(self.base).st_dev:
            self.skipTest("no second file system available here (needs a writable tmpfs at /dev/shm)")
        self.other = tempfile.mkdtemp(prefix="fe-other", dir=shm); self.addCleanup(shutil.rmtree, self.other, True)
        self.b = os.path.join(self.other, "B"); os.makedirs(self.b)
        p = mock.patch.object(core, "SYSTEM_DIRS_POSIX", tuple(x for x in core.SYSTEM_DIRS_POSIX if x != "/dev"))
        p.start(); self.addCleanup(p.stop)
        w(NP(self.a + "/one.bin"), b"data-one" * 50); w(NP(self.a + "/two.bin"), b"data-one" * 50)
        w(NP(self.b + "/three.bin"), b"data-one" * 50); w(NP(self.b + "/only.txt"), b"only b")


    def test_files_really_are_on_different_devices(self):
        self.assertNotEqual(os.stat(self.a).st_dev, os.stat(self.b).st_dev)


    def test_compare_across_file_systems(self):
        r = core.compare(self.a, self.b)
        self.assertEqual(r["only_in_b"], ["only.txt"])
        self.assertEqual(len(r["same_content_elsewhere"]), 2)


    def test_move_each_file_on_its_own_drive_then_undo_all(self):
        before = (tree(self.a), tree(self.b))
        r = core.find_duplicates([self.a, self.b])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        self.assertEqual(len(sel), 2)
        res = core.apply_plan(r, sel)  # no explicit holding: one per drive
        self.assertEqual(res["moved"], 2)
        self.assertEqual(len(res["runs"]), 2)
        for run in res["runs"]:
            self.assertEqual(os.stat(run).st_dev, os.stat(self.a).st_dev if run.startswith(self.base) else os.stat(self.b).st_dev)
        for run in res["runs"]:
            self.assertEqual(core.undo_run(run)["conflicts"], [])
        self.assertEqual(before, (tree(self.a), tree(self.b)))


    def test_explicit_holding_on_other_drive_is_refused_and_nothing_moves(self):
        before = (tree(self.a), tree(self.b))
        r = core.find_duplicates([self.a, self.b])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(before, (tree(self.a), tree(self.b)))


    def test_rename_across_devices_never_falls_back_to_copy_delete(self):
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        before = tree(self.a)
        with mock.patch.object(core.os, "rename", side_effect=OSError(errno.EXDEV, "Invalid cross-device link")):
            res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 0); self.assertEqual(len(res["skipped"]), len(sel))
        self.assertEqual(before, tree(self.a))
        self.assertEqual([f for _, _, fs in os.walk(os.path.join(self.base, "hold")) for f in fs if f not in (core.MARKER_NAME, core.JOURNAL_NAME)], [])


class DriveTrouble(Base):
    def setUp(self):
        super().setUp()
        for i in range(6): w(NP(f"{self.a}/f{i}.bin"), b"same bytes " * 30)
        w(NP(self.a + "/unique.txt"), "u")
        self.hold = os.path.join(self.base, "hold"); self.orig = tree(self.a)
        self.r = core.find_duplicates([self.a])
        self.sel = [x["abs"] for g in self.r["groups"] for x in g["extras"]]


    def test_drive_disappears_during_moves(self):
        real = os.rename; calls = {"n": 0}
        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] > 2: raise OSError(errno.EIO, "Input/output error")
            return real(src, dst)
        with mock.patch.object(core.os, "rename", side_effect=flaky):
            res = core.apply_plan(self.r, self.sel, self.hold)
        self.assertEqual(res["moved"], 2); self.assertEqual(len(res["skipped"]), len(self.sel) - 2)
        self.assertEqual(sum(os.path.exists(s) for s in self.sel), len(self.sel) - 2)
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 2); self.assertEqual(tree(self.a), self.orig)


    def test_drive_unreadable_during_recheck(self):
        with mock.patch.object(core, "sha256_file", side_effect=OSError(errno.ENODEV, "No such device")):
            res = core.apply_plan(self.r, self.sel, self.hold)
        self.assertEqual(res["moved"], 0); self.assertEqual(tree(self.a), self.orig)
        self.assertTrue(all("could not be read" in why for _, why in res["skipped"]))


    def test_drive_unplugged_between_check_and_move(self):
        shutil.move(self.a, self.a + "-gone")
        res = core.apply_plan(self.r, self.sel, self.hold)
        self.assertEqual(res["moved"], 0)
        self.assertTrue(all(why == "file is gone" for _, why in res["skipped"]))


    def test_folder_vanishes_during_scan_is_reported_not_crash(self):
        os.makedirs(NP(self.a + "/sub")); w(NP(self.a + "/sub/x"), "x")
        real = os.scandir
        def bad(p):
            if p.endswith("sub"): raise OSError(errno.EIO, "Input/output error")
            return real(p)
        with mock.patch.object(core.os, "scandir", side_effect=bad):
            s = core.scan_folder(self.a)
        self.assertTrue(s.errors); self.assertIn("f0.bin", s.files)


    @unittest.skipIf(core.IS_WIN or IS_ROOT, "needs POSIX permissions as a normal user")
    def test_read_only_parent_gives_plain_message_and_changes_nothing(self):
        ro = os.path.join(self.base, "ro"); os.makedirs(ro); os.chmod(ro, 0o555)
        try:
            with self.assertRaises(FolderEchoError) as cm:
                core.apply_plan(self.r, self.sel, os.path.join(ro, "hold"))
        finally:
            os.chmod(ro, 0o755)
        self.assertIn("holding folder", str(cm.exception)); self.assertEqual(tree(self.a), self.orig)


    @unittest.skipIf(core.IS_WIN or IS_ROOT, "needs POSIX permissions as a normal user")
    def test_read_only_source_folder_files_not_moved(self):
        os.chmod(self.a, 0o555)
        try:
            res = core.apply_plan(self.r, self.sel, self.hold)
        finally:
            os.chmod(self.a, 0o755)
        self.assertEqual(res["moved"], 0); self.assertEqual(len(res["skipped"]), len(self.sel))
        self.assertEqual(tree(self.a), self.orig)


    @unittest.skipIf(core.IS_WIN or IS_ROOT, "needs POSIX permissions as a normal user")
    def test_undo_when_original_folder_became_read_only(self):
        res = core.apply_plan(self.r, self.sel, self.hold)
        os.chmod(self.a, 0o555)
        try:
            u = core.undo_run(res["run"])
        finally:
            os.chmod(self.a, 0o755)
        self.assertEqual(u["restored"], 0); self.assertEqual(len(u["conflicts"]), len(self.sel))
        self.assertEqual(core.undo_run(res["run"])["restored"], len(self.sel))
        self.assertEqual(tree(self.a), self.orig)


class CoarseTimestamps(Base):
    """FAT keeps modified times to 2 seconds, exFAT to 10 ms, and many copy tools round them."""
    def test_identical_rounded_times_still_deterministic_and_reversible(self):
        t = 1700000000  # even second
        for n in ("b.bin", "a.bin", "c.bin"): w(NP(f"{self.a}/{n}"), b"x" * 100, t)
        r1 = core.find_duplicates([self.a]); r2 = core.find_duplicates([self.a])
        self.assertEqual([g["keep"]["abs"] for g in r1["groups"]], [g["keep"]["abs"] for g in r2["groups"]])
        self.assertEqual(r1["groups"][0]["keep"]["rel"], "a.bin")
        before = tree(self.a); sel = [x["abs"] for g in r1["groups"] for x in g["extras"]]
        res = core.apply_plan(r1, sel, os.path.join(self.base, "hold")); core.undo_run(res["run"])
        self.assertEqual(before, tree(self.a))


    def test_copy_with_rounded_time_is_still_a_duplicate_by_content(self):
        w(NP(self.a + "/orig.bin"), b"z" * 500, 1700000001.7); w(NP(self.a + "/rounded.bin"), b"z" * 500, 1700000000)
        self.assertEqual(core.find_duplicates([self.a])["extra_count"], 1)


    def test_same_size_different_content_never_matched_even_with_equal_times(self):
        w(NP(self.a + "/x.bin"), b"A" * 500, 1700000000); w(NP(self.a + "/y.bin"), b"B" * 500, 1700000000)
        self.assertEqual(core.find_duplicates([self.a])["groups"], [])


class CaseRules(Base):
    def test_case_insensitive_drive_treats_names_as_same(self):
        b = os.path.join(self.base, "B"); os.makedirs(b)
        w(NP(self.a + "/Photo.JPG"), "x"); w(NP(b + "/photo.jpg"), "x")
        with mock.patch.object(core, "fs_case_insensitive", return_value=True):
            r = core.compare(self.a, b)
        self.assertEqual(len(r["same"]), 1); self.assertEqual(r["only_in_a"], []); self.assertEqual(r["only_in_b"], [])


    @unittest.skipIf(core.IS_WIN, "POSIX probe")
    def test_probe_says_case_sensitive_here(self):
        self.assertFalse(core.fs_case_insensitive(self.a))


if __name__ == "__main__":
    unittest.main()

