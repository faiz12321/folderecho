"""Real-browser test of the window against the real local server (skipped without Chrome/websocket-client)."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import websocket  # noqa
    import cdp
    HAVE = cdp.find_chrome() is not None
except Exception:
    HAVE = False
from folderecho import server


NP = os.path.normpath


def w(p, d):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f: f.write(d)


@unittest.skipUnless(HAVE, "Chrome or websocket-client not available")
class E2E(unittest.TestCase):
    def setUp(self):
        self.srv, self.base, self.tok, _ = server.start_for_test()
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="e2e", dir=os.environ.get("FOLDERECHO_TEST_TMP")))
        self.a = os.path.join(self.tmp, "Photos"); self.b = os.path.join(self.tmp, "Backup")
        w(self.a + "/IMG_1.jpg", b"x" * 500); w(self.a + "/IMG_1 copy.jpg", b"x" * 500)
        w(self.b + "/old/IMG_1.jpg", b"x" * 500); w(self.a + "/only.txt", b"only a"); w(self.b + "/other.txt", b"b only!")
        self.br = cdp.Browser(390, 844)
        self.br.go(self.base + "/?t=" + self.tok)


    def tearDown(self):
        self.br.close(); self.srv.shutdown(); shutil.rmtree(self.tmp, True)
        # no unexpected JS or network errors (favicon is answered with 204)
        self.assertEqual([e for e in self.br.errors], [])


    def overflow(self):
        return self.br.js("document.documentElement.scrollWidth>document.documentElement.clientWidth+1")


    def test_layout_no_overflow_at_phone_and_desktop(self):
        for w_, h, mob in [(320, 700, True), (390, 844, True), (1280, 800, False)]:
            self.br.size(w_, h, mob); self.br.go(self.base + "/?t=" + self.tok)
            for t in ["compare", "dupes", "undo", "about"]:
                self.br.js(f"document.querySelector('#t-{t}').click()")
                self.assertFalse(self.overflow(), f"overflow at {w_} on {t}")


    def test_compare_flow(self):
        self.br.js(f"ca.value={self.a!r};cb.value={self.b!r};document.querySelector('#go-compare').click()")
        self.br.wait("document.querySelectorAll('#out .stat').length==5")
        txt = self.br.js("document.querySelector('#out').innerText")
        self.assertIn("only.txt", txt); self.assertIn("Nothing was changed", txt)
        self.assertEqual(self.br.js("[...document.querySelectorAll('#out .stat b')].map(b=>b.innerText).join(',')"), "0,1,1,0,2")
        self.assertFalse(self.overflow())


    def test_dupes_move_undo_flow(self):
        self.br.js("document.querySelector('#t-dupes').click()")
        self.br.js(f"var i=document.querySelectorAll('#dfolders input');i[0].value={self.a!r};i[0].dispatchEvent(new Event('input'));i[1].value={self.b!r};i[1].dispatchEvent(new Event('input'));document.querySelector('#master').value={self.b!r};document.querySelector('#go-dupes').click()")
        self.br.wait("document.querySelectorAll('#out .extra input').length>0")
        boxes = self.br.js("[...document.querySelectorAll('#out .extra input')].map(b=>b.value)")
        self.assertEqual(sorted(boxes), sorted([NP(self.a + "/IMG_1.jpg"), NP(self.a + "/IMG_1 copy.jpg")]))
        self.assertIn("KEEP " + NP(self.b + "/old/IMG_1.jpg"), self.br.js("document.querySelector('#out').innerText"))
        self.br.shot(os.path.join(self.tmp, "dupes.png"), full=True)
        # unselect one, then move
        self.br.js("document.querySelectorAll('#out .extra input')[0].click()")
        self.assertIn("1 files selected", self.br.js("document.querySelector('#out').innerText"))
        hold = os.path.join(self.tmp, "hold")
        self.br.js(f"var h=document.querySelector('#out input[type=text]');h.value={hold!r};[...document.querySelectorAll('#out button')].find(b=>b.innerText.startsWith('Move selected')).click()")
        self.br.wait("document.querySelector('#m-confirm').classList.contains('on')")
        self.assertTrue(os.path.exists(self.a + "/IMG_1.jpg") and os.path.exists(self.a + "/IMG_1 copy.jpg"))  # nothing moved before confirm
        self.br.js("document.querySelector('#cf-yes').click()")
        self.br.wait("document.querySelector('#out').innerText.includes('Moved 1 files')")
        self.assertEqual(sum(os.path.exists(p) for p in [NP(self.a + "/IMG_1.jpg"), NP(self.a + "/IMG_1 copy.jpg")]), 1)
        self.assertTrue(os.path.exists(self.b + "/old/IMG_1.jpg"))
        self.br.js("[...document.querySelectorAll('#out button')].find(b=>b.innerText.startsWith('Undo')).click()")
        self.br.wait("document.querySelector('#out').innerText.includes('Put back 1 files')")
        self.assertTrue(os.path.exists(self.a + "/IMG_1.jpg") and os.path.exists(self.a + "/IMG_1 copy.jpg"))


    def test_refusal_is_shown_in_plain_words(self):
        self.br.js("ca.value='/';cb.value='/tmp';document.querySelector('#go-compare').click()")
        self.br.wait("document.querySelector('#status').innerText.includes('whole drive')")
        self.assertIn("whole drive", self.br.js("document.querySelector('#status').innerText"))


    def test_folder_picker(self):
        self.br.js("document.querySelector('[data-pick=ca]').click()")
        self.br.wait("document.querySelector('#m-pick').classList.contains('on') && document.querySelectorAll('#pk-list button').length>0")
        self.br.js(f"ca.value={self.tmp!r}")
        self.br.js("document.querySelector('[data-pick=ca]').click()")
        self.br.wait(f"document.querySelector('#pk-path').innerText=={self.tmp!r}")
        names = self.br.js("[...document.querySelectorAll('#pk-list button')].map(b=>b.innerText).sort().join(',')")
        self.assertEqual(names, "Backup,Photos")
        self.br.js("[...document.querySelectorAll('#pk-list button')].find(b=>b.innerText=='Photos').click()")
        self.br.wait(f"document.querySelector('#pk-path').innerText=={self.a!r}")
        self.br.js("document.querySelector('#pk-use').click()")
        self.assertEqual(self.br.js("ca.value"), self.a)
        self.assertFalse(self.br.js("document.querySelector('#m-pick').classList.contains('on')"))


    def test_page_text_is_not_executed(self):
        evil = os.path.join(self.tmp, "&lt;img src=x onerror=window.PWNED=1&gt;" if os.name == "nt" else "<img src=x onerror=window.PWNED=1>")
        w(evil + "/a.txt", b"1"); w(self.b + "/b.txt", b"2")
        self.br.js(f"ca.value={evil!r};cb.value={self.b!r};document.querySelector('#go-compare').click()")
        self.br.wait("document.querySelectorAll('#out .stat').length==5")
        self.assertFalse(self.br.js("!!window.PWNED"))


if __name__ == "__main__":
    unittest.main()

