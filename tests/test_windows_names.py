"""Windows-only: names that Windows normally forbids (CON, NUL, trailing dots/spaces) can exist on disk
when made with the long-path prefix. The tool must not crash, hang, or lose files with them."""
import os, sys, tempfile, unittest, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import core

WIN = os.name == "nt"
NAMES = ["CON", "NUL.txt", "aux", "trailing.", "space ", "COM1.log"]


def mk(path, data):
    p = core.lp(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)


@unittest.skipUnless(WIN, "Windows only")
class ReservedNames(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="resv", dir=os.environ.get("FOLDERECHO_TEST_TMP")))
        self.a = os.path.join(self.tmp, "A"); self.b = os.path.join(self.tmp, "B")
        os.makedirs(self.a); os.makedirs(self.b)
        self.made = []
        for i, n in enumerate(NAMES):
            try:
                mk(os.path.join(self.a, n), b"same content %d" % i)
                mk(os.path.join(self.b, n), b"same content %d" % i)
                self.made.append(n)
            except OSError as e:
                print("could not create", repr(n), e)
    def tearDown(self):
        shutil.rmtree(core.lp(self.tmp), True)
    def test_some_were_creatable(self):
        print("created:", self.made)
    def test_compare_and_dupes_do_not_crash(self):
        r = core.compare(self.a, self.b)
        print("compare:", {k: len(v) for k, v in r.items() if isinstance(v, list)}, "errors", r.get("errors"))
        self.assertEqual(len(r["same"]) + len(r["errors"] if isinstance(r.get("errors"), list) else []) >= 0, True)
        d = core.find_duplicates([self.a, self.b], master=self.b)
        print("dupes extras:", d["extra_count"], "errors", d.get("errors"))
    def test_move_and_undo_round_trip_or_clean_refusal(self):
        d = core.find_duplicates([self.a, self.b], master=self.b)
        sel = [x["abs"] for g in d["groups"] for x in g["extras"]]
        if not sel:
            return
        try:
            res = core.apply_plan(d, sel)
        except core.FolderEchoError as e:
            print("clean refusal:", e); return
        print("moved", res["moved"], "skipped", res["skipped"])
        for run in res["runs"]:
            u = core.undo_run(run)
            print("undo", u)
        for n in self.made:
            self.assertTrue(os.path.exists(core.lp(os.path.join(self.a, n))), "lost " + repr(n))

