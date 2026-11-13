import hashlib, json, os, sys, tempfile, time, unittest, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import core
from folderecho.core import FolderEchoError


NP = os.path.normpath


def w(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())
    if mtime:
        os.utime(path, (mtime, mtime))


def _h(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tree(root):
    out = {}
    for d, _, fs in os.walk(root):
        for f in fs:
            p = os.path.join(d, f)
            out[os.path.relpath(p, root)] = (_h(p), os.stat(p).st_mtime_ns)
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fe", dir=os.environ.get("FOLDERECHO_TEST_TMP"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.base = os.path.realpath(self.tmp)
        self.a = os.path.join(self.base, "A"); self.b = os.path.join(self.base, "B")
        os.makedirs(self.a); os.makedirs(self.b)


class CompareTests(Base):
    def test_basic_categories(self):
        w(f"{self.a}/same.txt", "x"); w(f"{self.b}/same.txt", "x")
        w(f"{self.a}/diff.txt", "aaa"); w(f"{self.b}/diff.txt", "bbb")      # same size diff content
        w(f"{self.a}/diff2.txt", "a"); w(f"{self.b}/diff2.txt", "bb")       # diff size
        w(f"{self.a}/onlya.txt", "only a"); w(f"{self.b}/onlyb.txt", "only b!!")
        w(f"{self.a}/old/name.bin", "move me please"); w(f"{self.b}/new/other.bin", "move me please")
        r = core.compare(self.a, self.b)
        self.assertEqual(r["same"], ["same.txt"])
        self.assertEqual(sorted(d[0] for d in r["different"]), ["diff.txt", "diff2.txt"])
        self.assertEqual(r["only_in_a"], ["onlya.txt"])
        self.assertEqual(r["only_in_b"], ["onlyb.txt"])
        self.assertEqual(r["same_content_elsewhere"], [(os.path.join("old", "name.bin"), [os.path.join("new", "other.bin")])])


    def test_same_name_different_content_never_called_same(self):
        w(f"{self.a}/f", "12345"); w(f"{self.b}/f", "12346")
        r = core.compare(self.a, self.b)
        self.assertEqual(r["same"], [])
        self.assertEqual(len(r["different"]), 1)


    def test_empty_files_same_path(self):
        w(f"{self.a}/e", ""); w(f"{self.b}/e", "")
        r = core.compare(self.a, self.b)
        self.assertEqual(r["same"], ["e"])


    def test_empty_files_elsewhere_not_called_moved(self):
        w(f"{self.a}/e1", ""); w(f"{self.b}/e2", "")
        r = core.compare(self.a, self.b)
        self.assertEqual(r["same_content_elsewhere"], [])
        self.assertEqual(r["only_in_a"], ["e1"]); self.assertEqual(r["only_in_b"], ["e2"])


    def test_unicode_and_spaces(self):
        n = "caf\u00e9 \u65e5\u672c \u0639\u0631\u0628\u064a.txt"
        w(f"{self.a}/{n}", "x"); w(f"{self.b}/{n}", "x")
        self.assertEqual(core.compare(self.a, self.b)["same"], [n])


    def test_nested_folders_refused(self):
        os.makedirs(f"{self.a}/inner")
        with self.assertRaises(FolderEchoError):
            core.compare(self.a, NP(f"{self.a}/inner"))
        with self.assertRaises(FolderEchoError):
            core.compare(self.a, self.a)


    def test_compare_changes_nothing(self):
        w(f"{self.a}/x", "1"); w(f"{self.b}/y", "2")
        before = (tree(self.a), tree(self.b))
        core.compare(self.a, self.b)
        self.assertEqual(before, (tree(self.a), tree(self.b)))


class SafetyTests(Base):
    def test_refuse_bad_roots(self):
        for bad in ["", "   ", "relative/path", os.path.join(self.base, "nope"), "/", "/etc", "/usr/share", os.path.expanduser("~"),
                    os.path.join(self.a, "..", "B")]:
            with self.assertRaises(FolderEchoError, msg=bad):
                core.check_folder(bad)


    def test_file_is_not_folder(self):
        w(f"{self.a}/f", "x")
        with self.assertRaises(FolderEchoError):
            core.check_folder(f"{self.a}/f")


    @unittest.skipIf(core.IS_WIN, "symlink creation needs privileges on Windows")
    def test_symlink_root_and_ancestor_refused(self):
        ln = os.path.join(self.base, "ln"); os.symlink(self.a, ln)
        with self.assertRaises(FolderEchoError):
            core.check_folder(ln)
        os.makedirs(f"{self.a}/sub")
        with self.assertRaises(FolderEchoError):
            core.check_folder(os.path.join(ln, "sub"))


    @unittest.skipIf(core.IS_WIN, "symlink creation needs privileges on Windows")
    def test_symlinks_inside_are_skipped_not_followed(self):
        w(f"{self.a}/real", "data!"); os.symlink(f"{self.a}/real", NP(f"{self.a}/link"))
        os.symlink(self.b, NP(f"{self.a}/dirlink"))
        w(f"{self.b}/other", "data!")
        s = core.scan_folder(self.a)
        self.assertEqual(sorted(s.files), ["real"])
        self.assertEqual(sorted(r for r, _ in s.skipped), ["dirlink", "link"])


    def test_synced_names_refused(self):
        for name in ["OneDrive", "OneDrive - Contoso", "Dropbox", "Google Drive", "iCloud Drive"]:
            p = os.path.join(self.base, name, "Docs"); os.makedirs(p)
            with self.assertRaises(FolderEchoError, msg=name):
                core.check_folder(p)


    @unittest.skipIf(core.IS_WIN, "uses POSIX dotfiles")
    def test_hidden_files_skipped_and_counted(self):
        w(f"{self.a}/.hidden", "h"); w(f"{self.a}/v", "v")
        s = core.scan_folder(self.a)
        self.assertEqual(list(s.files), ["v"]); self.assertEqual(s.hidden, 1)


    @unittest.skipIf(core.IS_WIN, "hardlinks tested on POSIX")
    def test_hardlinks_skipped(self):
        w(f"{self.a}/one", "same data"); os.link(f"{self.a}/one", NP(f"{self.a}/two"))
        w(f"{self.a}/three", "same data")
        s = core.scan_folder(self.a)
        self.assertEqual(list(s.files), ["three"])
        self.assertEqual(len(s.skipped), 2)


    @unittest.skipIf(core.IS_WIN, "FIFO is POSIX only")
    def test_fifo_skipped_no_hang(self):
        os.mkfifo(f"{self.a}/pipe"); w(f"{self.a}/f", "x")
        s = core.scan_folder(self.a)
        self.assertEqual(list(s.files), ["f"])
        self.assertTrue(any(r == "pipe" for r, _ in s.skipped))


    @unittest.skipIf(core.IS_WIN or (hasattr(os, "geteuid") and os.geteuid() == 0), "needs POSIX non-root")
    def test_unreadable_file_reported(self):
        w(f"{self.a}/p1", "secret data"); w(f"{self.a}/p2", "secret data")
        os.chmod(f"{self.a}/p1", 0)
        try:
            r = core.find_duplicates([self.a])
        finally:
            os.chmod(f"{self.a}/p1", 0o644)
        self.assertEqual(r["groups"], [])
        self.assertTrue(r["errors"])




class PlatformTests(Base):
    @unittest.skipUnless(core.IS_WIN, "Windows only")
    def test_windows_hidden_and_system_skipped(self):
        import ctypes
        w(NP(f"{self.a}/h.txt"), "h"); w(NP(f"{self.a}/s.txt"), "s"); w(NP(f"{self.a}/v.txt"), "v")
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(NP(f"{self.a}/h.txt"), 0x2))
        self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(NP(f"{self.a}/s.txt"), 0x4))
        sc = core.scan_folder(self.a)
        self.assertEqual([e["rel"] for e in sc.files.values()], ["v.txt"]); self.assertEqual(sc.hidden, 2)


    @unittest.skipUnless(core.IS_WIN, "Windows only")
    def test_windows_names_compare_case_insensitively(self):
        w(NP(f"{self.a}/File.TXT"), "x"); w(NP(f"{self.b}/file.txt"), "x")
        r = core.compare(self.a, self.b)
        self.assertEqual(len(r["same"]), 1); self.assertEqual(r["only_in_a"], [])


    @unittest.skipUnless(core.IS_WIN, "Windows only")
    def test_windows_junction_refused(self):
        import subprocess
        j = os.path.join(self.base, "junc")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", j, self.a], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        with self.assertRaises(FolderEchoError):
            core.check_folder(j)
        w(NP(f"{self.a}/data"), "d")
        os.makedirs(NP(f"{self.b}/inner"))
        subprocess.run(["cmd", "/c", "mklink", "/J", NP(f"{self.b}/inner/jj"), self.a], capture_output=True, check=True)
        sc = core.scan_folder(self.b)
        self.assertEqual(sc.files, {}); self.assertTrue(any(why == "link or shortcut" for _, why in sc.skipped))


    @unittest.skipUnless(core.IS_WIN, "Windows only")
    def test_windows_long_path_move_and_undo(self):
        deep = os.path.join(self.a, *["d" * 40] * 8)  # well over 260 characters
        data = b"long path data" * 20
        w(NP(os.path.join(deep, "one.bin")), data); w(NP(os.path.join(self.a, "short.bin")), data)
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        self.assertEqual(len(sel), 1)
        res = core.apply_plan(r, sel, os.path.join(self.base, "hold"))
        self.assertEqual(res["moved"], 1)
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 1)


    @unittest.skipIf(core.IS_WIN, "POSIX case rules")
    def test_posix_names_are_case_sensitive(self):
        w(NP(f"{self.a}/File.TXT"), "x"); w(NP(f"{self.b}/file.txt"), "x")
        r = core.compare(self.a, self.b)
        self.assertEqual(r["same"], []); self.assertEqual(len(r["same_content_elsewhere"]), 1)


