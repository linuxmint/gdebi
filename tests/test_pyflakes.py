import os
import subprocess
import unittest

class TestPyflakesClean(unittest.TestCase):
    """ ensure that the tree is pyflakes clean """

    def setUp(self):
        self.paths = [
            os.path.join(os.path.dirname(__file__), ".."),
            os.path.join(os.path.dirname(__file__), "..", "gdebi"),
            os.path.join(os.path.dirname(__file__), "..", "gdebi-gtk"),
            os.path.join(os.path.dirname(__file__), "..", "gdebi-kde"),
            ]

    def test_pyflakes_clean(self):
        self.assertEqual(subprocess.check_call(['pyflakes'] + self.paths), 0)

    def test_pyflakes3_clean(self):
        self.assertEqual(subprocess.check_call(['pyflakes3'] +  self.paths), 0)


if __name__ == "__main__":
    unittest.main()
