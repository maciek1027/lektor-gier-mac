"""Dock icon and iPhone icon made by aplikacja/ikona.py from the real artwork (aplikacja/ikona-zrodlo.jpg).

What breaks for the user when these fail: a Dock icon with see-through holes or square corners (macOS then frames it in
grey as a "legacy" icon), an iPhone icon with transparency (iOS paints it black), or a missing artwork silently giving
some other icon. Runs the real script as zbuduj.sh does (python -I, own folder)."""
import os
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

KOD = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APL = os.path.join(KOD, "aplikacja")
S, M, R = 1024, 100, 185


def zrob(*argumenty):
    t = tempfile.mkdtemp(prefix="lektor-ikona-")
    out = os.path.join(t, "ikona.png")
    r = subprocess.run([sys.executable, "-I", "ikona.py", out, *argumenty], cwd=APL, capture_output=True, text=True, timeout=60)
    return r, out


class TestIkona(unittest.TestCase):
    def test_ikona_docka_ma_pelne_wnetrze_i_przezroczyste_rogi(self):
        r, out = zrob()
        self.assertEqual(r.returncode, 0, r.stderr)
        im = Image.open(out)
        self.assertEqual((im.mode, im.size), ("RGBA", (S, S)))
        a = im.split()[3]
        for xy in ((5, 5), (S - 6, 5), (5, S - 6), (S - 6, S - 6), (M + 10, M + 10)):  # outside + inside the rounded corner
            self.assertEqual(a.getpixel(xy), 0, f"róg {xy} nie jest przezroczysty")
        # the whole body minus the rounded corners: every pixel fully opaque (a half-transparent pixel = grey frame)
        for pudlo in ((M + R, M, S - M - R, S - M), (M, M + R, S - M, S - M - R)):
            self.assertEqual(a.crop(pudlo).getextrema(), (255, 255), f"dziura w ikonie w {pudlo}")

    def test_ikona_iphonea_bez_przezroczystosci(self):
        r, out = zrob("--pelne")
        self.assertEqual(r.returncode, 0, r.stderr)
        im = Image.open(out)
        self.assertEqual((im.mode, im.size), ("RGB", (S, S)))

    def test_brak_obrazu_to_blad_nie_inna_ikona(self):
        r, out = zrob("--zrodlo", os.path.join(tempfile.mkdtemp(), "nie-ma.jpg"))
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(out), "przy braku obrazu powstała jakaś inna ikona")


if __name__ == "__main__":
    unittest.main()
