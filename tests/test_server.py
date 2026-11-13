import json, os, shutil, sys, tempfile, time, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from folderecho import server, core


NP = os.path.normpath


def call(base, token, path, body=None, headers=None, host=None, raw=None):
    h = {"Content-Type": "application/json", "X-Token": token}
    h.update(headers or {})
    if host: h["Host"] = host
    req = urllib.request.Request(base + path, data=raw if raw is not None else json.dumps(body or {}).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, None


def _wf(p, d):
    with open(p, "wb") as f: f.write(d)


def w(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True); _wf(p, d)


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.srv, self.base, self.tok, self.state = server.start_for_test()
        self.addCleanup(self.srv.shutdown)
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="fesrv", dir=os.environ.get("FOLDERECHO_TEST_TMP"))); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.a = os.path.join(self.tmp, "A"); self.b = os.path.join(self.tmp, "B")
        w(self.a + "/x.bin", b"dupdata" * 10); w(self.a + "/y.bin", b"dupdata" * 10); w(self.b + "/z.txt", b"zz")


    def wait(self):
        for _ in range(100):
            _, j = call(self.base, self.tok, "/api/job"); j = j["job"]
            if j["status"] != "running": return j
            time.sleep(0.05)
        self.fail("job stuck")


    def test_page_needs_token_and_host(self):
        def get(path, host=None):
            h = {"Host": host} if host else {}
            try:
                with urllib.request.urlopen(urllib.request.Request(self.base + path, headers=h)) as r: return r.status, r.read().decode()
            except urllib.error.HTTPError as e: return e.code, ""
        self.assertEqual(get("/")[0], 403)
        self.assertEqual(get("/?t=wrong")[0], 403)
        code, html = get("/?t=" + self.tok); self.assertEqual(code, 200)
        self.assertIn(self.tok, html); self.assertNotIn("__TOKEN__", html)
        self.assertEqual(get("/?t=" + self.tok, host="evil.example")[0], 403)


    def test_api_needs_token_json_and_host(self):
        self.assertEqual(call(self.base, "bad", "/api/browse")[0], 403)
        self.assertEqual(call(self.base, self.tok, "/api/browse", host="evil.example")[0], 403)
        self.assertEqual(call(self.base, self.tok, "/api/browse", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(call(self.base, self.tok, "/api/browse", raw=b"not json")[0], 400)
        self.assertEqual(call(self.base, self.tok, "/api/browse", raw=b"[1,2]")[0], 400)


    def test_full_flow_dupes_apply_undo(self):
        _, r = call(self.base, self.tok, "/api/start", {"kind": "dupes", "folders": [self.a, self.b]}); self.assertTrue(r.get("started"))
        j = self.wait(); self.assertEqual(j["status"], "done"); self.assertEqual(j["result"]["extra_count"], 1)
        sel = [x["abs"] for g in j["result"]["groups"] for x in g["extras"]]
        hold = os.path.join(self.tmp, "hold")
        _, r = call(self.base, self.tok, "/api/apply", {"selected": sel, "holding": hold}); self.assertEqual(r["moved"], 1)
        self.assertFalse(os.path.exists(sel[0]))
        _, rr = call(self.base, self.tok, "/api/runs", {"holding": hold}); self.assertEqual(len(rr["runs"]), 1)
        _, u = call(self.base, self.tok, "/api/undo", {"run": r["run"]}); self.assertEqual(u["restored"], 1)
        self.assertTrue(os.path.exists(sel[0]))


    def test_apply_needs_a_preview_and_only_previewed_files(self):
        _, r = call(self.base, self.tok, "/api/apply", {"selected": [NP(self.a + "/x.bin")]}); self.assertIn("error", r)
        call(self.base, self.tok, "/api/start", {"kind": "dupes", "folders": [self.a]}); self.wait()
        _, r = call(self.base, self.tok, "/api/apply", {"selected": [NP(self.b + "/z.txt")], "holding": os.path.join(self.tmp, "h")}); self.assertIn("error", r)
        self.assertTrue(os.path.exists(self.b + "/z.txt"))


    def test_undo_refuses_random_folders(self):
        victim = os.path.join(self.tmp, "run-fake"); os.makedirs(victim)
        _, r = call(self.base, self.tok, "/api/undo", {"run": victim}); self.assertIn("error", r)
        _, r = call(self.base, self.tok, "/api/undo", {"run": "relative"}); self.assertIn("error", r)
        _, r = call(self.base, self.tok, "/api/undo", {"run": self.tmp + "/../x/run-1"}); self.assertIn("error", r)


    def test_compare_and_refusals_through_api(self):
        call(self.base, self.tok, "/api/start", {"kind": "compare", "a": self.a, "b": self.b})
        j = self.wait(); self.assertEqual(j["status"], "done"); self.assertEqual(len(j["result"]["only_in_a"]), 2)
        _, r = call(self.base, self.tok, "/api/start", {"kind": "compare", "a": "/", "b": self.b})
        self.assertNotIn("error", r); j = self.wait(); self.assertEqual(j["status"], "error")
        _, r = call(self.base, self.tok, "/api/start", {"kind": "dupes", "folders": []}); self.assertIn("error", r)


    def test_browse_lists_only_real_folders(self):
        os.makedirs(self.a + "/sub")
        _, r = call(self.base, self.tok, "/api/browse", {"path": self.a}); self.assertEqual([d["name"] for d in r["dirs"]], ["sub"])
        _, r = call(self.base, self.tok, "/api/browse", {"path": "relative"}); self.assertIn("error", r)
        _, r = call(self.base, self.tok, "/api/browse", {"path": self.a + "/../B"}); self.assertIn("error", r)


if __name__ == "__main__":
    unittest.main()

