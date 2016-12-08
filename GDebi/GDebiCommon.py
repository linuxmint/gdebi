# Copyright (c) 2005-2009 Canonical Ltd
#
# AUTHOR:
# Michael Vogt <mvo@ubuntu.com>
#
# This file is part of GDebi
#
# GDebi is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published
# by the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# GDebi is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with GDebi; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#

import gettext
import logging
import os
import sys
from mimetypes import guess_type

import apt_pkg
from apt.cache import Cache

from .DebPackage import (
    ClickPackage,
    DebPackage,
)


if sys.version_info[0] == 3:
    from gettext import gettext as _
    def py3utf8(s):
        return s
    utf8 = py3utf8
    unicode = str
else:
    def _(str):
        return utf8(gettext.gettext(str))


    def py2utf8(str):
        if isinstance(str, unicode):
            return str
        try:
            return unicode(str, 'UTF-8')
        except:
            # assume latin1 as fallback
            return unicode(str, 'latin1')
    utf8 = py2utf8


class GDebiCommon(object):

    # cprogress may be different in child classes
    def __init__(self, datadir, options, file=""):
        self.cprogress = None
        self._cache = None
        self.deps = ""
        self.version_info_title = ""
        self.version_info_msg = ""
        self._deb = None
        self._options = options
        self.install = []
        self.remove = []
        self.unauthenticated = 0

    def openCache(self):
        self._cache = Cache(self.cprogress)
        if self._cache._depcache.broken_count > 0:
            self.error_header = _("Broken dependencies")
            self.error_body = _("Your system has broken dependencies. "
                                "This application can not continue until "
                                "this is fixed. "
                                "To fix it run 'gksudo synaptic' or "
                                "'sudo apt-get install -f' "
                                "in a terminal window.")
            return False
        return True

    def open(self, file, downloaded=False):
        file = os.path.abspath(file)
        klass = DebPackage
        if file.endswith(".click"):
            klass = ClickPackage
        try:
            self._deb = klass(file, self._cache, downloaded)
        except (IOError, SystemError, ValueError) as e:
            logging.debug("open failed with %s" % e)
            mimetype=guess_type(file)
            if (mimetype[0] != None and
                mimetype[0] != "application/vnd.debian.binary-package"):
                self.error_header = _("'%s' is not a Debian package") % os.path.basename(file)
                self.error_body = _("The MIME type of this file is '%s' "
                             "and can not be installed on this system.") % mimetype[0]
                return False
            else:
                self.error_header = _("Could not open '%s'") % os.path.basename(file)
                self.error_body = _("The package might be corrupted or you are not "
                             "allowed to open the file. Check the permissions "
                             "of the file.")
                return False

    def compareDebWithCache(self):
        # check if the package is available in the normal sources as well
        res = self._deb.compare_to_version_in_cache(use_installed=False)
        if not self._options.non_interactive and res != DebPackage.VERSION_NONE:
            try:
                pkg = self._cache[self._deb.pkgname]
            except (KeyError, TypeError):
                return

            if self._deb.downloaded:
                self.version_info_title = ""
                self.version_info_msg = ""
                return

            # FIXME: make this strs better
            if res == DebPackage.VERSION_SAME:
                if pkg.candidate and pkg.candidate.downloadable:
                    self.version_info_title = _("Same version is available in a software channel")
                    self.version_info_msg = _("You are recommended to install the software "
                            "from the channel instead.")
            elif res == DebPackage.VERSION_NEWER:
                if pkg.candidate and pkg.candidate.downloadable:
                    self.version_info_title = _("An older version is available in a software channel")
                    self.version_info_msg = _("Generally you are recommended to install "
                            "the version from the software channel, since "
                            "it is usually better supported.")
            elif res == DebPackage.VERSION_OUTDATED:
                if pkg.candidate and pkg.candidate.downloadable:
                    self.version_info_title = _("A later version is available in a software "
                              "channel")
                    self.version_info_msg = _("You are strongly advised to install "
                            "the version from the software channel, since "
                            "it is usually better supported.")

    def compareProvides(self):
        provides = set()
        broken_provides = set()
        try:
            pkg = self._cache[self._deb.pkgname].installed
        except (KeyError, TypeError):
            pkg = None
        if pkg:
            if pkg.provides:
                for p in self._deb.provides:
                    for i in p:
                        provides.add(i[0])
            provides = set(pkg.provides).difference(provides)
            if provides:
                for package in list(self._cache.keys()):
                    if self._cache[package].installed:
                        for dep in self._cache[package].installed.dependencies:
                            for d in dep.or_dependencies:
                                if d.name in provides:
                                    broken_provides.add(d.name)
            return broken_provides

    def download_package(self):
        dirname = os.path.abspath(os.path.dirname(self._deb.filename))
        package = self._cache[self._deb.pkgname].candidate
        pkgname = os.path.basename(package.filename)
        if package.downloadable:
            if not os.access(dirname, os.W_OK):
                dirname = "/tmp"
            if not os.path.exists(os.path.join(dirname, pkgname)):
                package.fetch_binary(dirname)
            self.open(os.path.join(dirname, pkgname), True)
            return True

    def get_changes(self):
        (self.install, self.remove, self.unauthenticated) = self._deb.required_changes
        self.deps = ""
        if len(self.remove) == len(self.install) == 0:
            self.deps = _("All dependencies are satisfied")
        if len(self.remove) > 0:
            # FIXME: use ngettext here
            self.deps += _("Requires the <b>removal</b> of %s packages\n") % len(self.remove)
        if len(self.install) > 0:
            self.deps += _("Requires the installation of %s packages") % len(self.install)
        return True

    def try_acquire_lock(self):
        " check if we can lock the apt database "
        try:
            apt_pkg.pkgsystem_lock()
        except SystemError:
            self.error_header = _("Only one software management tool is allowed to"
                       " run at the same time")
            self.error_body = _("Please close the other application e.g. 'Update "
                     "Manager', 'aptitude' or 'Synaptic' first.")
            return False
        apt_pkg.pkgsystem_unlock()
        return True

    def acquire_lock(self):
        " lock the pkgsystem for install "
        # sanity check ( moved here )
        if self._deb is None:
          return False

        # check if we can lock the apt database
        try:
            apt_pkg.pkgsystem_lock()
        except SystemError:
            self.error_header = _("Only one software management tool is allowed to"
                                  " run at the same time")
            self.error_body = _("Please close the other application e.g. 'Update "
                                "Manager', 'aptitude' or 'Synaptic' first.")
            return False
        return True

    def release_lock(self):
        " release the pkgsystem lock "
        apt_pkg.pkgsystem_lock()
        return True
