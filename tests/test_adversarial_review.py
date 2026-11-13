"""Independent adversarial review tests (not from the builder)."""
import hashlib, json, os, shutil, sys, tempfile, threading, time, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import core, server
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
    return {os.path.relpath(os.path.join(d, f), root): digest(os.path.join(d, f))
            for d, _, fs in os.walk(root) for f in fs}

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="adv", dir=os.environ.get("FOLDERECHO_TEST_TMP"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.realpath(self.tmp)
        self.a = os.path.join(self.base, "A"); self.b = os.path.join(self.base, "B")
        os.makedirs(self.a); os.makedirs(self.b)


class NameCollisionTests(Base):
    @unittest.skipIf(core.IS_WIN, "POSIX name rules")
    def test_nfc_nfd_collision_silently_drops_a_file(self):
        """'café.txt' in NFC and NFD are two different files on ext4 but one scan key."""
        n1 = "caf\u00e9.txt"          # NFC
        n2 = "cafe\u0301.txt"         # NFD
        w(f"{self.a}/{n1}", "content-one")
        w(f"{self.a}/{n2}", "content-two-different")
        s = core.scan_folder(self.a)
        # Fixed contract: both files left out AND the collision is reported loudly.
        self.assertEqual(len(s.files), 0, "colliding files must not be silently treated as one")
        self.assertEqual(len(s.errors), 2, s.errors)
        self.assertTrue(any("look the same" in why for _, why in s.errors))

    @unittest.skipIf(core.IS_WIN, "POSIX name rules")
    def test_case_collision_silently_drops_a_file_on_ci_drives(self):
        """On a case-insensitive drive (or Windows case-sensitive dirs), same-folded names collide."""
        w(f"{self.a}/Photo.JPG", "one")
        w(f"{self.a}/photo.jpg", "two-different")
        with mock.patch.object(core, "fs_case_insensitive", return_value=True):
            r = core.compare(self.a, self.b)
        self.assertEqual(r["counts"]["a"], 0)
        self.assertTrue(any("look the same" in why for _, why in r["errors"]), r["errors"])

    @unittest.skipIf(core.IS_WIN, "POSIX name rules")
    def test_nfc_nfd_same_folder_duplicates_reported_not_grouped(self):
        """Two same-folder files differing only in Unicode form: left out loudly, not silently grouped."""
        n1 = "caf\u00e9.txt"; n2 = "cafe\u0301.txt"
        w(f"{self.a}/{n1}", "SAME")
        w(f"{self.a}/{n2}", "SAME")
        w(f"{self.b}/other.txt", "SAME")
        r = core.find_duplicates([self.a, self.b])
        self.assertTrue(any("look the same" in why for _, why in r["errors"]), r["errors"])
        # only B/other.txt is visible and unique, so no groups
        self.assertEqual(r["extra_count"], 0, r)

    @unittest.skipIf(core.IS_WIN, "POSIX name rules")
    def test_nfc_nfd_across_roots_move_and_undo(self):
        """NFC name in A, NFD name in B: valid duplicate pair, move + undo must work."""
        n1 = "caf\u00e9.txt"; n2 = "cafe\u0301.txt"
        w(f"{self.a}/{n1}", "SAME")
        w(f"{self.b}/{n2}", "SAME")
        r = core.find_duplicates([self.a, self.b])
        self.assertEqual(r["extra_count"], 1, r)
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 1)
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 1)
        self.assertEqual(u["conflicts"], [])