class DupeTests(Base):
    def make(self):
        t = time.time() - 1000
        w(f"{self.a}/photo.jpg", "PHOTO-DATA", t)
        w(f"{self.a}/copy of photo.jpg", "PHOTO-DATA", t + 5)
        w(f"{self.b}/backup/photo.jpg", "PHOTO-DATA", t + 9)
        w(f"{self.a}/unique.txt", "unique content")
        w(f"{self.a}/samesize1", "AAAA"); w(f"{self.a}/samesize2", "BBBB")
        w(f"{self.a}/e1", ""); w(f"{self.a}/e2", "")


    def test_groups_by_content_not_name(self):
        self.make()
        r = core.find_duplicates([self.a, self.b])
        self.assertEqual(len(r["groups"]), 1)
        g = r["groups"][0]
        self.assertEqual(len(g["extras"]), 2)
        self.assertEqual(r["empty_files"], 2)
        allp = [g["keep"]["abs"]] + [x["abs"] for x in g["extras"]]
        self.assertNotIn(f"{self.a}/unique.txt", allp)


    def test_master_never_extra(self):
        self.make()
        w(f"{self.b}/dupe-inside-master-too.jpg", "PHOTO-DATA")
        r = core.find_duplicates([self.a, self.b], master=self.b)
        for g in r["groups"]:
            self.assertTrue(g["keep"]["abs"].startswith(self.b))
            for x in g["extras"]:
                self.assertTrue(x["abs"].startswith(self.a), x["abs"])
        self.assertEqual(r["extra_count"], 2)


    def test_master_only_duplicates_untouched(self):
        w(f"{self.b}/m1", "dup"); w(f"{self.b}/m2", "dup"); w(f"{self.a}/x", "other")
        r = core.find_duplicates([self.a, self.b], master=self.b)
        self.assertEqual(r["groups"], [])


    def test_master_must_be_a_root(self):
        self.make()
        other = os.path.join(self.base, "C"); os.makedirs(other)
        with self.assertRaises(FolderEchoError):
            core.find_duplicates([self.a, self.b], master=other)


    def test_scan_changes_nothing(self):
        self.make(); before = (tree(self.a), tree(self.b))
        core.find_duplicates([self.a, self.b])
        self.assertEqual(before, (tree(self.a), tree(self.b)))


