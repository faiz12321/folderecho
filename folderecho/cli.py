import argparse
import json
import os
import sys


from . import core
from .core import FolderEchoError, human




def _progress(kind, done, total):
    if total:
        sys.stderr.write(f"\r{kind}: {done}/{total}   ")
    else:
        sys.stderr.write(f"\r{kind}: {done} files   ")
    sys.stderr.flush()




def print_compare(r, out):
    p = lambda s="": print(s, file=out)
    p(f"Folder A: {r['folder_a']}  ({r['counts']['a']} files)")
    p(f"Folder B: {r['folder_b']}  ({r['counts']['b']} files)")
    p()
    p(f"Same in both: {len(r['same'])}")
    p(f"Only in A: {len(r['only_in_a'])}")
    for x in r["only_in_a"]:
        p(f"  A only: {x}")
    p(f"Only in B: {len(r['only_in_b'])}")
    for x in r["only_in_b"]:
        p(f"  B only: {x}")
    p(f"Same name, different content: {len(r['different'])}")
    for a, b, why in r["different"]:
        p(f"  differs ({why}): {a}")
    p(f"Same content, different place: {len(r['same_content_elsewhere'])}")
    for a, bs in r["same_content_elsewhere"]:
        p(f"  A: {a}  =  B: {', '.join(bs)}")
    _skipped(r, out)




def _skipped(r, out):
    if r["skipped"] or r["errors"] or r["hidden_skipped"]:
        print("", file=out)
        print("Left out of the check (not looked at):", file=out)
        if r["hidden_skipped"]:
            print(f"  {r['hidden_skipped']} hidden/system files", file=out)
        for x, why in r["skipped"][:50]:
            print(f"  {x}: {why}", file=out)
        for x, why in r["errors"][:50]:
            print(f"  {x}: {why}", file=out)




def print_dupes(r, out):
    p = lambda s="": print(s, file=out)
    p(f"Checked {r['file_count']} files. Exact duplicate groups: {len(r['groups'])}. "
      f"Extra copies: {r['extra_count']}. Space the extras use: {human(r['reclaimable_bytes'])}.")
    if r["master"]:
        p(f"Master folder (never touched): {r['master']}")
    if r["empty_files"]:
        p(f"{r['empty_files']} empty files were not treated as duplicates.")
    for g in r["groups"]:
        p(f"\n  KEEP  {g['keep']['abs']}  ({human(g['size'])})")
        for x in g["extras"]:
            p(f"  extra {x['abs']}")
    _skipped(r, out)




def main(argv=None):
    ap = argparse.ArgumentParser(prog="folderecho", description="Compare folders and move exact duplicates to a holding folder (never deletes).")
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("compare", help="compare two folders")
    c.add_argument("a"); c.add_argument("b"); c.add_argument("--json", action="store_true")
    d = sub.add_parser("dupes", help="find exact duplicates (preview only)")
    d.add_argument("folders", nargs="+"); d.add_argument("--master"); d.add_argument("--json", action="store_true")
    m = sub.add_parser("move", help="move the extra copies into a holding folder")
    m.add_argument("folders", nargs="+"); m.add_argument("--master"); m.add_argument("--holding")
    m.add_argument("--yes", action="store_true", help="actually move (without it only a preview is shown)")
    u = sub.add_parser("undo", help="put back a moved run"); u.add_argument("run")
    sub.add_parser("ui", help="open the window in your browser").add_argument("--no-browser", action="store_true")
    ns = ap.parse_args(argv)
    try:
        if ns.cmd == "compare":
            r = core.compare(ns.a, ns.b, progress=_progress if sys.stderr.isatty() else None)
            if ns.json:
                print(json.dumps(r, indent=1))
            else:
                print_compare(r, sys.stdout)
            return 0
        if ns.cmd in ("dupes", "move"):
            r = core.find_duplicates(ns.folders, master=ns.master, progress=_progress if sys.stderr.isatty() else None)
            if ns.cmd == "dupes":
                if ns.json:
                    print(json.dumps(r, indent=1))
                else:
                    print_dupes(r, sys.stdout)
                return 0
            print_dupes(r, sys.stdout)
            if not ns.yes:
                print("\nPreview only. Add --yes to move the extra copies into the holding folder.")
                return 0
            sel = [x["abs"] for g in r["groups"] for x in g["extras"]]
            if not sel:
                print("Nothing to move.")
                return 0
            res = core.apply_plan(r, sel, ns.holding)
            print(f"\nMoved {res['moved']} files ({human(res['bytes'])}) into {res['run']}")
            for s, why in res["skipped"]:
                print(f"  not moved: {s}: {why}")
            print(f"To put them back: folderecho undo \"{res['run']}\"")
            return 0
        if ns.cmd == "undo":
            res = core.undo_run(ns.run)
            print(f"Put back {res['restored']} files.")
            for s, why in res["conflicts"]:
                print(f"  not restored: {s}: {why}")
            return 0 if not res["conflicts"] else 3
        if ns.cmd == "ui":
            from . import server
            server.serve(open_browser=not ns.no_browser)
            return 0
        ap.print_help()
        return 2
    except FolderEchoError as e:
        print(f"Stopped: {e}", file=sys.stderr)
        return 1
    except core.Cancelled:
        print("Stopped.", file=sys.stderr)
        return 1




if __name__ == "__main__":
    sys.exit(main())

