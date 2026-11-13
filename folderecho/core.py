"""Core engine. Python standard library only.


Safety rules (see README):
- Files are matched by size, then by full SHA-256 of the content. Never by name or sampling alone.
- Nothing is ever deleted. "Moving duplicates" renames them into a holding folder on the same drive.
- Links, reparse points, hardlinks, hidden/system files and unreadable files are skipped and reported.
- Every file is re-checked (and re-hashed) right before it is moved.
- Undo only puts back files whose content is unchanged and never overwrites anything.
"""
import hashlib
import json
import os
import sys
import time
import unicodedata


IS_WIN = os.name == "nt"
FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
CHUNK = 1024 * 1024
MAX_FILES = 300000
JOURNAL_NAME = "journal.jsonl"
MARKER_NAME = "FOLDERECHO-HOLDING.txt"




class FolderEchoError(Exception):
    """A refusal or problem that is shown to the person in plain words."""




class Cancelled(Exception):
    pass




# ---------- path helpers ----------


def lp(path):
    """Long-path safe form on Windows; unchanged elsewhere."""
    if not IS_WIN:
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p




def has_traversal(path):
    parts = path.replace("\\", "/").split("/")
    return ".." in parts




def key_of(rel):
    """Comparison key for a relative path. Windows is case-insensitive."""
    rel = unicodedata.normalize("NFC", rel)
    return rel.casefold() if IS_WIN else rel




def fs_case_insensitive(root):
    """True if the folder's file system ignores letter case (Windows, FAT/exFAT, many external drives).
    Read-only probe: asks the system whether the folder name with swapped case is the same folder."""
    if IS_WIN:
        return True
    base = os.path.basename(root.rstrip("/\\"))
    alt = base.swapcase()
    if not base or alt == base:
        return False
    try:
        return os.path.samefile(root, os.path.join(os.path.dirname(root), alt))
    except OSError:
        return False




def is_link_stat(st):
    import stat as _s
    if _s.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)




def _is_hidden(name, st):
    if IS_WIN:
        return bool(getattr(st, "st_file_attributes", 0) & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM))
    return name.startswith(".")




def sha256_file(path, cancel=None):
    h = hashlib.sha256()
    with open(lp(path), "rb") as f:
        while True:
            if cancel is not None and cancel():
                raise Cancelled()
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()




# ---------- folder safety ----------


def _env_paths(names):
    out = []
    for n in names:
        v = os.environ.get(n)
        if v:
            out.append(os.path.normcase(os.path.abspath(v)))
    return out




SYSTEM_DIRS_POSIX = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib32", "/lib64", "/proc", "/root", "/run",
                     "/sbin", "/sys", "/usr", "/var", "/snap", "/opt")


SYNC_NAMES = ("onedrive", "dropbox", "google drive", "googledrive", "icloud drive", "iclouddrive",
              "icloud", "box", "box sync", "mega", "megasync", "pcloud drive", "sync.com", "nextcloud",
              "owncloud", "yandex.disk", "tresorit")




def _inside(child, parent):
    c = os.path.normcase(os.path.abspath(child))
    p = os.path.normcase(os.path.abspath(parent))
    if c == p:
        return True
    return c.startswith(p.rstrip("\\/") + os.sep)




