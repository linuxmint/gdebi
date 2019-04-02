# Copyright (c) 2005-2011 Canonical Ltd
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
import apt_pkg
import fcntl
import logging
import os
import posix
import re
import subprocess
import sys
import time
import tempfile
import threading

import mintcommon.aptdaemon

# py3 compat
try:
    from urllib import url2pathname
    url2pathname  # pyflakes
except ImportError:
    from urllib.request import url2pathname

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk
from gi.repository import GObject
from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Pango
from gi.repository import Gio

from gettext import gettext as _

from .DebPackage import DebPackage
from .SimpleGtkbuilderApp import SimpleGtkbuilderApp
from .GDebiCommon import GDebiCommon, utf8

class GDebiGtk(SimpleGtkbuilderApp, GDebiCommon):

    def __init__(self, datadir, options, file=""):
        GDebiCommon.__init__(self,datadir, options, file)

        SimpleGtkbuilderApp.__init__(self, path=os.path.join(datadir, "gdebi.ui"), domain="gdebi")

        # setup status
        self.context=self.statusbar_main.get_context_id("context_main_window")
        self.statusbar_main.push(self.context,_("Loading..."))

        # show what we have
        self.window_main.realize()
        self.window_main.show()

        # setup drag'n'drop
        # FIXME: this seems to have no effect, droping in nautilus does nothing
        target_entry = Gtk.TargetEntry().new('text/uri-list',0,0)
        self.window_main.drag_dest_set(Gtk.DestDefaults.ALL, [target_entry], Gdk.DragAction.COPY)
        self.window_main.connect("drag_data_received", self.on_window_main_drag_data_received)

        # Check file with gio
        file = self.gio_copy_in_place(file)

        if not self.openCache():
            self.show_alert(Gtk.MessageType.ERROR, self.error_header, self.error_body)
            sys.exit(1)
        self.statusbar_main.push(self.context, "")

        # setup the details treeview
        self.details_list = Gtk.ListStore(GObject.TYPE_STRING)
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "markup", 0)
        self.treeview_details.append_column(column)
        self.treeview_details.set_model(self.details_list)

        # setup the files treeview
        column = Gtk.TreeViewColumn("")
        render = Gtk.CellRendererText()
        column.pack_start(render, True)
        column.add_attribute(render, "text", 0)
        self.treeview_files.append_column(column)

        if file != "" and os.path.exists(file):
            self.open(file)

        self.window_main.set_sensitive(True)

    def _show_busy_cursor(self, show_busy_cursor):
        win = self.window_main.get_window()
        if not win:
            return
        if show_busy_cursor:
            win.set_cursor(Gdk.Cursor.new(Gdk.CursorType.WATCH))
            while Gtk.events_pending():
                Gtk.main_iteration()
        else:
            win.set_cursor(None)

    def gio_progress_callback(self, bytes_read, bytes_total, data):
        self.progressbar_download.set_fraction(bytes_read/float(bytes_total))
        while Gtk.events_pending():
            Gtk.main_iteration()

    def on_button_cancel_download_clicked(self, button, cancellable):
        cancellable.cancel()

    def gio_copy_in_place(self, file):
        "helper that copies the file to the local system via gio"
        gio_file = Gio.file_new_for_path(file)
        if (gio_file.get_uri_scheme() == "file"):
            return file
        if (os.getuid()==0):
            self.show_alert(Gtk.MessageType.ERROR,
                            _("Can not download as root"),
                            _("Remote packages can not be downloaded when "
                              "running as root. Please try again as a "
                              "normal user."))
            sys.exit(1)
        # Download the file
        temp_file_name = os.path.join(tempfile.mkdtemp(),os.path.basename(file))
        gio_dest = Gio.file_new_for_path(temp_file_name)
        try:
            # download
            gio_cancellable = Gio.Cancellable()
            self.button_cancel_download.connect("clicked", self.on_button_cancel_download_clicked, gio_cancellable)
            self.dialog_gio_download.set_transient_for(self.window_main)
            self.dialog_gio_download.show()
            self.label_action.set_text(_("Downloading package"))
            if gio_file.copy(gio_dest, 0, gio_cancellable,
                             self.gio_progress_callback, 0):
                file = gio_dest.get_path()
            self.dialog_gio_download.hide()
        except Exception as e:
            self.show_alert(Gtk.MessageType.ERROR,
                            _("Download failed"),
                            _("Downloading the package failed: "
                              "file '%s' '%s'") % (file, e))
            sys.exit(1)
        return file

    def _get_file_path_from_dnd_dropped_uri(self, uri):
        """ helper to get a useful path from a drop uri"""
        path = url2pathname(uri) # escape special chars
        path = path.strip('\r\n\x00') # remove \r\n and NULL
        # get the path to file
        if path.startswith('file:\\\\\\'): # windows
            path = path[8:] # 8 is len('file:///')
        elif path.startswith('file://'): # nautilus, rox
            path = path[7:] # 7 is len('file://')
        elif path.startswith('file:'): # xffm
            path = path[5:] # 5 is len('file:')
        return path

    def on_menuitem_quit_activate(self, widget):
        try:
            Gtk.main_quit()
        except:
            # if we are outside of the main loop, just exit
            sys.exit(0)

    def on_window_main_drag_data_received(self, widget, context, x, y,
                                          selection, target_type, timestamp):
        """ call when we got a drop event """
        uri = selection.data.strip()
        uri_splitted = uri.split() # we may have more than one file dropped
        for uri in uri_splitted:
            path = self._get_file_path_from_dnd_dropped_uri(uri)
            #print 'path to open', path
            if path.endswith(".deb"):
                self.open(path)

    def open(self, filename, downloaded=False):
        self._show_busy_cursor(True)
        res = GDebiCommon.open(self, filename, downloaded)
        self._show_busy_cursor(False)
        if res == False:
            self.show_alert(
                Gtk.MessageType.ERROR, self.error_header, self.error_body)
            return False

        self.statusbar_main.push(self.context, "")

        # set window title
        self.window_main.set_title(_("Package Installer"))

        # set name and ungrey some widgets
        self.label_name.set_markup("<b><big>%s</big></b>" % self._deb.pkgname)
        self.notebook_details.set_sensitive(True)

        # set description
        buf = self.textview_description.get_buffer()
        try:
            long_desc = ""
            raw_desc = utf8(self._deb["Description"]).split("\n")
            # append a newline to the summary in the first line
            summary = raw_desc[0]
            raw_desc[0] = ""
            long_desc = "%s\n" % summary
            for line in raw_desc:
                tmp = line.strip()
                if tmp == ".":
                    long_desc += "\n"
                else:
                    long_desc += tmp + "\n"
            #print long_desc
            # do some regular expression magic on the description
            # Add a newline before each bullet
            p = re.compile(r'^(\s|\t)*(\*|0|-)',re.MULTILINE)
            long_desc = p.sub('\n*', long_desc)
            # replace all newlines by spaces
            p = re.compile(r'\n', re.MULTILINE)
            long_desc = p.sub(" ", long_desc)
            # replace all multiple spaces by
            # newlines
            p = re.compile(r'\s\s+', re.MULTILINE)
            long_desc = p.sub("\n", long_desc)
            # write the descr string to the buffer
            buf.set_text(long_desc)
            # tag the first line with a bold font
            tag = buf.create_tag(None, weight=Pango.Weight.BOLD)
            iter = buf.get_iter_at_offset(0)
            (start, end) = iter.forward_search("\n",
                                               Gtk.TextSearchFlags.TEXT_ONLY,
                                               None)
            buf.apply_tag(tag , iter, end)
        except:
            buf.set_text("No description is available")

        # set various status bits
        self.label_version.set_text(self._deb["Version"])
        self.label_maintainer.set_text(utf8(self._deb["Maintainer"]))
        self.label_priority.set_text(self._deb["Priority"])
        self.label_section.set_text(utf8(self._deb["Section"]))
        self.label_size.set_text(self._deb["Installed-Size"] + " KiB")

        # set file list
        store = Gtk.TreeStore(str)
        try:
            header = store.append(None, [_("Package control data")])
            for name in self._deb.control_filelist:
                store.append(header, [name])
            header = store.append(None, [_("Upstream data")])
            for name in self._deb.filelist:
                store.append(header, [name])
        except Exception as e:
            logging.exception("Exception while reading the filelist: '%s'" % e)
            store.clear()
            store.append(None, [_("Error reading filelist")])
        self.treeview_files.set_model(store)
        self.treeview_files.expand_all()
        # and the file content textview
        font_desc = Pango.FontDescription('monospace')
        self.textview_file_content.modify_font(font_desc)
        # self.textview_lintian_output.modify_font(font_desc)

        # run lintian async
        # if self._options and self._options.non_interactive is False:
        #     self._run_lintian(filename)

        # check the deps
        if not self._deb.check():
            self.label_status.set_markup(_("Error: ") +
                #glib.markup_escape_text(self._deb._failure_string) +
                self._deb._failure_string)
            self.infobar1.set_message_type(Gtk.MessageType.ERROR)
            self.infobar1.show()
            self.button_install.set_label(_("_Install Package"))

            self.button_install.set_sensitive(False)
            self.button_details.hide()
            return

        # check provides
        provides = self.compareProvides()
        if provides:
            self.label_status.set_markup(_("Error: no longer provides ") + ", ".join(provides))
            self.infobar1.set_message_type(Gtk.MessageType.ERROR)
            self.infobar1.show()
            return

        # set version_info_{msg,title} strings
        self.compareDebWithCache()
        self.get_changes()

        # version_status = self._deb.compare_to_version_in_cache(use_installed=False)
        # if (version_status in (DebPackage.VERSION_SAME, DebPackage.VERSION_OUTDATED)):
        #     if (self._deb.pkgname in self._cache
        #         and self._cache[self._deb.pkgname].candidate.downloadable
        #         and not self._deb.downloaded):
        #         self.button_download.show()
        #         self.button_download.set_sensitive(True)

        if self._deb.compare_to_version_in_cache() == DebPackage.VERSION_SAME:
            self.label_status.set_text(_("Same version is already installed"))
            self.infobar1.set_message_type(Gtk.MessageType.INFO)
            self.infobar1.show()
            self.button_install.set_label(_("_Reinstall Package"))
            self.button_install.grab_default()
            self.button_install.set_sensitive(True)
            # self.button_remove.show()
            # self.button_remove.set_sensitive(True)
            self.button_details.hide()
            return

        if self.version_info_title != "" and self.version_info_msg != "":
            msg = "<big><b>%s</b></big>\n\n%s" % (self.version_info_title,
              self.version_info_msg)
            dialog = Gtk.MessageDialog(parent=self.window_main,
                                       flags=Gtk.DialogFlags.MODAL,
                                       type=Gtk.MessageType.INFO,
                                       buttons=Gtk.ButtonsType.CLOSE)
            dialog.set_markup(msg)
            dialog.run()
            dialog.destroy()

        # load changes into (self.install, self.remove, self.unauthenticated)
        if len(self.remove) == len(self.install) == 0:
            self.button_details.hide()
            self.label_status.set_markup(self.deps)
            self.infobar1.set_message_type(Gtk.MessageType.INFO)
            self.infobar1.show()
        else:
            self.button_details.show()
            self.label_status.set_markup(self.deps)
            self.infobar1.set_message_type(Gtk.MessageType.WARNING)
            self.infobar1.show()

        self.button_install.set_label(_("_Install Package"))
        self.button_install.set_sensitive(True)
        self.button_install.grab_default()

    def _run_lintian(self, filename):
        buf = self.textview_lintian_output.get_buffer()
        if not os.path.exists("/usr/bin/lintian"):
            buf.set_text(
                _("No lintian available.\n"
                  "Please install using sudo apt-get install lintian"))
            return
        buf.set_text(_("Running lintian..."))
        self._lintian_output = ""
        self._lintian_exit_status = None
        self._lintian_exit_status_gathered = None
        cmd = ["/usr/bin/lintian", filename]
        (pid, stdin, stdout, stderr) = GLib.spawn_async(
            cmd, flags=GObject.SPAWN_DO_NOT_REAP_CHILD,
            standard_output=True, standard_error=True)
        for fd in [stdout, stderr]:
            channel = GLib.IOChannel(filedes=fd)
            channel.set_flags(GLib.IOFlags.NONBLOCK)
            GLib.io_add_watch(channel, GLib.PRIORITY_DEFAULT,
                              GLib.IOCondition.IN | GLib.IO_ERR | GLib.IO_HUP,
                              self._on_lintian_output)
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, pid,
                             self._on_lintian_finished)

    def _on_lintian_finished(self, pid, condition):
        exit_status = os.WEXITSTATUS(condition)
        self._lintian_exit_status = exit_status
        if not self._lintian_exit_status_gathered:
            self._lintian_exit_status_gathered = True
            text = _("\nLintian finished with exit status %s") % exit_status
            self._lintian_output += text
        buf = self.textview_lintian_output.get_buffer()
        buf.set_text(self._lintian_output)

    def _on_lintian_output(self, gio_file, condition):
        if condition & GLib.IOCondition.IN:
            # we get bytes from gio
            content = gio_file.read().decode("utf-8")
            if content:
                self._lintian_output += content
                buf = self.textview_lintian_output.get_buffer()
                buf.set_text(self._lintian_output)
            return True
        gio_file.close()
        return False

    def on_treeview_files_cursor_changed(self, treeview):
        " the selection in the files list chanaged "
        model = treeview.get_model()
        (path, col) = treeview.get_cursor()
        if not (model and path):
            return
        name = model[path][0]
        # if we are at the top-level, do nothing
        if path.get_depth() < 2:
            return
        # parent path == 0 means we look at the control information
        # parent path == 1 means we look at the data
        parent_path = path.get_indices()[0]
        if name.endswith("/"):
            data = _("Selection is a directory")
        elif parent_path == 0:
            try:
                data = self._deb.control_content(name)
            except Exception as e:
                data = _("Error reading file content '%s'") % e
        elif parent_path == 1:
            self._show_busy_cursor(True)
            try:
                data = self._deb.data_content(name)
            except Exception as e:
                data = _("Error reading file content '%s'") % e
            self._show_busy_cursor(False)
        else:
            assert False, "NOT REACHED"
        if not data:
            data = _("File content can not be extracted")
        buf = self.textview_file_content.get_buffer()
        buf.set_text(data)

    def on_button_details_clicked(self, widget):
        #print "on_button_details_clicked"
        # sanity check
        if not self._deb:
          return
        self.details_list.clear()
        for rm in self.remove:
            self.details_list.append([_("<b>To be removed: %s</b>") % rm])
        for inst in self.install:
            self.details_list.append([_("To be installed: %s") % inst])
        self.dialog_details.set_transient_for(self.window_main)
        self.dialog_details.run()
        self.dialog_details.hide()

    def on_open_activate(self, widget):
        #print "open"
        # build dialog
        self.window_main.set_sensitive(False)
        fs = Gtk.FileChooserDialog(parent=self.window_main,
                                   buttons=(Gtk.STOCK_CANCEL,
                                            Gtk.ResponseType.CANCEL,
                                            Gtk.STOCK_OPEN,
                                            Gtk.ResponseType.OK),
                                   action=Gtk.FileChooserAction.OPEN,
                                   title=_("Open Software Package"))
        fs.set_default_response(Gtk.ResponseType.OK)
        # set filter
        filter = Gtk.FileFilter()
        filter.add_pattern("*.deb")
        filter.set_name(_("Software packages"))
        #fs.add_filter(filter)
        fs.set_filter(filter)
        # run it!
        res = fs.run()
        fs.hide()
        if res == Gtk.ResponseType.OK:
            #print fs.get_filename()
            self.open(fs.get_filename())
        fs.destroy()
        self.window_main.set_sensitive(True)

    def on_copy_activate(self, widget):
        clipboard = Gtk.Clipboard.get(Gdk.atom_intern('CLIPBOARD', True))
        buf = self.textview_description.get_buffer()
        if buf.get_has_selection():
            buf.copy_clipboard(clipboard)
        else:
            (start, end) = buf.get_bounds()
            text = buf.get_text(start, end, True)
            clipboard.set_text(text, -1)

    def on_install_finished(self, transaction=None, exit_state=None):
        self.openCache()
        if self._cache._depcache.broken_count > 0:
            err_header = _("Failed to completely install all dependencies")
            err_body = _("To fix this run 'sudo apt-get install -f' in a terminal window.")
            self.show_alert(Gtk.MessageType.ERROR, err_header, err_body)
        self.open(self._deb.filename)
        self.window_main.set_sensitive(True)

    def dpkg_action(self, widget, install):
        # lock for install
        self.window_main.set_sensitive(False)
        apt = mintcommon.aptdaemon.APT(self.window_main)
        apt.set_finished_callback(self.on_install_finished)
        apt.set_cancelled_callback(self.on_install_finished)
        apt.install_file(self._deb.filename)
        return

    def on_button_install_clicked(self, widget):
        self.dpkg_action(widget, True)

    def on_button_download_clicked(self, widget):
        if self.download_package():
            self.window_main.get_window().set_cursor(None)
            self.button_download.hide()

    def on_window_main_delete_event(self, *args):
        if self.window_main.get_property("sensitive"):
            if Gtk.main_level() > 0:
                Gtk.main_quit()
            return False
        else:
            return True

    def show_alert(self, type, header, body=None, details=None, parent=None):
        if parent is not None:
             self.dialog_hig.set_transient_for(parent)
        else:
             self.dialog_hig.set_transient_for(self.window_main)

        message = "<b><big>%s</big></b>" % header
        if not body == None:
             message = "%s\n\n%s" % (message, body)
        self.label_hig.set_markup(message)

        if not details == None:
             buffer = self.textview_hig.get_buffer()
             buffer.set_text(str(details))
             self.expander_hig.set_expanded(False)
             self.expander_hig.show()

        if type == Gtk.MessageType.ERROR:
             self.image_hig.set_property("stock", "gtk-dialog-error")
        elif type == Gtk.MessageType.WARNING:
             self.image_hig.set_property("stock", "gtk-dialog-warning")
        elif type == Gtk.MessageType.INFO:
             self.image_hig.set_property("stock", "gtk-dialog-info")

        res = self.dialog_hig.run()
        self.dialog_hig.hide()
        if res == Gtk.ResponseType.CLOSE:
            return True
        return False

