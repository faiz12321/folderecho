"""The demo page (made-up files) in a real browser."""
import os, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT); sys.path.insert(0, HERE)
try:
    import websocket  # noqa
    import cdp
    HAVE = cdp.find_chrome() is not None
except Exception:
    HAVE = False


@unittest.skipUnless(HAVE, "Chrome or websocket-client not available")
class Demo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="demo", dir=os.environ.get("FOLDERECHO_TEST_TMP"))
        self.page = os.path.join(self.tmp, "demo.html")
        subprocess.check_call([sys.executable, os.path.join(ROOT, "tools", "build_demo.py"), self.page], stdout=subprocess.DEVNULL)
        self.br = cdp.Browser(390, 844)
        self.br.go("file:///" + self.page.replace("\\", "/").lstrip("/"))
    def tearDown(self):
        self.br.close()
        self.assertEqual(self.br.errors, [])
    def test_demo_flows(self):
        b = self.br
        self.assertFalse(b.js("document.querySelector('#demo').hidden"))
        b.js("document.querySelector('#go-compare').click()")
        b.wait("document.querySelectorAll('#out .stat').length==5")
        self.assertIn("Made-up", b.js("document.body.innerText") or "Made-up")
        b.js("document.querySelector('#t-dupes').click()")
        self.assertEqual(b.js("document.querySelector('#master').value"), b.js("document.querySelectorAll('#dfolders input')[1].value"))
        b.js("document.querySelector('#go-dupes').click()")
        b.wait("document.querySelectorAll('#out .extra input').length>0")
        n = b.js("document.querySelectorAll('#out .extra input').length")
        b.js("[...document.querySelectorAll('#out button')].find(x=>x.innerText.startsWith('Move selected')).click()")
        b.wait("document.querySelector('#m-confirm').classList.contains('on')")
        b.js("document.querySelector('#cf-yes').click()")
        b.wait("document.querySelector('#out').innerText.includes('Moved '+%d+' files')" % n)
        b.js("[...document.querySelectorAll('#out button')].find(x=>x.innerText.startsWith('Undo')).click()")
        b.wait("document.querySelector('#out').innerText.includes('Put back')")
        b.js("document.querySelector('#t-undo').click()"); b.js("document.querySelector('#go-runs').click()")
        b.wait("document.querySelectorAll('#runs .grp').length==1")
        self.assertFalse(b.js("document.documentElement.scrollWidth>document.documentElement.clientWidth+1"))


if __name__ == "__main__":
    unittest.main()

