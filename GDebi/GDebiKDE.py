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
import re
import sys


from PyKDE4.kdeui import (
    KApplication,
    KIcon,
    KMessageBox,
    DesktopIcon,
    )
from PyQt4.QtCore import (
    Qt,
    SIGNAL,
    QTimer,
    )
from PyQt4.QtGui import (
    QDialog,
    QHBoxLayout,
    QTextCursor,
    QTextEdit,
    QTextOption,
    )
from PyQt4 import uic

from apt.cache import Cache

import gettext

from .DebPackage import DebPackage
from .GDebiCommon import GDebiCommon, utf8, _
from .KDEAptDialogs import (
    CacheProgressAdapter,
    KDEDpkgInstallProgress,
    KDEFetchProgressAdapter,
    KDEInstallProgressAdapter,
    )


def __(catalog,str):
    return utf8(gettext.dgettext(catalog, str))

def loadUi(file, parent):
    """load local file if possible else use installed file"""
    if os.path.exists("data/" + file):
        uic.loadUi("data/" + file, parent)
    else:
        uic.loadUi("/usr/share/gdebi/" + file, parent)

class DumbTerminal(QTextEdit):
    """ a very dumb terminal """
    def __init__(self, parent_frame):
        """ really dumb terminal with simple editing support """
        QTextEdit.__init__(self, parent_frame)
        self.setFontFamily("Monospace")
        self.setFontPointSize(8)
        self.setWordWrapMode(QTextOption.NoWrap)
        self.setUndoRedoEnabled(False)
        self._block = False
        self.connect(self, SIGNAL("cursorPositionChanged(int,int)"), self.onCursorPositionChanged)

    def setInstallProgress(self, installProgress):
        self.installProgress = installProgress

    def insertWithTermCodes(self, text):
        """ support basic terminal codes """
        display_text = ""
        for c in text:
            # \b - backspace
            if c == chr(8):
                self.moveCursor(QTextEdit.MoveBackward, QTextCursor.KeepAnchor)
                self.cut() #self.removeSelectedText()  FIXME
            # \r - is filtered out
            elif c == chr(13):
                pass
            # \a - bell - ignore for now
            elif c == chr(7):
                pass
            else:
                display_text += c
        self.insertPlainText(display_text)

    def keyPressEvent(self, ev):
        """ send (ascii) key events to the pty """
        # no master_fd yet
        if not hasattr(self.installProgress, "master_fd"):
            return
        # special handling for backspace
        if ev.key() == Qt.Key_Backspace:
            #print "sent backspace"
            os.write(self.installProgress.master_fd, chr(8))
            return
        # do nothing for events like "shift"
        if not ev.text():
            return
        # now sent the key event to the termianl as utf-8
        os.write(self.installProgress.master_fd, ev.text().toUtf8())

    def onCursorPositionChanged(self, x, y):
        """ helper that ensures that the cursor is always at the end """
        if self._block:
            return
        # block signals so that we do not run into a recursion
        self._block = True
        #para = self.paragraphs() - 1
        #pos = self.paragraphLength(para)
        self.moveCursor(QTextCursor.End)
        self._block = False


class GDebiKDEDialog(QDialog):
    """Our main user interface, load from UI file"""
    def __init__(self, parent):
        QDialog.__init__(self, parent)
        loadUi("GDebiKDEDialog.ui", self)


