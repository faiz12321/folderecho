"""Build a self-contained demo page from real engine output on made-up files.
The demo page cannot touch any real file."""
import json, os, shutil, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import core


def w(p, d, t=None):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(d)
    if t: os.utime(p, (t, t))


base = os.path.realpath(tempfile.mkdtemp(prefix="demo", dir=os.environ.get("FOLDERECHO_TEST_TMP") or None))
try:
    a, b = os.path.join(base, "Photos"), os.path.join(base, "Backup 2024")
    t0 = time.time() - 86400 * 30
    for i in range(1, 6):
        w(f"{a}/Holiday/IMG_{i:03d}.jpg", (f"holiday photo {i} " * 2000).encode(), t0 + i)
    w(f"{a}/Holiday/IMG_002 copy.jpg", ("holiday photo 2 " * 2000).encode(), t0 + 50)
    w(f"{a}/Holiday/IMG_004 (1).jpg", ("holiday photo 4 " * 2000).encode(), t0 + 60)
    w(f"{a}/Downloads/invoice-march.pdf", ("invoice march " * 900).encode(), t0)
    w(f"{a}/Downloads/invoice-march (2).pdf", ("invoice march " * 900).encode(), t0 + 7)
    w(f"{a}/Downloads/notes.txt", b"only in photos folder")
    w(f"{a}/budget.xlsx", b"budget v2 " * 700)
    for i in range(1, 6):
        w(f"{b}/Holiday/IMG_{i:03d}.jpg", (f"holiday photo {i} " * 2000).encode(), t0 + i)
    w(f"{b}/Old downloads/invoice-march.pdf", ("invoice march " * 900).encode(), t0)
    w(f"{b}/budget.xlsx", b"budget v1 " * 700)
    w(f"{b}/receipts/shop.pdf", b"receipt " * 500)
    w(f"{b}/Family/reunion.jpg", ("holiday photo 3 " * 2000).encode())
    cmp_ = core.compare(a, b)
    dup = core.find_duplicates([a, b], master=b)
    fake = "C:\\Users\\Sam"
    def conv(o):
        if isinstance(o, str):
            return o.replace(base, fake).replace("/", "\\") if o.startswith(base) else o.replace("/", "\\") if ("/" in o and not o.startswith("http")) else o
        if isinstance(o, dict): return {k: conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [conv(v) for v in o]
        return o
    cmp_c, dup_c = conv(cmp_), conv(dup)
    demo = {"a": fake + "\\Photos", "b": fake + "\\Backup 2024", "compare": cmp_c, "dupes": dup_c,
            "holding": fake + "\\FolderEcho holding",
            "run": fake + "\\FolderEcho holding\\run-20261016-221500",
            "tree": {"": {"path": "", "parent": None, "dirs": [{"name": "C:\\", "path": "C:\\"}]},
                     "C:\\": {"path": "C:\\", "parent": "", "dirs": [{"name": "Users", "path": "C:\\Users"}]},
                     "C:\\Users": {"path": "C:\\Users", "parent": "C:\\", "dirs": [{"name": "Sam", "path": fake}]},
                     fake: {"path": fake, "parent": "C:\\Users", "dirs": [{"name": "Photos", "path": fake + "\\Photos"}, {"name": "Backup 2024", "path": fake + "\\Backup 2024"}]},
                     fake + "\\Photos": {"path": fake + "\\Photos", "parent": fake, "dirs": [{"name": "Holiday", "path": fake + "\\Photos\\Holiday"}, {"name": "Downloads", "path": fake + "\\Photos\\Downloads"}]},
                     fake + "\\Backup 2024": {"path": fake + "\\Backup 2024", "parent": fake, "dirs": [{"name": "Holiday", "path": fake + "\\Backup 2024\\Holiday"}]}}}
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "folderecho", "ui.html"), encoding="utf-8").read()
    html = html.replace("const TOKEN=\"__TOKEN__\";", "const TOKEN=\"demo\";window.__DEMO__=" + json.dumps(demo).replace("</", "<\\/") + ";")
    out = sys.argv[1] if len(sys.argv) > 1 else "demo.html"
    with open(out, "w", encoding="utf-8", newline="\n") as f: f.write(html)
    print("wrote", out, len(html), "bytes; compare:", {k: len(v) for k, v in cmp_.items() if isinstance(v, list)}, "dupe groups:", len(dup["groups"]), "extras:", dup["extra_count"])
finally:
    shutil.rmtree(base, True)

