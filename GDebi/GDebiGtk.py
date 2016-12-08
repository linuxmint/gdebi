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
import sys
import time
import tempfile
import threading
# py3 compat
try:
    from urllib import url2pathname
    url2pathname  # pyflakes
except ImportError:
    from urllib.request import url2pathname

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gtk
from gi.repository import GObject
from gi.repository import GLib
from gi.repository import Gdk
from gi.repository import Pango
from gi.repository import Vte
from gi.repository import Gio

from apt.progress.base import InstallProgress
from gettext import gettext as _

from .DebPackage import DebPackage
from .SimpleGtkbuilderApp import SimpleGtkbuilderApp
from .GDebiCommon import GDebiCommon, utf8

# the timeout when the termial is expanded if no activity from dpkg
# is happening
GDEBI_TERMINAL_TIMEOUT=4*60.0

# HACK - there are two ubuntu specific patches, one for VTE, one
#        for gksu
UBUNTU=False
try:
    import lsb_release
    UBUNTU = lsb_release.get_distro_information()['ID'] == 'Ubuntu'
except Exception as e:
    pass

class GDebiGtk(SimpleGtkbuilderApp, GDebiCommon):

    def __init__(self, datadir, options, file=""):
        GDebiCommon.__init__(self,datadir, options, file)

        SimpleGtkbuilderApp.__init__(
            self, path=os.path.join(datadir, "gdebi.ui"), domain="gdebi")

        # use a nicer default icon
        icons = Gtk.IconTheme.get_default()
        try:
          logo=icons.load_icon("gnome-mime-application-x-deb", 48, 0)
          if logo != "":
            Gtk.Window.set_default_icon_list([logo])
        except Exception as e:
          logging.warning("Error loading logo %s" % e)

        # create terminal
        self.vte_terminal = Vte.Terminal()
        # FIXME: this sucks but without it the terminal window is only
        #        1 line height
        self.vte_terminal.set_size_request(80*10, 25*10)
        menu = Gtk.Menu()
        menu_items = Gtk.MenuItem(label=_("Copy selected text"))
        menu.append(menu_items)
        menu_items.connect("activate", self.menu_action, self.vte_terminal)
        menu_items.show()
        self.vte_terminal.connect_object("event", self.vte_event, menu)
        self.hbox_install_terminal.pack_start(self.vte_terminal, True, True, 0)
        scrollbar = Gtk.VScrollbar(adjustment=self.vte_terminal.get_vadjustment())
        self.hbox_install_terminal.pack_start(scrollbar, False, False, 0)

        # setup status
        self.context=self.statusbar_main.get_context_id("context_main_window")
        self.statusbar_main.push(self.context,_("Loading..."))

        # show what we have
        self.window_main.realize()
        self.window_main.show()

        # setup drag'n'drop
        # FIXME: this seems to have no effect, droping in nautilus does nothing
        target_entry = Gtk.TargetEntry().new('text/uri-list',0,0)
        self.window_main.drag_dest_set(Gtk.DestDefaults.ALL,
                                       [target_entry],
                                       Gdk.DragAction.COPY)
        self.window_main.connect("drag_data_received",
                                 self.on_window_main_drag_data_received)

        # Check file with gio
        file = self.gio_copy_in_place(file)

        #self.vte_terminal.set_font_from_string("monospace 10")
        self.cprogress = self.CacheProgressAdapter(self.progressbar_cache)
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

        # empty config
        self.synaptic_config = apt_pkg.Configuration()

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
        self.window_main.set_title(_("Package Installer - %s") %
                                   self._deb.pkgname)

        # set name and ungrey some widgets
        self.label_name.set_markup(self._deb.pkgname)
        self.notebook_details.set_sensitive(True)
        self.hbox_main.set_sensitive(True)

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
        except KeyError:
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
        self.textview_lintian_output.modify_font(font_desc)

        # run lintian async
        if self._options and self._options.non_interactive is False:
            self._run_lintian(filename)

        # check the deps
        if not self._deb.check():
            self.label_status.set_markup(
                "<span foreground=\"red\" weight=\"bold\">"+
                _("Error: ") +
                #glib.markup_escape_text(self._deb._failure_string) +
                self._deb._failure_string +
                "</span>")
            self.button_install.set_label(_("_Install Package"))

            self.button_install.set_sensitive(False)
            self.button_details.hide()
            return

        # check provides
        provides = self.compareProvides()
        if provides:
            self.label_status.set_markup(
                "<span foreground=\"red\" weight=\"bold\">"+
                _("Error: no longer provides ") + ", ".join(provides) +
                "</span>")
            return

        # set version_info_{msg,title} strings
        self.compareDebWithCache()
        self.get_changes()

        version_status = self._deb.compare_to_version_in_cache(use_installed=False)
        if (version_status in (DebPackage.VERSION_SAME, DebPackage.VERSION_OUTDATED)):
            if (self._deb.pkgname in self._cache
                and self._cache[self._deb.pkgname].candidate.downloadable
                and not self._deb.downloaded):
                self.button_download.show()
                self.button_download.set_sensitive(True)

        if self._deb.compare_to_version_in_cache() == DebPackage.VERSION_SAME:
            self.label_status.set_text(_("Same version is already installed"))
            self.button_install.set_label(_("_Reinstall Package"))
            self.button_install.grab_default()
            self.button_install.set_sensitive(True)
            self.button_remove.show()
            self.button_remove.set_sensitive(True)
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
        else:
            self.button_details.show()

        self.label_status.set_markup(self.deps)
        #img = Gtk.Image()
        #img.set_from_stock(Gtk.STOCK_APPLY,Gtk.IconSize.BUTTON)
        #self.button_install.set_image(img)
        self.button_install.set_label(_("_Install Package"))
        self.button_install.set_sensitive(True)
        self.button_install.grab_default()
        self.button_remove.hide()

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

    def on_refresh_activate(self, widget):
        #print "refresh"
        self.window_main.set_sensitive(False)
        self.openCache()
        if self._deb:
            self.open(self._deb.filename)
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

    def on_about_activate(self, widget):
        #print "about"
        from .Version import VERSION
        self.dialog_about.set_version(VERSION)
        self.dialog_about.run()
        self.dialog_about.hide()

    def dpkg_action(self, widget, install):
        if not install:
            self.openCache()
            if not self._deb.pkgname in self._cache:
                return
            self._cache[self._deb.pkgname].mark_delete()
            if self._cache.delete_count > 1:
                details = set()
                for package in self._cache.get_changes():
                    if package.shortname != self._deb.pkgname:
                        if package.marked_delete:
                            details.add(package.shortname)
                self.error_header = _("Dependency problems")
                self.error_body = _("One or more packages are required by %s, "
                                    "it cannot be removed.") % self._deb.pkgname
                self.show_alert(Gtk.MessageType.ERROR, self.error_header,
                                self.error_body, "\n".join(details))
                return
        self.action_completed=False
        # check if we actually have a deb, see #213725
        if install and not self._deb:
            err_header = _("File not found")
            err_body = _("You tried to install a file that does not "
                         "(or no longer) exist. ")
            dia = Gtk.MessageDialog(None, 0, Gtk.MessageType.ERROR,
                                    Gtk.ButtonsType.OK, "")
            dia.set_markup("<b><big>%s</big></b>" % err_header)
            dia.format_secondary_text(err_body)
            dia.run()
            dia.destroy()
            return
        # do it
        if install:
            msgstring = _("Installing package file...")
        else:
            msgstring = _("Removing package...")
        self.statusbar_main.push(self.context, msgstring)
        if install and widget != None and len(self.unauthenticated) > 0:
            primary = _("Install unauthenticated software?")
            secondary = _("Malicious software can damage your data "
                          "and take control of your system.\n\n"
                          "The packages below are not authenticated and "
                          "could therefore be of malicious nature.")
            msg = "<big><b>%s</b></big>\n\n%s" % (primary, secondary)
            dialog = Gtk.MessageDialog(parent=self.dialog_deb_install,
                                       flags=Gtk.DialogFlags.MODAL,
                                       type=Gtk.MessageType.WARNING,
                                       buttons=Gtk.ButtonsType.YES_NO)
            dialog.set_markup(msg)
            dialog.set_border_width(6)
            scrolled = Gtk.ScrolledWindow()
            textview = Gtk.TextView()
            textview.set_cursor_visible(False)
            textview.set_editable(False)
            buf = textview.get_buffer()
            buf.set_text("\n".join(self.unauthenticated))
            scrolled.add(textview)
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.show()
            dialog.get_content_area().pack_start(scrolled, True, True, 0)
            textview.show()
            res = dialog.run()
            dialog.destroy()
            if res != Gtk.ResponseType.YES:
                return

        if install:
            msg_hdr = _("You need to grant administrative rights to install software")
            msg_bdy = _("""
It is a possible security risk to install packages files manually.
Install software from trustworthy software distributors only.
""")
        else:
            msg_hdr = _("You need to grant administrative rights to remove software")
            msg_bdy = _("It is a possible risk to remove packages.")
        if os.getuid() != 0:

            # build command and argument lists
            gksu_cmd = "/usr/bin/gksu"
            gksu_args = ["gksu", "--desktop",
                         "/usr/share/applications/gdebi.desktop",
                         "--message",
                         "<big><b>%s</b></big>\n\n%s" % (msg_hdr,msg_bdy)]
            gdebi_args = ["--", "gdebi-gtk", "--non-interactive",
                          self._deb.filename]
            if not install:
                gdebi_args.append("--remove")
            # check if we run on ubuntu and always ask for the password
            # there - we would like to do that on debian too, but this
            # gksu patch is only available on ubuntu currently unfortunately
            if UBUNTU:
                    gksu_args.append("--always-ask-pass")
            os.execv(gksu_cmd, gksu_args+gdebi_args)

        if not self.try_acquire_lock():
            if install:
                msgstring = _("Failed to install package file")
            else:
                msgstring = _("Failed to remove package")
            self.statusbar_main.push(self.context, msgstring)
            self.show_alert(Gtk.MessageType.ERROR, self.error_header, self.error_body)
            return False

        # lock for install
        self.window_main.set_sensitive(False)
        self.button_deb_install_close.set_sensitive(False)
        # clear terminal
        #self.vte_terminal.feed(str(0x1b)+"[2J")

        # Get whether we auto close from synaptic's config file and
        # update the toggle button as neccessary
        config = apt_pkg.Configuration()
        if os.path.isfile("/root/.synaptic/synaptic.conf"):
            apt_pkg.read_config_file(config, "/root/.synaptic/synaptic.conf")
        else:
            config["Synaptic::closeZvt"] = "false"
        if not "Synaptic" in config.list():
            config["Synaptic::closeZvt"] = "false"
        self.synaptic_config = config.subtree("Synaptic")
        self.checkbutton_autoclose.set_active(self.synaptic_config.find_b("closeZvt"))

        self.dialog_deb_install.set_transient_for(self.window_main)
        self.dialog_deb_install.show_all()

        if install and (len(self.install) > 0 or len(self.remove) > 0):
            # FIXME: use the new python-apt acquire interface here,
            # or rather use it in the apt module and raise exception
            # when stuff goes wrong!
            if not self.acquire_lock():
              self.show_alert(Gtk.MessageType.ERROR, self.error_header, self.error_body)
              return False
            fprogress = self.FetchProgressAdapter(self.progressbar_install,
                                                self.label_action,
                                                self.dialog_deb_install)
            iprogress = self.InstallProgressAdapter(self.progressbar_install,
                                                    self.vte_terminal,
                                                    self.label_action,
                                                    self.expander_install)
            try:
                res = self._cache.commit(fprogress,iprogress)
            except IOError as e:
                res = False
                msg = e
                header = _("Could not download all required files")
                body = _("Please check your internet connection or "
                        "installation medium, and make sure your "
                        "APT cache is up-to-date.")
            except SystemError as e:
                res = False
                msg = e
                header = _("Could not install all dependencies"),
                body = _("Usually this is related to an error of the "
                        "software distributor. See the terminal window for "
                        "more details.")
            if not res:
                self.show_alert(Gtk.MessageType.ERROR, header, body, msg,
                                parent=self.dialog_deb_install)

                self.label_install_status.set_markup("<span foreground=\"red\" weight=\"bold\">%s</span>" % header)
                self.button_deb_install_close.set_sensitive(True)
                self.button_deb_install_close.grab_default()
                self.statusbar_main.push(self.context,_("Failed to install package file"))
                return

        # install the package itself
        self.dialog_deb_install.set_title(self.window_main.get_title())
        if install:
            self.label_action.set_markup("<b><big>" + _("Installing %s") %
                                         self._deb.pkgname + "</big></b>")
        else:
            self.label_action.set_markup("<b><big>" + _("Removing %s") %
                                         self._deb.pkgname + "</big></b>")
        if install:
            debfilename = self._deb.filename
        else:
            debfilename = self._deb.pkgname
        dprogress = self.DpkgActionProgress(debfilename,
                                            self.label_install_status,
                                            self.progressbar_install,
                                            self.vte_terminal,
                                            self.expander_install,
                                            install)
        dprogress.commit()
        self.action_completed=True
        #self.label_action.set_markup("<b><big>"+_("Package installed")+"</big></b>")
        # show the button
        self.button_deb_install_close.set_sensitive(True)
        self.button_deb_install_close.grab_default()
        #Close if checkbox is selected
        if self.checkbutton_autoclose.get_active():
            self.on_button_deb_install_close_clicked(None)
        if install:
            self.label_action.set_markup("<b><big>"+_("Installation finished")+"</big></b>")
        else:
            self.label_action.set_markup("<b><big>"+_("Removal finished")+"</big></b>")
        if dprogress.exitstatus == 0:
            if install:
                self.label_install_status.set_markup("<i>"+_("Package '%s' was installed") % os.path.basename(self._deb.filename)+"</i>")
            else:
                self.label_install_status.set_markup("<i>"+_("Package '%s' was removed") % os.path.basename(self._deb.pkgname)+"</i>")
        else:
            if install:
                self.label_install_status.set_markup("<b>"+_("Failed to install package '%s'") %
                                                     os.path.basename(self._deb.filename)+"</b>")
            else:
                self.label_install_status.set_markup("<b>"+_("Failed to remove package '%s'") %
                                                    os.path.basename(self._deb.pkgname)+"</b>")
            self.expander_install.set_expanded(True)
        if install:
            self.statusbar_main.push(self.context,_("Installation complete"))
        else:
            self.statusbar_main.push(self.context,_("Removal complete"))
        # FIXME: Doesn't stop notifying
        #self.window_main.set_property("urgency-hint", 1)

        # reopen the cache, reread the file
        self.openCache()
        if self._cache._depcache.broken_count > 0:
            if install:
                err_header = _("Failed to completely install all dependencies")
            else:
                err_header = _("Failed to completely remove package")
            err_body = _("To fix this run 'sudo apt-get install -f' in a "
                         "terminal window.")
            self.show_alert(Gtk.MessageType.ERROR, err_header, err_body)
        self.open(self._deb.filename)

    def on_button_install_clicked(self, widget):
        self.dpkg_action(widget, True)

    def on_button_download_clicked(self, widget):
        if self.download_package():
            self.window_main.get_window().set_cursor(None)
            self.button_download.hide()

    def on_button_remove_clicked(self, widget):
        self.dpkg_action(widget, False)

    def on_button_deb_install_close_clicked(self, widget):
        # Set the autoclose option when we close
        autoclose = self.checkbutton_autoclose.get_active()
        self.synaptic_config["closeZvt"] = str(autoclose).lower()
        self.write_synaptic_config_file(self.synaptic_config,
                                        "/root/.synaptic/synaptic.conf")
        # FIXME: doesn't turn it off
        #self.window_main.set_property("urgency-hint", 0)
        if hasattr(self, "_gio_cancellable"):
            self._gio_cancellable.cancel()
        self.dialog_deb_install.hide()
        self.window_main.set_sensitive(True)

    def on_checkbutton_autoclose_clicked(self, widget):
        if self.action_completed:
            self.on_button_deb_install_close_clicked(None)

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

    def write_synaptic_config_file(self, config, path):
        if not os.path.exists(path):
            return
        config_file = open(path, "w")
        config_file.write(config.dump())
        config_file.close()

    def vte_event(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            if event.button.button == 3:
                widget.popup_for_device(None, None, None, None, None,
                                        event.button.button, event.time)
                return True
        return False

    def menu_action(self, widget, terminal):
        terminal.copy_clipboard()

    # embedded classes
    class DpkgActionProgress(object):
        def __init__(self, debfile, status, progress, term, expander, install=True):
            self.debfile = debfile
            self.status = status
            self.progress = progress
            self.term = term
            self.term_expander = expander
            self.time_last_update = time.time()
            self.term_expander.set_expanded(False)
            self.install = install
        def commit(self):
            def finish_dpkg(term, status, lock):
                """ helper that is run when dpkg finishes """
                self.exitstatus = posix.WEXITSTATUS(status)
                #print "dpkg finished %s %s" % (pid,status)
                #print "exit status: %s" % self.exitstatus
                #print "was signaled %s" % posix.WIFSIGNALED(status)
                try:
                    lock.release()
                except:
                    logging.exception("lock.release failed")

            # get a lock
            lock = threading.Lock()
            lock.acquire()

            # ui
            if self.install:
                self.status.set_markup("<i>"+_("Installing '%s'...") % \
                                       os.path.basename(self.debfile)+"</i>")
            else:
                self.status.set_markup("<i>"+_("Removing '%s'...") % \
                                       os.path.basename(self.debfile)+"</i>")

            self.progress.pulse()
            self.progress.set_text("")

            # prepare reading the pipe
            (readfd, writefd) = os.pipe()
            # File descriptors are not inheritable by child processes by
            # default in Python 3.4.  We need the dpkg child to inherit the
            # write file descriptor.
            if hasattr(os, 'set_inheritable'):
                os.set_inheritable(writefd, True)
            fcntl.fcntl(readfd, fcntl.F_SETFL,os.O_NONBLOCK)
            #print("fds (%i,%i)" % (readfd,writefd))

            # the command
            argv = ["/usr/bin/dpkg", "--auto-deconfigure"]
            # ubuntu supports VTE_PTY_KEEP_FD, see
            # https://bugzilla.gnome.org/320128 for the upstream bug
            if UBUNTU:
                argv += ["--status-fd", "%s"%writefd]
            if self.install:
                argv += ["-i", self.debfile]
            else:
                argv += ["-r", self.debfile]
            env = ["VTE_PTY_KEEP_FD=%s"% writefd,
                   "DEBIAN_FRONTEND=gnome",
                   "APT_LISTCHANGES_FRONTEND=gtk"]
            #print argv
            #print env
            #print self.term

            # prepare for the fork
            self.term.connect("child-exited", finish_dpkg, lock)
            (res, pid) =self.term.spawn_sync(
                Vte.PtyFlags.DEFAULT,
                "/",
                argv,
                env,
                GLib.SpawnFlags.LEAVE_DESCRIPTORS_OPEN,
                # FIXME: add setup_func that closes all fds excpet for writefd
                None, #setup_func
                None, #setup_data
                None, #cancellable
                )
            #print "fork_command_full: ", res, pid

            raw_read = b""
            while lock.locked():
                while True:
                    try:
                        raw_read += os.read(readfd,1)
                    except OSError as e:
                        # resource temporarly unavailable is ignored
                        from errno import EAGAIN
                        if e.errno != EAGAIN:
                            logging.warning(e.errstr)
                        break
                    self.time_last_update = time.time()
                    if raw_read[-1] == ord("\n"):
                        read = raw_read.decode("utf-8")
                        statusl = read.split(":")
                        if len(statusl) < 3:
                            logging.warning("got garbage from dpkg: '%s'" % read)
                            raw_read = ""
                            break
                        status = statusl[2].strip()
                        #print status
                        if status == "error" or status == "conffile-prompt":
                            self.term_expander.set_expanded(True)
                        raw_read = b""
                self.progress.pulse()
                while Gtk.events_pending():
                    Gtk.main_iteration()
                time.sleep(0.2)
                # if the terminal has not reacted for some time, do something
                if (not self.term_expander.get_expanded() and
                    (self.time_last_update + GDEBI_TERMINAL_TIMEOUT) < time.time()):
                  self.term_expander.set_expanded(True)
            self.progress.set_fraction(1.0)

    class InstallProgressAdapter(InstallProgress):
        def __init__(self,progress,term,label,term_expander):
            InstallProgress.__init__(self)
            self.progress = progress
            self.term = term
            self.term_expander = term_expander
            self.finished = False
            self.action = label
            self.time_last_update = time.time()
            self.term.connect("child-exited", self.child_exited)
            self.env = ["VTE_PTY_KEEP_FD=%s"% self.writefd,
                        "DEBIAN_FRONTEND=gnome",
                        "APT_LISTCHANGES_FRONTEND=gtk"]
        def child_exited(self, term, status):
            #print "apt finished %s" % status
            #print "exit status: %s" % posix.WEXITSTATUS(status)
            #print "was signaled %s" % posix.WIFSIGNALED(status)
            self.apt_status = status
            self.finished = True
        def error(self, pkg, errormsg):
            # FIXME: display a msg
            self.term_expander.set_expanded(True)
        def conffile(self, current, new):
            # FIXME: display a msg or expand term
            self.term_expander.set_expanded(True)
        def start_update(self):
            #print "startUpdate"
            apt_pkg.pkgsystem_unlock()
            self.action.set_markup("<i>"+_("Installing dependencies...")+"</i>")
            self.progress.set_fraction(0.0)
            self.progress.set_text("")
        def status_change(self, pkg, percent, status):
            #print "status change", pkg, percent, status
            self.progress.set_fraction(percent/100.0)
            self.progress.set_text(status)
            self.time_last_update = time.time()
        def update_interface(self):
            InstallProgress.update_interface(self)
            while Gtk.events_pending():
                Gtk.main_iteration()
            if (not self.term_expander.get_expanded() and
                (self.time_last_update + GDEBI_TERMINAL_TIMEOUT) < time.time()):
              self.term_expander.set_expanded(True)
            # sleep just long enough to not create a busy loop
            time.sleep(0.01)
        def fork(self):
            pty = Vte.Pty.new_sync(Vte.PtyFlags.DEFAULT)
            pid = os.fork()
            if pid == 0:
                # *grumpf* workaround bug in vte here (gnome bug #588871)
                for env in self.env:
                    (key, value) = env.split("=")
                    os.environ[key] = value
                # MUST be called
                pty.child_setup()
                # FIXME: close all fds expect for self.writefd
            else:
                self.term.set_pty(pty)
                self.term.watch_child(pid)
            return pid
        def wait_child(self):
            while not self.finished:
                self.update_interface()
            return self.apt_status

    class FetchProgressAdapter(apt.progress.base.AcquireProgress):
        def __init__(self,progress,action,main):
            super(GDebiGtk.FetchProgressAdapter, self).__init__()
            self.progress = progress
            self.action = action
            self.main = main
        def start(self):
            super(GDebiGtk.FetchProgressAdapter, self).start()
            self.action.set_markup("<i>"+_("Downloading additional package files...")+"</i>")
            self.progress.set_fraction(0)
        def stop(self):
            #print "stop()"
            pass
        def pulse(self, owner):
            super(GDebiGtk.FetchProgressAdapter, self).pulse(owner)
            at_item = min(self.current_items + 1, self.total_items)
            if self.current_cps > 0:
                self.progress.set_text(_("File %s of %s at %sB/s") % (at_item,self.total_items,apt_pkg.size_to_str(self.current_cps)))
            else:
                self.progress.set_text(_("File %s of %s") % (at_item,self.total_items))
            self.progress.set_fraction(self.current_bytes/self.total_bytes)
            while Gtk.events_pending():
                Gtk.main_iteration()
            return True
        def media_change(self, medium, drive):
            #print "mediaChange %s %s" % (medium, drive)
            msg = _("Please insert '%s' into the drive '%s'" % (medium,drive))
            dialog = Gtk.MessageDialog(parent=self.main,
                                       flags=Gtk.DialogFlags.MODAL,
                                       type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.OK_CANCEL)
            dialog.set_markup(msg)
            res = dialog.run()
            #print res
            dialog.destroy()
            if  res == Gtk.ResponseType.OK:
                return True
            return False

    class CacheProgressAdapter(apt.progress.base.OpProgress):
        def __init__(self, progressbar):
            self.progressbar = progressbar
        def update(self, percent=None):
            self.progressbar.show()
            if percent:
                self.progressbar.set_fraction(percent/100.0)
            #self.progressbar.set_text(self.op)
            while Gtk.events_pending():
                Gtk.main_iteration()
        def done(self):
            self.progressbar.hide()


if __name__ == "__main__":
    app = GDebiGtk("data/",None)

    pkgs = ["cw"]
    for pkg in pkgs:
        print("installing %s" % pkg)
        app._cache[pkg].mark_install()

    for pkg in app._cache:
        if pkg.marked_install or pkg.marked_upgrade:
            print(pkg.name)

    apt_pkg.pkgsystem_lock()
    app.dialog_deb_install.set_transient_for(app.window_main)
    app.dialog_deb_install.show_all()

    # install the dependecnies
    fprogress = app.FetchProgressAdapter(app.progressbar_install,
                                         app.label_action,
                                         app.dialog_deb_install)
    iprogress = app.InstallProgressAdapter(app.progressbar_install,
                                           app.vte_terminal,
                                           app.label_action,
                                           app.expander_install)
    res = app._cache.commit(fprogress,iprogress)
    print("commit retured: %s" % res)
    Gtk.main()
