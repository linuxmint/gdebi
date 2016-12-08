#!/usr/bin/python

import os
import unittest

from locale import setlocale, LC_ALL
from unittest.mock import Mock

from GDebi.GDebiCli import GDebiCli


class GDebiCliTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # use classmethod to speed up the tests as the cache is only
        # read once
        cls.testdir = os.path.join(os.path.dirname(__file__))
        mock_options = Mock()
        mock_options.rootdir = None
        mock_options.apt_opts = []
        cls.cli = GDebiCli(options=mock_options)

    def test_against_deb_with_conflict_against_apt(self):
        setlocale(LC_ALL, "C")
        res = self.cli.open(os.path.join(self.testdir, "gdebi-test1.deb"))
        self.assertFalse(res)
        self.assertEqual(
            self.cli._deb._failure_string,
            "Conflicts with the installed package 'apt'")

    def test_against_impossible_dep(self):
        res = self.cli.open(os.path.join(self.testdir, "gdebi-test2.deb"))
        self.assertFalse(res)

    def test_against_that_works_with_no_additonal_deps(self):
        res = self.cli.open(os.path.join(self.testdir, "gdebi-test3.deb"))
        self.assertTrue(res)
        self.assertEqual(
            self.cli.get_dependencies_info(), "")


if __name__ == "__main__":
    unittest.main()