class GDebiKDE(GDebiCommon, GDebiKDEDialog):

    def __init__(self, datadir, options, file="", parent=None, name=None,
                 modal=0, fl=0):
        GDebiKDEDialog.__init__(self,parent)
        GDebiCommon.__init__(self,datadir,options,file)
        # load the icon
        self.setWindowIcon(KIcon("application-x-deb"))
        # first, we load all the default descriptions -- pyuic doesn't use
        # gettext as default (FIXME, copy code from language-selector)
        self.textLabel1.setText(_("Package:"))
        self.textLabel1_2.setText(_("Status:"))
        self.detailsButton.setText(_("Details"))
        self.tabWidget2.setTabText(0,_("Description"))
        self.tabWidget2.setTabText(1,_("Details"))
        self.tabWidget2.setTabText(2,_("Included Files"))
        self.cancelButton.setText(__("kdelibs","&Cancel"))
        self.installButton.setText(_("&Install Package"))
        self.downloadButton.setText(_("&Download Package"))
        self.DetailsVersionLabel.setText(_("<b>Version:</b>"))
        self.DetailsMaintainerLabel.setText(_("<b>Maintainer:</b>"))
        self.DetailsPriorityLabel.setText(_("<b>Priority:</b>"))
        self.DetailsSectionLabel.setText(_("<b>Section:</b>"))
        self.DetailsSizeLabel.setText(_("<b>Size:</b>"))
        # translation finished
        self.setDisabled(True)
        self.PackageProgressBar.setEnabled(True)
        self.detailsButton.hide()
        self.downloadButton.hide()
        self.installButton.setIcon(KIcon("dialog-ok"))
        self.cancelButton.setIcon(KIcon("dialog-cancel"))
        self.show()
        self.kapp = KApplication.kApplication() #incidently, this stops it crashing on quit, no idea why, jriddell
        self.kapp.processEvents() #run because openCache takes a while to do its thing
        self.cprogress = CacheProgressAdapter(self.PackageProgressBar)
        if not self.openCache():
            KMessageBox.error(self, '<b>' + self.error_header + '</b><br>' + self.error_body,
                self.error_header)
            sys.exit(1)
        # try to open the file
        if file != "" and os.path.exists(file):
            self.open(file)
        else:
            header = _("The package file does not exist")
            body = _("A nonexistent file has been selected for installation. Please select an existing .deb package file.")
            KMessageBox.error(self, '<b>' + header + '</b><br>' + body, header)
            sys.exit(1)

        self.setEnabled(True)
        self.PackageProgressBar.hide()
        self.connect(self.cancelButton, SIGNAL("clicked()"), self.cancelButtonClicked)
        self.connect(self.installButton, SIGNAL("clicked()"), self.installButtonClicked)
        self.connect(self.downloadButton, SIGNAL("clicked()"), self.downloadButtonClicked)
        self.connect(self.detailsButton, SIGNAL("clicked()"), self.detailsButtonClicked)

    def open(self, file, downloaded=False):
        """ load the .deb file """
        res = GDebiCommon.open(self, file, downloaded)
        if res == False:
            KMessageBox.error(self, '<b>' + self.error_header + '</b><br>' + self.error_body, self.error_header)
            return False

        # set name
        self.setWindowTitle(_("Package Installer - %s") % self._deb.pkgname)
        self.textLabel1_3.setText(self._deb.pkgname)

        # set description
        buf = self.DecriptionEdit
        try:
            long_desc = ""
            raw_desc = str.splitlines(utf8(self._deb["Description"]))
            # append a newline to the summary in the first line
            summary = raw_desc[0]
            raw_desc[0] = ""
            long_desc = "%s\n" % summary
            for line in raw_desc:
                tmp = str.strip(line)
                if tmp == ".":
                    long_desc += "\n"
                else:
                    long_desc += tmp + "\n"
            # do some regular expression magic on the description
            # Add a newline before each bullet
            p = re.compile(r'^(\s|\t)*(\*|0|-)',re.MULTILINE)
            long_desc = p.sub('\n*', long_desc)
            # add the <b> tag to the first line
            long_desc = re.sub(r'\n','</b>\n',long_desc,1)
            # replace all newlines by spaces
            p = re.compile(r'\n', re.MULTILINE)
            long_desc = p.sub(" ", long_desc)
            # replace all multiple spaces by
            # paragraph tags
            p = re.compile(r'\s\s+', re.MULTILINE)
            long_desc = p.sub("</p><p>", long_desc)
            # add the remaining tags
            long_desc = '<p><b>' + long_desc + '</p>'
            # write the descr string to the buffer
            buf.setText(long_desc)
        except KeyError:
            buf.setText(_("No description is available"))

        # check deps
        if not self._deb.check():
            self.installButton.setEnabled(False)
            self.textLabel1_3_2.setText(utf8("<font color=\"#ff0000\"> Error: " + self._deb._failure_string + "</font>"))
            self.detailsButton.hide()
            return False

        # check provides
        provides = self.compareProvides()
        if provides:
            self.installButton.setEnabled(False)
            self.textLabel1_3_2.setText(utf8("<font color=\"#ff0000\"> Error: no longer provides " + ", ".join(provides) + "</font>"))    
            return False

        # set version_info_{msg,title} strings
        self.compareDebWithCache()

        version_status = self._deb.compare_to_version_in_cache(use_installed=False)
        if (version_status in (DebPackage.VERSION_SAME, DebPackage.VERSION_OUTDATED)):
            if not self._deb.downloaded:
                self.downloadButton.show()

        if self._deb.compare_to_version_in_cache() == DebPackage.VERSION_SAME:
            #self.textLabel1_3_2.setText(_("Same version is already installed"))
            self.installButton.setText(_("&Reinstall Package"))
            self.installButton.setIcon(KIcon("view-refresh"))
            #TODO
            #self.button_install.grab_default()
            #self.button_install.set_sensitive(True)
            #self.button_details.hide()

        # load changes into (self.install, self.remove, self.unauthenticated)
        self.get_changes()

        if self.version_info_title != "" and self.version_info_msg != "":
            self.infoIcon.setPixmap(DesktopIcon("dialog-information"))
            self.infoBox.setText(self.version_info_title + '\n' + self.version_info_msg)

        if len(self.remove) == len(self.install) == 0:
            pass # handled by common core
        else:
            self.detailsButton.show()

        self.textLabel1_3_2.setText(self.deps)
        # set various status bits
        self.DetailsVersion.setText(self._deb["Version"])
        self.DetailsMaintainer.setText(utf8(self._deb["Maintainer"]))
        self.DetailsPriority.setText(self._deb["Priority"])
        self.DetailsSection.setText(utf8(self._deb["Section"]))
        self.DetailsSize.setText(self._deb["Installed-Size"] + " KiB")

        # set filelist
        buf = self.IncFilesEdit
        buf.setText("\n".join(self._deb.filelist))

        if not self._deb.check():
            self.installButton.setText(_("&Install Package"))

        if self._deb.compare_to_version_in_cache() == DebPackage.VERSION_SAME:
            self.installButton.setText(_("Re&install Package"))

    def cancelButtonClicked(self):
        self.close()

    def detailsButtonClicked(self):
        changedList = list()
        (install, remove, unauthenticated) = self._deb.required_changes
        for i in install:
            changedList.append(_("To be installed: %s") % i)
        for r in remove:
            changedList.append(_("To be removed: %s") % r)

        KMessageBox.informationList(
            self,
            _("<b>To install the following changes are required:</b>"),
            changedList, _("Details"))

    def installButtonClicked(self):
        # if not root, start a new instance
        if os.getuid() != 0:
            if os.path.exists("gdebi-kde"):
                executable = os.path.curdir + "/gdebi-kde"
            else:
                executable = "/usr/bin/gdebi-kde"
            su_cmd = "/usr/bin/kdesudo"
            if not os.access(su_cmd, os.X_OK):
                su_cmd = "/usr/lib/kde4/libexec/kdesu"
            os.execl(su_cmd, os.path.basename(su_cmd), executable, "--", "-n", self._deb.filename)
            self.kapp.exit()

        if not self.try_acquire_lock():
            KMessageBox.error(self, '<b>' + self.error_header + '</b><br>' + self.error_body, self.error_header)
            return False
        if not self.try_acquire_lock():
            KMessageBox.error(self, '<b>' + self.error_header + '</b><br>' + self.error_body, self.error_header)
            return False
        # set the window as disabled
        self.setDisabled(True)
        self.installDialog = GDebiKDEInstall(self)
        self.installDialog.show()

        # FIXME: use the new python-apt acquire interface here,
        # or rather use it in the apt module and raise exception
        # when stuff goes wrong!
        if len(self.install) > 0 or len(self.remove) > 0:
            #if not self.acquire_lock():
            if not self.acquire_lock():
                #self.show_alert(Gtk.MessageType.ERROR, self.error_header, self.error_body)
                KMessageBox.sorry(self, '<b>' + self.error_header + '</b><br>' + self.error_body, self.error_header)
                return False
            fprogress = KDEFetchProgressAdapter(self.installDialog.installationProgress,
                                                self.installDialog.installingLabel,
                                                self.installDialog)
            iprogress = KDEInstallProgressAdapter(self.installDialog.installationProgress,
                                                  self.installDialog.installingLabel,
                                                  self.installDialog)
            self.installDialog.konsole.setInstallProgress(iprogress)
            #errMsg = None
            try:
                res = self._cache.commit(fprogress,iprogress)
            except IOError as msg:
                res = False
                #errMsg = "%s" % msg
                header = _("Could not download all required files")
                body = _("Please check your internet connection or "
                            "installation medium, or make sure your "
                            "APT cache is up-to-date.")
            except SystemError as msg:
                logging.warning("error: %s" % msg)
                res = False
                header = _("Could not install all dependencies")
                body = _("Usually this is related to an error of the "
                        "software distributor. See the terminal window for "
                        "more details.")
            if not res:
                #print "something bad happend"
                self.errorReport = KMessageBox.error(self, header + "<br>" + body, header)
                return

        # install the package itself
        #self.label_action.set_markup("<b><big>"+_("Installing package file")+"</big></b>")
        dprogress = KDEDpkgInstallProgress(self._deb.filename,
                                             self.installDialog.installingLabel,
                                             self.installDialog.installationProgress,
                                             self.installDialog.konsole, self.installDialog)
        dprogress.commit()
        #self.label_action.set_markup("<b><big>"+_("Package installed")+"</big></b>")
        # show the button
        #self.button_deb_install_close.set_sensitive(True)
        #self.button_deb_install_close.grab_default()
        self.installDialog.setWindowTitle(_("Installation finished"))
        if dprogress.exitstatus == 0:
            self.installDialog.installingLabel.setText(_("<b>" + "Package '%s' was installed" + "</b>") % self._deb.pkgname)
        else:
            self.installDialog.installingLabel.setText("<b>"+_("Failed to install package '%s'") % os.path.basename(self._deb.filename)+"</b>")
            self.installDialog.konsoleFrame.show()
        #self.statusbar_main.push(self.context,_("Installation complete"))
        # FIXME: Doesn't stop notifying
        #self.window_main.set_property("urgency-hint", 1)

        # reopen the cache, reread the file, FIXME: add progress reporting
        #self._cache = Cache(self.cprogress)
        self._cache = Cache()
        if self._cache._depcache.broken_count > 0:
            header = _("Failed to completely install all dependencies")
            text = _("To fix this run 'sudo apt-get install -f' in a "
                         "terminal window.")
            self.errorReport = KMessageBox.error(self, header + text, header)
            sys.exit(1)

    def downloadButtonClicked(self):
        if self.download_package():
            self.downloadButton.hide()

