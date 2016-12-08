# -*- coding: utf-8 -*-
#
# Copyright (c) 2005-2007 Martin Böhm
# Copyright (c) 2008-2009 Canonical Ltd
#
# AUTHOR:
# Martin Böhm <martin.bohm@ubuntu.com>
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
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import logging
import os
import subprocess

import apt
import apt_pkg

from PyKDE4.kdeui import (
    KApplication,
    KMessageBox,
    KStandardGuiItem,
    )
from PyQt4.QtCore import (
    QTimer,
    )

import pty
import select

from apt.progress.base import InstallProgress

from .GDebiCommon import utf8, _


class KDEDpkgInstallProgress(object):
    """The frontend for dpkg -i"""
    # there is only 0/100 state for the progress bar

    def __init__(self, debfile, status, progress, konsole, parent):
        # an expander would be handy, sadly we don't have one in KDE3
        self.debfile = debfile
        self.status = status
        self.progress = progress
        self.konsole = konsole
        self.parent = parent
        self.konsole.setInstallProgress(self)

        # in case there was some progress left from the deps
        self.progress.setValue(0)

    def timeoutHandler(self,signum, frame):
        raise IOError("Stopped waiting for I/O.")

    def commit(self):
        # ui
        self.status.setText(_("Installing '%s'...") % os.path.basename(self.debfile))
        # the command
        cmd = "/usr/bin/dpkg"
        argv = [cmd, "--auto-deconfigure", "-i", self.debfile]
        (self.child_pid, self.master_fd) = pty.fork()

        if self.child_pid == 0:
            os.environ["TERM"] = "dumb"
            if not "DEBIAN_FRONTEND" in os.environ:
                os.environ["DEBIAN_FRONTEND"] = "noninteractive"
            os.environ["APT_LISTCHANGES_FRONTEND"] = "none"
            exitstatus = subprocess.call(argv)
            os._exit(exitstatus)
        
        while True:
            #Read from pty and write to DumbTerminal
            try:
                (rlist, wlist, xlist) = select.select([self.master_fd],[],[], 0.001)
                if len(rlist) > 0:
                    line = os.read(self.master_fd, 255)
                    self.parent.konsole.insertWithTermCodes(utf8(line))
            except Exception as e:
                #print e
                from errno import EAGAIN
                if hasattr(e, "errno") and e.errno == EAGAIN:
                    continue
                break
            KApplication.kApplication().processEvents()
        # at this point we got a read error from the pty, that most
        # likely means that the client is dead
        (pid, status) = os.waitpid(self.child_pid, 0)
        self.exitstatus = os.WEXITSTATUS(status)

        self.progress.setValue(100)
        self.parent.closeButton.setEnabled(True)
        self.parent.closeButton.setVisible(True)
        self.parent.installationProgress.setVisible(False)
        QTimer.singleShot(1, self.parent.changeSize)

class KDEInstallProgressAdapter(InstallProgress):
    def __init__(self, progress, action, parent):
        # TODO: implement the term
        InstallProgress.__init__(self)
        self.progress = progress
        self.action = action
        self.parent = parent
        self.finished = False
        self.parent.konsole.setInstallProgress(self)

    def child_exited(self,process):
        self.finished = True
        # FIXME: check if this is just the return code or
        #        the full exit status (as returned by waitpid())
        self.apt_status = process.exitStatus()
        self.finished = True

    def error(self, pkg, errormsg):
        # FIXME: display a msg
        self.parent.showTerminal()

    def conffile(self, current, new):
        # FIXME: display a msg or expand term
        self.parent.showTerminal()

    def start_update(self):
        apt_pkg.pkgsystem_unlock()
        self.action.setText(_("Installing dependencies..."))
        self.progress.setValue(0)

    def status_change(self, pkg, percent, status):
        self.progress.setValue(percent)
        #print status # mhb debug
        #self.progress.setText(status) #FIXME set text

    def update_interface(self):
        # run the base class
        try:
            InstallProgress.update_interface(self)
        except ValueError as e:
            pass
        # log the output of dpkg (on the master_fd) to the DumbTerminal
        while True:
            try:
                (rlist, wlist, xlist) = select.select([self.master_fd],[],[], 0.01)
                # data available, read it
                if len(rlist) > 0:
                    line = os.read(self.master_fd, 255)
                    self.parent.konsole.insertWithTermCodes(utf8(line))
                else:
                    # nothing happend within the timeout, break
                    break
            except Exception as e:
                logging.debug("update_interface: %s" % e)
                break
        KApplication.kApplication().processEvents()

    def fork(self):
        """pty voodoo"""
        (self.child_pid, self.master_fd) = pty.fork()
        if self.child_pid == 0:
            os.environ["TERM"] = "dumb"
            if not "DEBIAN_FRONTEND" in os.environ:
                os.environ["DEBIAN_FRONTEND"] = "noninteractive"
            os.environ["APT_LISTCHANGES_FRONTEND"] = "none"
        return self.child_pid

    def wait_child(self):
        while True:
            try:
                select.select([self.statusfd],[],[], self.select_timeout)
            except Exception as e:
                logging.debug("wait_child: %s" % e)
                pass
            self.update_interface()
            (pid, res) = os.waitpid(self.child_pid,os.WNOHANG)
            if pid == self.child_pid:
                #print "child exited: ", pid, os.WEXITSTATUS(res)
                break
        return os.WEXITSTATUS(res)

class KDEFetchProgressAdapter(apt.progress.base.AcquireProgress):
    def __init__(self,progress,label,parent):
        super(KDEFetchProgressAdapter, self).__init__()
        self.progress = progress
        self.label = label
        self.parent = parent

    def start(self):
        super(KDEFetchProgressAdapter, self).start()
        self.label.setText(_("Downloading additional package files..."))
        self.progress.setValue(0)

    def stop(self):
        pass

    def pulse(self, owner):
        super(KDEFetchProgressAdapter, self).pulse(owner)
        at_item = min(self.current_items + 1, self.total_items)
        if self.current_cps > 0:
            self.label.setText(_("Downloading additional package files...") + _("File %s of %s at %sB/s" % (at_item, self.total_items, apt_pkg.size_to_str(self.current_cps))))
        else:
            self.label.setText(_("Downloading additional package files...") + _("File %s of %s" % (at_item, self.total_items)))
        self.progress.setValue(100 * self.current_bytes / self.total_bytes)
        KApplication.kApplication().processEvents()
        return True

    def mediaChange(self, medium, drive):
        msg = _("Please insert '%s' into the drive '%s'") % (medium,drive)
        #change = QMessageBox.question(None, _("Media Change"), msg, QMessageBox.Ok, QMessageBox.Cancel)
        change = KMessageBox.questionYesNo(None, _("Media Change"), _("Media Change") + "<br>" + msg, KStandardGuiItem.ok(), KStandardGuiItem.cancel())
        if change == KMessageBox.Yes:
            return True
        return False

class CacheProgressAdapter(apt.progress.base.OpProgress):
    def __init__(self, progressbar):
        self.progressbar = progressbar

    def update(self, percent=None):
        self.progressbar.show()
        if percent:
            self.progressbar.setValue(percent)
        KApplication.kApplication().processEvents()

    def done(self):
        pass
