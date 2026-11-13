"""Local-only web window. Listens on 127.0.0.1 with a random port and a random token."""
import http.server
import json
import os
import secrets
import socketserver
import string
import sys
import threading
import webbrowser


from . import core
from .core import FolderEchoError


HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 1024 * 1024




class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.job = None          # dict(kind, status, progress, result, error)
        self.cancel = False
        self.plan = None
        self.plan_id = None




def _drives():
    if os.name == "nt":
        return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
    return ["/"]




def browse(path):
    if not path:
        home = os.path.expanduser("~")
        return {"path": "", "parent": None, "dirs": [{"name": d, "path": d} for d in _drives()] + [{"name": "Home folder", "path": home}]}
    if core.has_traversal(path) or not os.path.isabs(path):
        raise FolderEchoError("Enter a full folder path.")
    ap = os.path.abspath(path)
    try:
        names = sorted(os.listdir(core.lp(ap)), key=str.lower)
    except OSError as e:
        raise FolderEchoError(f"Cannot open that folder ({e.strerror or e}).")
    out = []
    for n in names:
        p = os.path.join(ap, n)
        try:
            st = os.lstat(core.lp(p))
        except OSError:
            continue
        import stat as _s
        if _s.S_ISDIR(st.st_mode) and not core.is_link_stat(st):
            out.append({"name": n, "path": p})
        if len(out) >= 500:
            break
    parent = os.path.dirname(ap)
    return {"path": ap, "parent": parent if parent != ap else "", "dirs": out}