class ApplyUndoTests(Base):
    def setUp(self):
        super().setUp()
        t = time.time() - 5000
        w(f"{self.a}/keep.bin", b"\x00\x01" * 100, t)
        w(f"{self.a}/sub/dup1.bin", b"\x00\x01" * 100, t + 1)
        w(f"{self.a}/dup2.bin", b"\x00\x01" * 100, t + 2)
        w(f"{self.a}/other.txt", "other", t)
        self.hold = os.path.join(self.base, "hold")
        self.orig = tree(self.a)


    def plan(self):
        r = core.find_duplicates([self.a])
        self.assertEqual(r["extra_count"], 2)
        return r, [x["abs"] for g in r["groups"] for x in g["extras"]]


    def test_move_then_undo_restores_bytes_and_mtimes(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        self.assertEqual(res["moved"], 2)
        self.assertEqual(len(tree(self.a)), 2)  # keep + other
        self.assertTrue(os.path.exists(os.path.join(self.hold, core.MARKER_NAME)))
        for s in sel:
            self.assertFalse(os.path.exists(s))
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 2); self.assertEqual(u["conflicts"], [])
        self.assertEqual(tree(self.a), self.orig)
        self.assertTrue(os.path.isdir(self.hold))  # nothing deleted


    def test_never_deletes_anything_on_move(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        total = sum(len(f) for _, _, f in os.walk(self.base))
        self.assertEqual(total, 4 + 2)  # 4 files + marker + journal
        moved = [os.path.join(d, f) for d, _, fs in os.walk(res["run"]) for f in fs if f != core.JOURNAL_NAME]
        self.assertEqual(len(moved), 2)


    def test_file_edited_after_preview_is_not_moved(self):
        r, sel = self.plan()
        w(sel[0], b"changed after preview!!")
        res = core.apply_plan(r, sel, self.hold)
        self.assertEqual(res["moved"], 1)
        self.assertEqual(len(res["skipped"]), 1)
        self.assertEqual(open(sel[0], "rb").read(), b"changed after preview!!")


    def test_same_size_edit_with_restored_mtime_caught_by_hash(self):
        r, sel = self.plan()
        x = [e for g in r["groups"] for e in g["extras"] if e["abs"] == sel[0]][0]
        w(sel[0], b"\x09\x09" * 100, x["mtime_ns"] / 1e9)
        os.utime(sel[0], ns=(x["mtime_ns"], x["mtime_ns"]))
        res = core.apply_plan(r, sel, self.hold)
        self.assertEqual(res["moved"], 1)
        self.assertEqual(open(sel[0], "rb").read(), b"\x09\x09" * 100)


    def test_kept_copy_gone_or_changed_blocks_move(self):
        r, sel = self.plan()
        keep = r["groups"][0]["keep"]["abs"]
        os.remove(keep)
        res = core.apply_plan(r, sel, self.hold)
        self.assertEqual(res["moved"], 0)
        self.assertEqual(len(res["skipped"]), 2)
        for s in sel:
            self.assertTrue(os.path.exists(s))


    def test_unselected_extras_untouched(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel[:1], self.hold)
        self.assertEqual(res["moved"], 1)
        self.assertTrue(os.path.exists(sel[1]))


    def test_selection_outside_preview_refused(self):
        r, sel = self.plan()
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, [NP(f"{self.a}/other.txt")], self.hold)
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, [], self.hold)
        self.assertEqual(tree(self.a), self.orig)


    def test_holding_inside_scanned_folder_refused(self):
        r, sel = self.plan()
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, os.path.join(self.a, "hold"))
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, os.path.join(self.base, "x", "..", "hold"))
        self.assertEqual(tree(self.a), self.orig)


    def test_undo_does_not_overwrite_new_file(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        w(sel[0], "someone made a new file here")
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 1); self.assertEqual(len(u["conflicts"]), 1)
        self.assertEqual(open(sel[0]).read(), "someone made a new file here")


    def test_undo_refuses_edited_holding_file(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        held = [os.path.join(d, f) for d, _, fs in os.walk(res["run"]) for f in fs if f not in (core.JOURNAL_NAME,)][0]
        with open(held, "ab") as f:
            f.write(b"edited")
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 1); self.assertEqual(len(u["conflicts"]), 1)
        self.assertTrue(os.path.exists(held))


    def test_undo_recreates_removed_parent_folder(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        shutil.rmtree(f"{self.a}/sub")
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 2)
        self.assertEqual(tree(self.a), self.orig)


    def test_undo_twice_is_harmless(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        core.undo_run(res["run"])
        u2 = core.undo_run(res["run"])
        self.assertEqual(u2["restored"], 0)
        self.assertEqual(tree(self.a), self.orig)


    def test_damaged_and_foreign_journal_refused(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        jp = os.path.join(res["run"], core.JOURNAL_NAME)
        good = open(jp).read()
        open(jp, "w").write("not json\n")
        with self.assertRaises(FolderEchoError):
            core.undo_run(res["run"])
        open(jp, "w").write(good)
        copy = os.path.join(self.hold, "run-copied"); shutil.copytree(res["run"], copy)
        with self.assertRaises(FolderEchoError):
            core.undo_run(copy)


    def test_journal_entry_escaping_run_folder_ignored(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        jp = os.path.join(res["run"], core.JOURNAL_NAME)
        lines = open(jp).read().split("\n")
        head = json.loads(lines[0]); victim = os.path.join(self.base, "victim.txt"); w(victim, "precious")
        head["entries"][0]["dest"] = victim; head["entries"][0]["sha256"] = hashlib.sha256(b"precious").hexdigest(); head["entries"][0]["size"] = 8
        lines[0] = json.dumps(head); open(jp, "w").write("\n".join(lines))
        u = core.undo_run(res["run"])
        self.assertTrue(os.path.exists(victim))
        self.assertTrue(any("outside" in why for _, why in u["conflicts"]))


    def test_journal_src_outside_cleaned_folders_ignored(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        jp = os.path.join(res["run"], core.JOURNAL_NAME)
        lines = open(jp).read().split("\n")
        head = json.loads(lines[0]); target = os.path.join(self.base, "elsewhere", "stolen.bin")
        head["entries"][0]["src"] = target; lines[0] = json.dumps(head); open(jp, "w").write("\n".join(lines))
        u = core.undo_run(res["run"])
        self.assertFalse(os.path.exists(target))
        self.assertTrue(any("outside the cleaned" in why for _, why in u["conflicts"]))


    def test_holding_in_unwritable_or_file_place_gives_plain_error(self):
        r, sel = self.plan()
        blocker = os.path.join(self.base, "blocker"); w(blocker, "i am a file")
        with self.assertRaises(FolderEchoError):
            core.apply_plan(r, sel, blocker)
        self.assertEqual(tree(self.a), self.orig)


    def test_crash_after_rename_before_journal_mark(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold)
        jp = os.path.join(res["run"], core.JOURNAL_NAME)
        lines = open(jp).read().split("\n")
        open(jp, "w").write(lines[0] + "\n")  # drop 'moved' marks
        u = core.undo_run(res["run"])
        self.assertEqual(u["restored"], 2)
        self.assertEqual(tree(self.a), self.orig)


    def test_unicode_long_names(self):
        n = "\u65e5\u672c\u8a9e " + "x" * 120 + ".bin"
        w(f"{self.a}/{n}", b"\x00\x01" * 100)
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        res = core.apply_plan(r, sel, self.hold)
        core.undo_run(res["run"])
        self.assertEqual(tree(self.a), {**self.orig, n: tree(self.a)[n]})


    def test_many_files(self):
        for i in range(600):
            w(f"{self.a}/many/f{i%300}_{i}.txt", f"content {i%300}")
        r = core.find_duplicates([self.a])
        sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
        self.assertGreaterEqual(len(sel), 300)
        res = core.apply_plan(r, sel, self.hold)
        self.assertEqual(res["moved"], len(sel))
        core.undo_run(res["run"])
        self.assertEqual(tree(self.a), {**self.orig, **{k: v for k, v in tree(self.a).items() if k.startswith("many")}})
        self.assertEqual(sum(len(f) for _, _, f in os.walk(self.a)), 4 + 600)


    def test_cancel_stops_before_moving(self):
        r, sel = self.plan()
        res = core.apply_plan(r, sel, self.hold, cancel=lambda: True)
        self.assertEqual(res["moved"], 0)
        self.assertEqual(tree(self.a), self.orig)


if __name__ == "__main__":
    unittest.main()