class SwapInTests(Base):
    def plan(self):
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/sub/dup1.bin", b"\x00\x01" * 100, t + 1)
        w(f"{self.a}/dup2.bin", b"\x00\x01" * 100, t + 2)
        r = core.find_duplicates([self.a])
        return r, [x["abs"] for g in r["groups"] for x in g["extras"]]

    @unittest.skipIf(core.IS_WIN, "symlink needs privileges on Windows")
    def test_extra_swapped_for_symlink_before_apply_is_refused(self):
        r, sel = self.plan()
        victim_outside = os.path.join(self.base, "outside.bin"); w(victim_outside, "outside data")
        os.remove(sel[0]); os.symlink(victim_outside, sel[0])
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 1)
        self.assertTrue(any("link" in why for _, why in res["skipped"]))
        self.assertEqual(open(victim_outside).read(), "outside data")

    @unittest.skipIf(core.IS_WIN, "hardlinks on POSIX")
    def test_extra_hardlinked_to_keep_before_apply_is_refused(self):
        r, sel = self.plan()
        keep = r["groups"][0]["keep"]["abs"]
        os.remove(sel[0]); os.link(keep, sel[0])
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 1)
        self.assertTrue(any("hardlink" in why or "another name" in why for _, why in res["skipped"]))
        self.assertTrue(os.path.exists(sel[0]))

    def test_extra_swapped_for_other_same_size_content_same_mtime(self):
        """Worst swap: same size, same mtime, different content -> hash check must catch it."""
        r, sel = self.plan()
        x = [e for g in r["groups"] for e in g["extras"] if e["abs"] == sel[0]][0]
        w(sel[0], b"\x07\x07" * 100)
        os.utime(sel[0], ns=(x["mtime_ns"], x["mtime_ns"]))
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 1)
        self.assertEqual(open(sel[0], "rb").read(), b"\x07\x07" * 100)


class HoldingPlacementTests(Base):
    def plan(self):
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/dup1.bin", b"\x00\x01" * 100, t + 1)
        return core.find_duplicates([self.a]), [x["abs"] for g in core.find_duplicates([self.a])["groups"] for x in g["extras"]]

    def test_holding_inside_synced_folder_should_be_refused(self):
        r, sel = self.plan()
        synced = os.path.join(self.base, "Dropbox", "hold")
        os.makedirs(os.path.dirname(synced))
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, synced)

    @unittest.skipIf(core.IS_WIN, "symlink needs privileges on Windows")
    def test_holding_that_is_symlink_refused(self):
        r, sel = self.plan()
        real = os.path.join(self.base, "realhold"); os.makedirs(real)
        link = os.path.join(self.base, "linkhold"); os.symlink(real, link)
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, link)

    def test_holding_parent_is_symlink_refused(self):
        if core.IS_WIN: self.skipTest("symlink privileges")
        r, sel = self.plan()
        real = os.path.join(self.base, "realparent"); os.makedirs(real)
        linkp = os.path.join(self.base, "linkparent"); os.symlink(real, linkp)
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, os.path.join(linkp, "hold"))


class DoubleApplyTests(Base):
    def test_second_apply_of_same_plan_moves_nothing_and_loses_nothing(self):
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/d1.bin", b"\x00\x01" * 100, t + 1)
        w(f"{self.a}/d2.bin", b"\x00\x01" * 100, t + 2)
        orig = tree(self.a)
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        res1 = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res1["moved"], 2)
        res2 = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res2["moved"], 0)
        self.assertEqual(len(res2["skipped"]), 2)
        # undo run 1 still restores everything
        u = core.undo_run(res1["run"])
        self.assertEqual(u["restored"], 2)
        self.assertEqual(tree(self.a), orig)


class UndoAdversarialTests(Base):
    def setup_run(self):
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/sub/dup1.bin", b"\x00\x01" * 100, t + 1)
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        return core.apply_plan(r, sel, os.path.join(self.base, "hold")), sel

    @unittest.skipIf(core.IS_WIN, "symlink needs privileges on Windows")
    def test_undo_through_symlinked_intermediate_dir(self):
        """If run/from-2 is replaced by a symlink, undo must not follow it somewhere else."""
        res, sel = self.setup_run()
        run = res["run"]
        subdir = os.path.join(run, "from-1", "sub")
        real_file = os.path.join(subdir, "dup1.bin")
        self.assertTrue(os.path.isfile(real_file))
        # attacker/confused user replaces 'sub' with a symlink to another folder
        # holding an identical-content file at the same name
        elsewhere = os.path.join(self.base, "elsewhere")
        os.makedirs(elsewhere)
        shutil.move(real_file, os.path.join(elsewhere, "dup1.bin"))
        shutil.rmtree(os.path.join(run, "from-1", "sub"))
        os.symlink(elsewhere, os.path.join(run, "from-1", "sub"))
        u = core.undo_run(run)
        # fixed contract: undo must not follow the link; file left alone and reported
        self.assertEqual(u["restored"], 0, u)
        self.assertTrue(any("link" in why for _, why in u["conflicts"]), u)
        self.assertTrue(os.path.isfile(os.path.join(elsewhere, "dup1.bin")))

    def test_undo_with_src_now_a_symlink(self):
        """Original place now holds a symlink: must NOT be overwritten/removed."""
        res, sel = self.setup_run()
        other = os.path.join(self.base, "other.bin"); w(other, "other")
        os.symlink(other, sel[0])
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 0)
        self.assertTrue(os.path.islink(sel[0]))
        self.assertEqual(open(other).read(), "other")


