"""Tiny Chrome DevTools driver (needs the 'websocket-client' package and a Chrome/Edge)."""
import base64, json, os, shutil, subprocess, tempfile, time, urllib.request


def find_chrome():
    for c in ["google-chrome", "chromium", "chromium-browser", "chrome",
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]:
        p = shutil.which(c) or (c if os.path.exists(c) else None)
        if p: return p
    return None


class Browser:
    def __init__(self, width=390, height=844, mobile=True):
        import websocket
        self.dir = tempfile.mkdtemp(prefix="cdp")
        port = 9222 + (os.getpid() % 500)
        self.proc = subprocess.Popen([find_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-first-run",
            f"--remote-debugging-port={port}", f"--user-data-dir={self.dir}", f"--window-size={width},{height}", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                t = [x for x in tabs if x["type"] == "page"][0]; break
            except Exception: time.sleep(0.2)
        self.ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
        self.n = 0; self.errors = []
        self.send("Runtime.enable"); self.send("Page.enable"); self.send("Log.enable")
        self.size(width, height, mobile)


    def send(self, method, **params):
        self.n += 1; i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("method") == "Runtime.exceptionThrown": self.errors.append(m["params"]["exceptionDetails"].get("exception", {}).get("description", "js error"))
            if m.get("method") == "Log.entryAdded" and m["params"]["entry"]["level"] == "error": self.errors.append(m["params"]["entry"]["text"] + " " + m["params"]["entry"].get("url", ""))
            if m.get("id") == i:
                if "error" in m: raise RuntimeError(m["error"])
                return m.get("result", {})


    def size(self, w, h, mobile=True):
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=mobile)


    def go(self, url):
        self.send("Page.navigate", url=url); time.sleep(0.8)


    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        if "exceptionDetails" in r: raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "js error"))
        return r["result"].get("value")


    def wait(self, expr, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expr): return True
            time.sleep(0.2)
        raise TimeoutError(expr)


    def shot(self, path, full=False):
        p = {"format": "png"}
        if full:
            h = self.js("Math.max(document.documentElement.scrollHeight,innerHeight)")
            w = self.js("innerWidth")
            p.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": w, "height": h, "scale": 1})
        open(path, "wb").write(base64.b64decode(self.send("Page.captureScreenshot", **p)["data"]))


    def close(self):
        try: self.ws.close()
        except Exception: pass
        self.proc.terminate()
        try: self.proc.wait(10)
        except Exception: self.proc.kill()
        shutil.rmtree(self.dir, True)