class GDebiKDEInstall(QDialog):
    def __init__(self, parent):
        QDialog.__init__(self, parent)
        loadUi("GDebiKDEInstallDialog.ui", self)
        self.setWindowTitle(_("Installing"))
        self.showDetailsButton.setText(__("libept","Show Details")) #FIXME check i18n
        self.closeButton.setText(__("kdelibs","&Close"))
        self.showDetailsButton.setIcon(KIcon("utilities-terminal"))
        self.closeButton.setIcon(KIcon("window-close"))
        self.closeButton.setEnabled(False)
        self.closeButton.setVisible(False)
        self.parent = parent
        self.konsole = None
        self.konsoleFrameLayout = QHBoxLayout(self.konsoleFrame)
        self.konsoleFrame.hide()
        self.newKonsole()
        kapp = KApplication.kApplication()
        kapp.processEvents()

    def newKonsole(self):
        self.konsole = DumbTerminal(self.konsoleFrame)
        self.konsoleFrame.setMinimumSize(500, 400)
        self.konsoleFrameLayout.addWidget(self.konsole)

    def showTerminal(self):
        if self.konsoleFrame.isVisible():
            self.konsoleFrame.hide()
            self.showDetailsButton.setText(__("libept","Show Details"))
            QTimer.singleShot(1, self.changeSize)
        else:
            self.konsoleFrame.show()
            self.showDetailsButton.setText(__("libept","Hide Details"))

    def changeSize(self):
        self.resize(self.minimumSize())

    def closeButtonClicked(self):
        self.close()

    def closeEvent(self, event):
        self.close()

    def close(self):
        self.accept()
        KApplication.kApplication().exit()
