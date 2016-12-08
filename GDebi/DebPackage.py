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

import apt
import apt.debfile
from gettext import gettext as _


class DebPackage(apt.debfile.DebPackage):

    def __init__(self, filename, cache, downloaded=False):
        super(DebPackage, self).__init__(cache=cache, filename=filename)
        self.downloaded = downloaded

    def __getitem__(self,item):
        if not item in self._sections:
            # Translators: it's for missing entries in the deb package,
            # e.g. a missing "Maintainer" field
            return _("%s is not available") % item
        return self._sections[item]


# just for compatibility
class DscSrcPackage(apt.debfile.DscSrcPackage):
    pass


class ClickPackage(DebPackage):
    """Basic support to view the new ubuntu click packages, more to come"""

    def check(self):
        self._failure_string = _(
            "Click packages can currently only be inspected with this tool")
        return False