def make_handler(state, token, port):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}


    class H(http.server.BaseHTTPRequestHandler):
        server_version = "FolderEcho"
        protocol_version = "HTTP/1.0"


        def log_message(self, *a):
            pass


        def _send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(b)


        def _host_ok(self):
            return self.headers.get("Host", "") in allowed_hosts


        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "forbidden", "text/plain")
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/":
                if q.get("t", [""])[0] != token:
                    return self._send(403, "Open the link printed by the program (it contains a private key).", "text/plain")
                html = open(os.path.join(HERE, "ui.html"), encoding="utf-8").read().replace("__TOKEN__", token)
                return self._send(200, html, "text/html")
            if u.path == "/favicon.ico":
                self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers(); return
            self._send(404, "not found", "text/plain")


        def do_POST(self):
            if not self._host_ok() or self.headers.get("X-Token") != token:
                return self._send(403, json.dumps({"error": "Not allowed."}))
            if not (self.headers.get("Content-Type", "").startswith("application/json")):
                return self._send(415, json.dumps({"error": "Bad request."}))
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, json.dumps({"error": "Request too large."}))
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
                assert isinstance(data, dict)
            except Exception:
                return self._send(400, json.dumps({"error": "Bad request."}))
            try:
                res = self.route(self.path, data)
                self._send(200, json.dumps(res))
            except FolderEchoError as e:
                self._send(200, json.dumps({"error": str(e)}))
            except Exception as e:  # never leak a traceback to the page
                self._send(500, json.dumps({"error": "Something went wrong. Nothing was changed by this step."}))


        # ---- routes ----
        def route(self, path, d):
            if path == "/api/browse":
                return browse(str(d.get("path", "")))
            if path == "/api/start":
                return self.start(d)
            if path == "/api/job":
                with state.lock:
                    j = state.job
                    return {"job": None if j is None else {k: j[k] for k in ("kind", "status", "progress", "result", "error")}}
            if path == "/api/cancel":
                state.cancel = True
                return {"ok": True}
            if path == "/api/apply":
                return self.apply(d)
            if path == "/api/runs":
                hp = str(d.get("holding", ""))
                if not hp or core.has_traversal(hp) or not os.path.isabs(hp):
                    raise FolderEchoError("Enter the full path of the holding folder.")
                return {"runs": core.list_runs(os.path.abspath(hp))}
            if path == "/api/undo":
                return self.undo(d)
            raise FolderEchoError("Unknown action.")


        def start(self, d):
            kind = d.get("kind")
            with state.lock:
                if state.job and state.job["status"] == "running":
                    raise FolderEchoError("A check is already running.")
                state.cancel = False
                job = {"kind": kind, "status": "running", "progress": {"stage": "scan", "done": 0, "total": None}, "result": None, "error": None}
                state.job = job
                state.plan = None
            if kind == "compare":
                fn = lambda pr, ca: core.compare(str(d.get("a", "")), str(d.get("b", "")), pr, ca)
            elif kind == "dupes":
                folders = [str(x) for x in d.get("folders", []) if str(x).strip()]
                if not folders:
                    with state.lock:
                        state.job = None
                    raise FolderEchoError("Choose at least one folder.")
                master = str(d.get("master") or "") or None
                fn = lambda pr, ca: core.find_duplicates(folders, master, pr, ca)
            else:
                with state.lock:
                    state.job = None
                raise FolderEchoError("Unknown action.")


            def run():
                def pr(stage, done, total):
                    job["progress"] = {"stage": stage, "done": done, "total": total}
                try:
                    r = fn(pr, lambda: state.cancel)
                    if kind == "dupes":
                        state.plan = r
                        r = dict(r)
                    job["result"] = r
                    job["status"] = "done"
                except FolderEchoError as e:
                    job["error"] = str(e); job["status"] = "error"
                except core.Cancelled:
                    job["error"] = "Stopped. Nothing was changed."; job["status"] = "error"
                except Exception:
                    job["error"] = "Something went wrong. Nothing was changed."; job["status"] = "error"
            threading.Thread(target=run, daemon=True).start()
            return {"started": True}


        def apply(self, d):
            with state.lock:
                if state.job and state.job["status"] == "running":
                    raise FolderEchoError("A check is already running.")
                plan = state.plan
                if plan is None:
                    raise FolderEchoError("Run the duplicate check first.")
                state.job = {"kind": "apply", "status": "running", "progress": {"stage": "move", "done": 0, "total": None}, "result": None, "error": None}
                state.cancel = False
            sel = d.get("selected")
            if not isinstance(sel, list) or not all(isinstance(x, str) for x in sel):
                with state.lock:
                    state.job = None
                raise FolderEchoError("Nothing selected.")
            hp = str(d.get("holding") or "") or None
            try:
                res = core.apply_plan(plan, sel, hp, cancel=lambda: state.cancel)
            finally:
                with state.lock:
                    state.job = None
                    state.plan = None
            return {"moved": res["moved"], "bytes": res["bytes"], "run": res["run"], "runs": res["runs"], "skipped": res["skipped"]}


        def undo(self, d):
            run = str(d.get("run", ""))
            if not run or core.has_traversal(run) or not os.path.isabs(run):
                raise FolderEchoError("Choose a run to undo.")
            run = os.path.abspath(run)
            if not os.path.basename(run).startswith("run-") or not os.path.isfile(core.lp(os.path.join(os.path.dirname(run), core.MARKER_NAME))):
                raise FolderEchoError("That is not a FolderEcho run folder.")
            res = core.undo_run(run)
            return {"restored": res["restored"], "conflicts": res["conflicts"]}


    return H




class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = False




def serve(open_browser=True, port=0):
    state = State()
    token = secrets.token_urlsafe(24)
    srv = Server(("127.0.0.1", port), lambda *a, **k: None)
    p = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(state, token, p)
    url = f"http://127.0.0.1:{p}/?t={token}"
    print("FolderEcho is running. Keep this window open while you use it.")
    print("If the page did not open, copy this link into your browser:")
    print(url)
    print("Close this window (or press Ctrl+C) to stop.")
    sys.stdout.flush()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return url




def start_for_test():
    """Start on a free port in a thread. Returns (server, url, token, state)."""
    state = State()
    token = secrets.token_urlsafe(24)
    srv = Server(("127.0.0.1", 0), lambda *a, **k: None)
    p = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(state, token, p)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{p}", token, state