def check_folder(path, label="Folder"):
    """Return absolute path of a folder that is safe to scan and move files out of, or raise FolderEchoError."""
    if not path or not str(path).strip():
        raise FolderEchoError(f"{label}: no folder chosen.")
    path = str(path).strip().strip('"')
    if has_traversal(path):
        raise FolderEchoError(f"{label}: paths containing '..' are not accepted. Choose the folder directly.")
    if not os.path.isabs(path):
        raise FolderEchoError(f"{label}: please give the full folder path (for example C:\\Users\\you\\Documents\\Photos).")
    ap = os.path.abspath(path)
    # drive root / filesystem root
    if os.path.dirname(ap) == ap:
        raise FolderEchoError(f"{label}: a whole drive is not allowed. Choose a folder inside it.")
    # every part, including ancestors, must exist and not be a link
    parts = []
    cur = ap
    while True:
        parts.append(cur)
        up = os.path.dirname(cur)
        if up == cur:
            break
        cur = up
    for p in reversed(parts[:-1]):  # skip the root itself
        try:
            st = os.lstat(lp(p))
        except OSError:
            raise FolderEchoError(f"{label}: '{p}' cannot be read or does not exist.")
        if is_link_stat(st):
            raise FolderEchoError(f"{label}: '{p}' is a shortcut/link. Links are refused so files are never moved somewhere unexpected. Choose the real folder.")
    import stat as _s
    if not _s.S_ISDIR(os.lstat(lp(ap)).st_mode):
        raise FolderEchoError(f"{label}: '{ap}' is not a folder.")
    # system places
    if IS_WIN:
        bad = _env_paths(["SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData"])
        for b in bad:
            if _inside(ap, b):
                raise FolderEchoError(f"{label}: system and program folders are not allowed.")
        home = os.environ.get("USERPROFILE")
        if home and os.path.normcase(os.path.abspath(home)) == os.path.normcase(ap):
            raise FolderEchoError(f"{label}: choose a folder inside your user folder, not the user folder itself.")
        if home and _inside(ap, os.path.join(home, "AppData")):
            raise FolderEchoError(f"{label}: AppData is not allowed.")
    else:
        for b in SYSTEM_DIRS_POSIX:
            if _inside(ap, b):
                raise FolderEchoError(f"{label}: system folders are not allowed.")
        if os.path.normcase(ap) in ("/home", "/mnt", "/media", "/tmp"):
            raise FolderEchoError(f"{label}: choose a folder inside it, not '{ap}' itself.")
        home = os.path.expanduser("~")
        if os.path.normcase(os.path.abspath(home)) == os.path.normcase(ap):
            raise FolderEchoError(f"{label}: choose a folder inside your home folder, not the home folder itself.")
    # cloud-synced folders
    low = [x.lower() for x in ap.replace("\\", "/").split("/") if x]
    for comp in low:
        if comp in SYNC_NAMES or comp.startswith("onedrive") or comp.startswith("dropbox"):
            raise FolderEchoError(f"{label}: this looks like a cloud-synced folder (OneDrive, Dropbox, Google Drive, iCloud and similar). "
                            "Moving files out of one can also remove the cloud copies, so synced folders are not supported. "
                            "Copy the files to an ordinary local folder first.")
    for b in _env_paths(["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]):
        if _inside(ap, b):
            raise FolderEchoError(f"{label}: this is inside your OneDrive folder. Synced folders are not supported.")
    return ap




def check_roots(paths):
    """Check several folders: all valid, none the same as or inside another."""
    aps = [check_folder(p, f"Folder {i+1}") for i, p in enumerate(paths)]
    for i in range(len(aps)):
        for j in range(len(aps)):
            if i != j and _inside(aps[i], aps[j]):
                raise FolderEchoError("One chosen folder is the same as, or inside, another chosen folder. Choose separate folders.")
    return aps




# ---------- scanning ----------


class Scan:
    def __init__(self, root):
        self.root = root
        self.files = {}      # key -> dict(rel, abs, size, mtime_ns)
        self.skipped = []    # (rel, reason)
        self.errors = []
        self.hidden = 0
        self.collided = set()  # keys dropped because two real files mapped to them




def scan_folder(root, exclude=(), progress=None, cancel=None, max_files=MAX_FILES, keyfn=None):
    keyfn = keyfn or key_of
    """Walk root without following links. Returns Scan."""
    sc = Scan(root)
    stack = [root]
    count = 0
    ex = [os.path.normcase(os.path.abspath(e)) for e in exclude]
    while stack:
        d = stack.pop()
        if cancel is not None and cancel():
            raise Cancelled()
        try:
            it = list(os.scandir(lp(d)))
        except OSError as e:
            sc.errors.append((os.path.relpath(d, root), f"cannot open folder ({e.strerror or e})"))
            continue
        for ent in sorted(it, key=lambda e: e.name):
            ap = os.path.join(d, ent.name)
            rel = os.path.relpath(ap, root)
            if os.path.normcase(ap) in ex:
                continue
            try:
                st = ent.stat(follow_symlinks=False)
            except OSError as e:
                sc.errors.append((rel, f"cannot read ({e.strerror or e})"))
                continue
            if is_link_stat(st):
                sc.skipped.append((rel, "link or shortcut"))
                continue
            if _is_hidden(ent.name, st):
                sc.hidden += 1
                continue
            import stat as _s
            if _s.S_ISDIR(st.st_mode):
                stack.append(ap)
                continue
            if not _s.S_ISREG(st.st_mode):
                sc.skipped.append((rel, "not a regular file"))
                continue
            if st.st_nlink > 1:
                sc.skipped.append((rel, "hardlinked file (another name points to the same data)"))
                continue
            count += 1
            if count > max_files:
                raise FolderEchoError(f"More than {max_files:,} files. Choose a smaller folder.")
            k = keyfn(rel)
            if k in sc.collided:
                sc.errors.append((rel, "two files have names that look the same on this drive (letter case or Unicode form); both left out"))
                continue
            if k in sc.files:
                first = sc.files.pop(k)
                sc.collided.add(k)
                sc.errors.append((first["rel"], "two files have names that look the same on this drive (letter case or Unicode form); both left out"))
                sc.errors.append((rel, "two files have names that look the same on this drive (letter case or Unicode form); both left out"))
                continue
            sc.files[k] = {"rel": rel, "abs": ap, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            if progress and count % 200 == 0:
                progress("scan", count, None)
    if progress:
        progress("scan", count, None)
    return sc




def _hash_entry(e, cancel=None):
    """Hash a scanned file, refusing if it changed while reading. Returns hex or None."""
    try:
        before = os.lstat(lp(e["abs"]))
        if before.st_size != e["size"] or before.st_mtime_ns != e["mtime_ns"] or is_link_stat(before):
            return None
        h = sha256_file(e["abs"], cancel)
        after = os.lstat(lp(e["abs"]))
        if after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
            return None
        return h
    except Cancelled:
        raise
    except OSError:
        return None




def hash_entries(entries, progress=None, cancel=None, notes=None):
    done = 0
    total = len(entries)
    for e in entries:
        if "sha" not in e:
            e["sha"] = _hash_entry(e, cancel)
            if e["sha"] is None and notes is not None:
                notes.append((e["rel"], "unreadable or changed while reading; left out"))
        done += 1
        if progress and (done % 25 == 0 or done == total):
            progress("hash", done, total)




# ---------- compare ----------


def compare(root_a, root_b, progress=None, cancel=None):
    a, b = check_roots([root_a, root_b])
    ci = fs_case_insensitive(a) or fs_case_insensitive(b)
    kf = (lambda r: unicodedata.normalize("NFC", r).casefold()) if ci else key_of
    sa = scan_folder(a, progress=progress, cancel=cancel, keyfn=kf)
    sb = scan_folder(b, progress=progress, cancel=cancel, keyfn=kf)
    notes = []
    both = [k for k in sa.files if k in sb.files]
    # same path in both: need hash only when sizes match
    need = []
    for k in both:
        if sa.files[k]["size"] == sb.files[k]["size"]:
            need += [sa.files[k], sb.files[k]]
    only_a = [k for k in sa.files if k not in sb.files]
    only_b = [k for k in sb.files if k not in sa.files]
    # renamed/moved: same content under a different path
    sizes_b = {}
    for k in only_b:
        sizes_b.setdefault(sb.files[k]["size"], []).append(k)
    sizes_a = {}
    for k in only_a:
        sizes_a.setdefault(sa.files[k]["size"], []).append(k)
    for sz, ks in sizes_a.items():
        if sz in sizes_b and sz > 0:
            need += [sa.files[k] for k in ks] + [sb.files[k] for k in sizes_b[sz]]
    hash_entries(need, progress, cancel, notes)
    same, different = [], []
    for k in both:
        ea, eb = sa.files[k], sb.files[k]
        if ea["size"] != eb["size"]:
            different.append((ea["rel"], eb["rel"], "different size"))
        elif ea.get("sha") is None or eb.get("sha") is None:
            continue
        elif ea["sha"] == eb["sha"]:
            same.append(ea["rel"])
        else:
            different.append((ea["rel"], eb["rel"], "same size, different content"))
    byhash_b = {}
    for k in only_b:
        e = sb.files[k]
        if e.get("sha"):
            byhash_b.setdefault(e["sha"], []).append(e)
    moved = []
    moved_a, moved_b = set(), set()
    for k in only_a:
        e = sa.files[k]
        if e.get("sha") and e["sha"] in byhash_b:
            m = byhash_b[e["sha"]]
            moved.append((e["rel"], [x["rel"] for x in m]))
            moved_a.add(k)
            moved_b.update(kf(x["rel"]) for x in m)
    only_a_list = sorted(sa.files[k]["rel"] for k in only_a if k not in moved_a and not _unhashed_skip(sa.files[k]))
    only_b_list = sorted(sb.files[k]["rel"] for k in only_b if k not in moved_b and not _unhashed_skip(sb.files[k]))
    return {
        "mode": "compare", "folder_a": a, "folder_b": b,
        "only_in_a": only_a_list, "only_in_b": only_b_list,
        "same": sorted(same), "different": sorted(different),
        "same_content_elsewhere": sorted(moved),
        "counts": {"a": len(sa.files), "b": len(sb.files)},
        "skipped": [("A: " + r, why) for r, why in sa.skipped] + [("B: " + r, why) for r, why in sb.skipped],
        "errors": [("A: " + r, why) for r, why in sa.errors] + [("B: " + r, why) for r, why in sb.errors] + notes,
        "hidden_skipped": sa.hidden + sb.hidden,
    }




def _unhashed_skip(e):
    return "sha" in e and e["sha"] is None




# ---------- duplicates ----------


def _keeper_rank(e):
    return (len(e["rel"]), e["mtime_ns"], e["rel"].lower(), e["abs"])




def find_duplicates(roots, master=None, progress=None, cancel=None):
    """Find exact duplicates across one or more folders.


    master: optional folder (must be one of roots) whose files are never touched.
    Returns a plan dict. Nothing is changed on disk.
    """
    aps = check_roots(roots)
    mroot = None
    if master:
        m = check_folder(master, "Master folder")
        if os.path.normcase(m) not in [os.path.normcase(x) for x in aps]:
            raise FolderEchoError("The master folder must be one of the folders being checked.")
        mroot = os.path.normcase(m)
    scans = []
    for i, r in enumerate(aps):
        s = scan_folder(r, progress=progress, cancel=cancel)
        for e in s.files.values():
            e["root_index"] = i
            e["is_master"] = mroot is not None and os.path.normcase(r) == mroot
        scans.append(s)
    allf = [e for s in scans for e in s.files.values()]
    empty = [e for e in allf if e["size"] == 0]
    bysize = {}
    for e in allf:
        if e["size"] > 0:
            bysize.setdefault(e["size"], []).append(e)
    cand = [e for g in bysize.values() if len(g) > 1 for e in g]
    notes = []
    hash_entries(cand, progress, cancel, notes)
    byhash = {}
    for e in cand:
        if e.get("sha"):
            byhash.setdefault(e["sha"], []).append(e)
    groups = []
    for sha, g in byhash.items():
        if len(g) < 2:
            continue
        if mroot is not None:
            masters = sorted([e for e in g if e["is_master"]], key=_keeper_rank)
            others = [e for e in g if not e["is_master"]]
            if not masters or not others:
                continue
            keep, extras = masters[0], sorted(others, key=_keeper_rank)
        else:
            g2 = sorted(g, key=_keeper_rank)
            keep, extras = g2[0], g2[1:]
        groups.append({"sha256": sha, "size": g[0]["size"],
                       "keep": _pub(keep), "extras": [_pub(x) for x in extras]})
    groups.sort(key=lambda x: (-x["size"] * len(x["extras"]), x["keep"]["rel"]))
    reclaim = sum(g["size"] * len(g["extras"]) for g in groups)
    return {
        "mode": "duplicates", "roots": aps, "master": (aps[[os.path.normcase(x) for x in aps].index(mroot)] if mroot else None),
        "groups": groups, "reclaimable_bytes": reclaim,
        "extra_count": sum(len(g["extras"]) for g in groups),
        "file_count": len(allf), "empty_files": len(empty),
        "skipped": [(f"{s.root}: {r}", why) for s in scans for r, why in s.skipped],
        "errors": [(f"{s.root}: {r}", why) for s in scans for r, why in s.errors] + notes,
        "hidden_skipped": sum(s.hidden for s in scans),
    }




def _pub(e):
    return {"abs": e["abs"], "rel": e["rel"], "size": e["size"], "mtime_ns": e["mtime_ns"],
            "sha256": e["sha"], "root_index": e["root_index"]}




# ---------- holding folder, apply, undo ----------


def default_holding(root):
    return os.path.join(os.path.dirname(os.path.abspath(root)), "FolderEcho holding")




def _same_device(a, b):
    try:
        return os.lstat(lp(a)).st_dev == os.lstat(lp(b)).st_dev
    except OSError:
        return False




def _fsync_write(path, text, mode="x"):
    with open(lp(path), mode, encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())




def apply_plan(plan, selected, holding_parent=None, progress=None, cancel=None, now=None):
    """Move the selected extra files into new run folders inside holding folders.


    selected: list of absolute paths of extras (must appear in plan).
    With no holding folder given, each chosen folder gets its own 'FolderEcho holding' next to it,
    so files on different drives are each moved on their own drive. Returns result dict with 'runs'.
    """
    roots = plan["roots"]
    extras0 = {}
    for g in plan["groups"]:
        for x in g["extras"]:
            extras0[os.path.normcase(x["abs"])] = (g, x)
    for s_ in selected:
        if os.path.normcase(s_) not in extras0:
            raise FolderEchoError("A selected file is not part of the preview. Run the check again.")
    if not selected:
        raise FolderEchoError("Nothing selected to move.")
    if holding_parent:
        r = _apply_group(plan, selected, holding_parent, progress, cancel, now)
        return {"runs": [r["run"]], "run": r["run"], "moved": r["moved"], "skipped": r["skipped"], "bytes": r["bytes"]}
    by_root = {}
    for s_ in selected:
        by_root.setdefault(extras0[os.path.normcase(s_)][1]["root_index"], []).append(s_)
    runs, skipped, moved, nbytes = [], [], 0, 0
    first_err = None
    for idx in sorted(by_root):
        try:
            r = _apply_group(plan, by_root[idx], None, progress, cancel, now, default_for=roots[idx])
        except FolderEchoError as ex:
            first_err = first_err or ex
            skipped += [(f, f"not moved: {ex}") for f in by_root[idx]]
            continue
        runs.append(r["run"]); skipped += r["skipped"]; moved += r["moved"]; nbytes += r["bytes"]
    if not runs and first_err:
        raise first_err
    return {"runs": runs, "run": runs[0] if runs else None, "moved": moved, "skipped": skipped, "bytes": nbytes}




def _apply_group(plan, selected, holding_parent, progress, cancel, now, default_for=None):
    roots = plan["roots"]
    extras = {}
    for g in plan["groups"]:
        for x in g["extras"]:
            extras[os.path.normcase(x["abs"])] = (g, x)
    chosen = []
    for s in selected:
        k = os.path.normcase(s)
        if k not in extras:
            raise FolderEchoError("A selected file is not part of the preview. Run the check again.")
        chosen.append(extras[k])
    if not chosen:
        raise FolderEchoError("Nothing selected to move.")
    hp = holding_parent or default_holding(default_for or roots[0])
    if has_traversal(hp) or not os.path.isabs(hp):
        raise FolderEchoError("The holding folder must be a full path without '..'.")
    hp = os.path.abspath(hp)
    for r in roots:
        if _inside(hp, r):
            raise FolderEchoError("The holding folder cannot be inside a folder being cleaned. Choose another place on the same drive.")
    low = [x.lower() for x in hp.replace("\\", "/").split("/") if x]
    for comp in low:
        if comp in SYNC_NAMES or comp.startswith("onedrive") or comp.startswith("dropbox"):
            raise FolderEchoError("The holding folder looks like it is inside a cloud-synced folder (OneDrive, Dropbox, Google Drive, iCloud and similar). "
                            "Files moved there would be uploaded. Choose an ordinary local folder on the same drive.")
    for b in _env_paths(["OneDrive", "OneDriveConsumer", "OneDriveCommercial"]):
        if _inside(hp, b):
            raise FolderEchoError("The holding folder is inside your OneDrive folder. Files moved there would be uploaded. Choose an ordinary local folder on the same drive.")
    _cur = hp
    while True:
        _up = os.path.dirname(_cur)
        if _up == _cur:
            break
        if os.path.lexists(lp(_cur)) and is_link_stat(os.lstat(lp(_cur))):
            raise FolderEchoError("The holding folder path goes through a shortcut/link. Choose the real folder.")
        _cur = _up
    parent = os.path.dirname(hp)
    if os.path.lexists(lp(hp)):
        st = os.lstat(lp(hp))
        if is_link_stat(st):
            raise FolderEchoError("The holding folder is a link. Refused.")
    if not os.path.isdir(lp(parent)):
        raise FolderEchoError("The place for the holding folder does not exist.")
    pst = os.lstat(lp(parent))
    if is_link_stat(pst):
        raise FolderEchoError("The holding folder's parent is a link. Refused.")
    anchor = hp
    while not os.path.lexists(lp(anchor)):
        up = os.path.dirname(anchor)
        if up == anchor:
            break
        anchor = up
    try:
        anchor_dev = os.lstat(lp(anchor)).st_dev
    except OSError:
        raise FolderEchoError("The place for the holding folder cannot be read. Is the drive still connected?")
    for _g, x in chosen:
        try:
            src_dev = os.lstat(lp(os.path.dirname(x["abs"]))).st_dev
        except OSError:
            continue  # folder unreadable or gone: reported per file below
        if src_dev != anchor_dev:
            raise FolderEchoError("The holding folder must be on the same drive as the files, so nothing is copied or lost. Choose a place on the same drive.")
    try:
        os.makedirs(lp(hp), exist_ok=True)
        marker = os.path.join(hp, MARKER_NAME)
        if not os.path.exists(lp(marker)):
            _fsync_write(marker, "FolderEcho holding folder.\nFiles moved here are NOT deleted. Use Undo in FolderEcho to put them back.\nDo not delete this folder until you are sure you do not need the files.\n")
    except OSError as ex:
        raise FolderEchoError(f"Could not create the holding folder ({ex.strerror or ex}). Choose another place on the same drive.")
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now or time.time()))
    run = os.path.join(hp, "run-" + stamp)
    n = 1
    while os.path.exists(lp(run)):
        n += 1
        run = os.path.join(hp, f"run-{stamp}-{n}")
    try:
        os.makedirs(lp(run))
    except OSError as ex:
        raise FolderEchoError(f"Could not create the holding folder ({ex.strerror or ex}). Choose another place on the same drive.")
    journal = os.path.join(run, JOURNAL_NAME)
    entries = []
    for i, (g, x) in enumerate(chosen):
        dest = os.path.join(run, f"from-{x['root_index']+1}", x["rel"])
        entries.append({"src": x["abs"], "dest": dest, "size": x["size"], "mtime_ns": x["mtime_ns"],
                        "sha256": x["sha256"], "keep": g["keep"]["abs"], "keep_mtime_ns": g["keep"]["mtime_ns"],
                        "keep_size": g["keep"]["size"]})
    _fsync_write(journal, json.dumps({"type": "plan", "version": 1, "run": run, "roots": roots, "count": len(entries), "entries": entries}) + "\n")
    moved, skipped = [], []
    total = len(entries)
    jf = open(lp(journal), "a", encoding="utf-8", newline="\n")
    try:
        _move_all(entries, moved, skipped, jf, total, progress, cancel)
    finally:
        jf.flush()
        try:
            os.fsync(jf.fileno())
        except OSError:
            pass
        jf.close()
    return {"run": run, "moved": len(moved), "skipped": skipped, "bytes": sum(m["size"] for m in moved)}




def _move_all(entries, moved, skipped, jf, total, progress, cancel):
    for i, e in enumerate(entries):
        if cancel is not None and cancel():
            skipped.append((e["src"], "stopped before this file"))
            continue
        why = _recheck(e, cancel)
        if why:
            skipped.append((e["src"], why))
        else:
            try:
                os.makedirs(lp(os.path.dirname(e["dest"])), exist_ok=True)
                if os.path.lexists(lp(e["dest"])):
                    raise FolderEchoError("holding destination already exists")
                os.rename(lp(e["src"]), lp(e["dest"]))
                moved.append(e)
                jf.write(json.dumps({"type": "moved", "src": e["src"]}) + "\n")
                if len(moved) % 50 == 0:
                    jf.flush()
            except (OSError, FolderEchoError) as ex:
                skipped.append((e["src"], f"could not move ({getattr(ex, 'strerror', None) or ex})"))
        if progress:
            progress("move", i + 1, total)




def _recheck(e, cancel=None):
    """Return None if the file may be moved, else a plain-words reason."""
    try:
        st = os.lstat(lp(e["src"]))
    except OSError:
        return "file is gone"
    if is_link_stat(st):
        return "became a link"
    import stat as _s
    if not _s.S_ISREG(st.st_mode):
        return "no longer a regular file"
    if st.st_nlink > 1:
        return "has another name (hardlink)"
    if st.st_size != e["size"] or st.st_mtime_ns != e["mtime_ns"]:
        return "changed since the check"
    try:
        kst = os.lstat(lp(e["keep"]))
    except OSError:
        return "the copy that is kept is gone"
    if is_link_stat(kst) or kst.st_size != e["keep_size"] or kst.st_mtime_ns != e["keep_mtime_ns"]:
        return "the copy that is kept changed"
    try:
        if sha256_file(e["src"], cancel) != e["sha256"]:
            return "content changed since the check"
        if sha256_file(e["keep"], cancel) != e["sha256"]:
            return "the copy that is kept no longer matches"
        st2 = os.lstat(lp(e["src"]))
        if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns:
            return "changed while checking"
    except OSError:
        return "could not be read"
    return None




def read_journal(run):
    jp = os.path.join(run, JOURNAL_NAME)
    if not os.path.isfile(lp(jp)):
        raise FolderEchoError("No journal found in that run folder.")
    with open(lp(jp), "r", encoding="utf-8") as f:
        lines = [ln for ln in f.read().split("\n") if ln.strip()]
    try:
        head = json.loads(lines[0])
        moved = {json.loads(l)["src"] for l in lines[1:] if json.loads(l).get("type") == "moved"}
    except (ValueError, KeyError, IndexError):
        raise FolderEchoError("The journal is damaged. Nothing was restored.")
    if head.get("type") != "plan":
        raise FolderEchoError("The journal is damaged. Nothing was restored.")
    return head, moved




def list_runs(holding_parent):
    out = []
    if not os.path.isdir(lp(holding_parent)):
        return out
    for n in sorted(os.listdir(lp(holding_parent))):
        r = os.path.join(holding_parent, n)
        if n.startswith("run-") and os.path.isfile(lp(os.path.join(r, JOURNAL_NAME))):
            out.append(r)
    return out




def undo_run(run, progress=None, cancel=None):
    """Put files back. Never overwrites. Only restores files whose content is unchanged."""
    run = os.path.abspath(run)
    if not os.path.isdir(lp(run)) or is_link_stat(os.lstat(lp(run))):
        raise FolderEchoError("That is not a FolderEcho run folder.")
    run_real = os.path.normcase(os.path.realpath(run))
    head, moved = read_journal(run)
    if os.path.normcase(os.path.abspath(head.get("run", ""))) != os.path.normcase(run):
        raise FolderEchoError("This journal belongs to a different folder (it was copied or moved). Nothing was restored.")
    restored, conflicts = [], []
    ents = head["entries"]
    run_n = os.path.normcase(run)
    for i, e in enumerate(ents):
        if cancel is not None and cancel():
            break
        dest, src = e["dest"], e["src"]
        if not os.path.normcase(os.path.abspath(dest)).startswith(run_n + os.sep):
            conflicts.append((src, "journal entry points outside the run folder; ignored"))
            continue
        if os.path.lexists(lp(dest)) and not os.path.normcase(os.path.realpath(dest)).startswith(run_real + os.sep):
            conflicts.append((src, "a link inside the holding folder points somewhere else; left alone"))
            continue
        if head.get("roots") and not any(_inside(src, r) and os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(r)) for r in head["roots"]):
            conflicts.append((src, "journal entry points outside the cleaned folders; ignored"))
            continue
        present = os.path.lexists(lp(dest))
        if not present:
            if e["src"] in moved:
                conflicts.append((src, "the moved file is no longer in the holding folder"))
            continue  # never moved: nothing to restore
        try:
            dst = os.lstat(lp(dest))
            if is_link_stat(dst):
                conflicts.append((src, "holding file is a link; left alone"))
                continue
            if dst.st_size != e["size"] or sha256_file(dest, cancel) != e["sha256"]:
                conflicts.append((src, "the file in the holding folder was edited; left alone"))
                continue
            if os.path.lexists(lp(src)):
                conflicts.append((src, "something already exists at the original place; not overwritten"))
                continue
            sp = os.path.dirname(src)
            _ensure_dirs(sp)
            if not _same_device(sp, dest):
                conflicts.append((src, "original place is on a different drive now"))
                continue
            os.rename(lp(dest), lp(src))
            restored.append(src)
        except OSError as ex:
            conflicts.append((src, f"could not restore ({ex.strerror or ex})"))
        if progress:
            progress("undo", i + 1, len(ents))
    try:
        _fsync_write(os.path.join(run, JOURNAL_NAME), json.dumps({"type": "undo", "restored": len(restored), "conflicts": len(conflicts)}) + "\n", "a")
    except OSError:
        pass
    return {"restored": len(restored), "conflicts": conflicts}




def _ensure_dirs(path):
    """Create missing folders without following links in existing ones."""
    if os.path.isdir(lp(path)):
        if is_link_stat(os.lstat(lp(path))):
            raise OSError("folder is a link")
        return
    parent = os.path.dirname(path)
    if parent != path:
        _ensure_dirs(parent)
    os.mkdir(lp(path))




def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024

