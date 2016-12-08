#!/usr/bin/python

import os
import subprocess
import unittest


class GDebiGtkTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not "DISPLAY" in os.environ:
            DISPLAY = ":42"
            cls.xvfb = subprocess.Popen(["Xvfb", DISPLAY])
            os.environ["DISPLAY"] = DISPLAY
        else:
            cls.xvfb = None

    @classmethod
    def tearDownClass(cls):
        if cls.xvfb:
            cls.xvfb.kill()

    def setUp(self):
        from GDebi.GDebiGtk import GDebiGtk
        datadir = os.path.join(os.path.dirname(__file__), "..", "data")
        self.app = GDebiGtk(datadir=datadir, options=None)
    
    def test_simple(self):
        self.assertNotEqual(self.app, None)


if __name__ == "__main__":
    unittest.main()
