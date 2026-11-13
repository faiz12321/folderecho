"""Smoke test for the built program: python tests/smoke_exe.py <path-to-exe-or-'python run.py'>"""
import os, shlex, subprocess, sys, tempfile, time, urllib.request, urllib.error, shutil


cmd = shlex.split(sys.argv[1], posix=(os.name != "nt"))
base = os.path.realpath(tempfile.mkdtemp(prefix="smoke", dir=os.environ.get("FOLDERECHO_TEST_TMP")))
try:
    a, b = os.path.join(base, "A"), os.path.join(base, "B")
    os.makedirs(a); os.makedirs(b)
    for p, d in [(a, "same"), (b, "same")]:
        with open(os.path.join(p, "x.txt"), "w") as f: f.write(d)
    with open(os.path.join(a, "y.txt"), "w") as f: f.write("same")
    out = subprocess.run(cmd + ["compare", a, b], capture_output=True, text=True, timeout=60)
    print(out.stdout, out.stderr)
    assert out.returncode == 0 and "Same in both: 1" in out.stdout, "compare failed"
    out = subprocess.run(cmd + ["dupes", a, b], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "Extra copies: 2" in out.stdout, out.stdout
    out = subprocess.run(cmd + ["compare", os.path.abspath(os.sep), b], capture_output=True, text=True, timeout=60)
    assert out.returncode == 1 and "whole drive" in out.stderr, out.stderr
    proc = subprocess.Popen(cmd + ["ui", "--no-browser"], stdout=subprocess.PIPE, text=True)
    url = None
    end = time.time() + 40
    while time.time() < end:
        line = proc.stdout.readline()
        if line.startswith("http://127.0.0.1:"):
            url = line.strip(); break
    assert url, "ui did not print its link"
    html = urllib.request.urlopen(url, timeout=10).read().decode()
    assert "FolderEcho" in html and "__TOKEN__" not in html
    try:
        urllib.request.urlopen(url.split("?")[0], timeout=10); raise SystemExit("page opened without the key")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    proc.terminate(); proc.wait(15)
    print("SMOKE OK")
finally:
    shutil.rmtree(base, True)