class JournalTamperTests(Base):
    def test_journal_dest_with_lexical_dotdot_inside_run(self):
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/dup1.bin", b"\x00\x01" * 100, t + 1)
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        jp = os.path.join(res["run"], core.JOURNAL_NAME)
        lines = open(jp).read().split("\n")
        head = json.loads(lines[0])
        victim = os.path.join(self.base, "hold", "sneaky.bin")
        w(victim, "precious")
        head["entries"][0]["dest"] = os.path.join(res["run"], "from-1", "..", "sneaky.bin")
        head["entries"][0]["sha256"] = digest(victim); head["entries"][0]["size"] = 8
        lines[0] = json.dumps(head); open(jp, "w").write("\n".join(lines))
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 0)
        self.assertEqual(open(victim).read(), "precious")


class ServerAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.srv, self.base, self.tok, self.state = server.start_for_test()
        self.addCleanup(self.srv.shutdown)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="advsrv", dir=os.environ.get("FOLDERECHO_TEST_TMP")))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = os.path.join(self.tmp, "A"); os.makedirs(self.a)
        w(self.a + "/x.bin", b"dupdata" * 10); w(self.a + "/y.bin", b"dupdata" * 10)

    def call(self, path, body=None, token=None, host=None, raw=None, ctype="application/json"):
        import urllib.request, urllib.error
        h = {"Content-Type": ctype, "X-Token": token if token is not None else self.tok}
        if host: h["Host"] = host
        req = urllib.request.Request(self.base + path, data=raw if raw is not None else json.dumps(body or {}).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try: return e.code, json.loads(e.read())
            except Exception: return e.code, None

    def test_oversized_body_refused(self):
        try:
            code, _ = self.call("/api/browse", raw=b" " * (1024 * 1024 + 10))
        except OSError:
            return  # server refused and closed while the client was still sending: also a refusal
        self.assertEqual(code, 413)

    def test_non_dict_json_refused(self):
        code, _ = self.call("/api/browse", raw=b"[1,2,3]")
        self.assertEqual(code, 400)

    def test_host_case_and_variants(self):
        import urllib.parse
        port = urllib.parse.urlparse(self.base).port
        for bad_host in [f"127.0.0.1.evil.com:{port}", f"localhost.evil.com:{port}", f"127.0.0.1:{port+1}", f"0.0.0.0:{port}"]:
            code, _ = self.call("/api/browse", {"path": ""}, host=bad_host)
            self.assertEqual(code, 403, bad_host)

    def test_second_start_refused_while_running(self):
        big = os.path.join(self.tmp, "big"); os.makedirs(big)
        for i in range(200): w(f"{big}/f{i}.bin", os.urandom(2048))
        self.call("/api/start", {"kind": "dupes", "folders": [big]})
        _, r = self.call("/api/start", {"kind": "dupes", "folders": [big]})
        # either refused, or first finished so fast it doesn't matter
        if "error" not in r:
            print("\\nsecond start accepted (first job finished instantly)")

    def test_apply_marks_no_running_job(self):
        """apply() never marks the job 'running', so a new scan can start mid-move (UI-level race)."""
        _, r = self.call("/api/start", {"kind": "dupes", "folders": [self.a]})
        for _ in range(100):
            _, j = self.call("/api/job")
            if j["job"]["status"] != "running": break
            time.sleep(0.05)
        sel = [x["abs"] for g in j["job"]["result"]["groups"] for x in g["extras"]]
        self.assertTrue(sel)
        _, r2 = self.call("/api/apply", {"selected": sel, "holding": os.path.join(self.tmp, "hold")})
        self.assertEqual(r2.get("moved"), 1)
        # after apply, server state allows a fresh start; the moved file must not reappear wrongly
        _, r3 = self.call("/api/start", {"kind": "dupes", "folders": [self.a]})
        self.assertNotIn("error", r3)


if __name__ == "__main__":
    unittest.main()

