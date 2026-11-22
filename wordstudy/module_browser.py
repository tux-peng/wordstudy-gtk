"""GTK 3 browser for modules from the MyBible Android registry."""
from __future__ import annotations
import threading
from gi.repository import GLib, Gtk
from .module_downloader import REGISTRY_URL, fetch_catalog, install_module

class ModuleBrowser(Gtk.Dialog):
    def __init__(self, parent):
        super().__init__(title="Download MyBible Modules", transient_for=parent)
        self.set_default_size(900, 600)
        self.add_button("Close", Gtk.ResponseType.CLOSE)
        self.connect("response", lambda *_: self.destroy())
        self.parent_window = parent
        self.catalog = []
        self.busy = False
        self.closed = False
        self.connect("destroy", lambda *_: setattr(self, "closed", True))
        area = self.get_content_area()
        area.set_spacing(8)
        area.set_border_width(12)
        info = Gtk.Label(label="Android MyBible catalog • Bibles, commentaries and dictionaries supported", xalign=0)
        area.pack_start(info, False, False, 0)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        area.pack_start(top, False, False, 0)
        self.url = Gtk.Entry()
        self.url.set_text(REGISTRY_URL)
        self.url.set_hexpand(True)
        top.pack_start(self.url, True, True, 0)
        self.refresh_button = Gtk.Button.new_with_label("Refresh catalog")
        self.refresh_button.connect("clicked", self.refresh)
        top.pack_start(self.refresh_button, False, False, 0)
        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        area.pack_start(filters, False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search name or abbreviation")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self.apply_filter)
        filters.pack_start(self.search, True, True, 0)
        self.kind = Gtk.ComboBoxText()
        for name in ("All types", "Bible", "Commentary", "Dictionary"):
            self.kind.append_text(name)
        self.kind.set_active(0)
        self.kind.connect("changed", self.apply_filter)
        filters.pack_start(self.kind, False, False, 0)
        self.language = Gtk.ComboBoxText()
        self.language.append_text("All languages")
        self.language.set_active(0)
        self.language.connect("changed", self.apply_filter)
        filters.pack_start(self.language, False, False, 0)
        self.store = Gtk.ListStore(str, str, str, str, str, str, int)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self.visible)
        self.view = Gtk.TreeView(model=self.filtered)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for i, name in enumerate(("Abbr.", "Description", "Language", "Type", "Updated", "Size")):
            cell = Gtk.CellRendererText()
            col = Gtk.TreeViewColumn(name, cell, text=i)
            col.set_resizable(True)
            self.view.append_column(col)
        scroll = Gtk.ScrolledWindow()
        scroll.add(self.view)
        area.pack_start(scroll, True, True, 0)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        area.pack_start(bottom, False, False, 0)
        self.status = Gtk.Label(label="Loading…", xalign=0)
        self.status.set_hexpand(True)
        self.status.set_line_wrap(True)
        bottom.pack_start(self.status, True, True, 0)
        self.download_button = Gtk.Button.new_with_label("Download selected")
        self.download_button.connect("clicked", self.download)
        bottom.pack_end(self.download_button, False, False, 0)
        self.show_all()
        self.refresh()

    def set_busy(self, value):
        self.busy = value
        self.refresh_button.set_sensitive(not value)
        self.download_button.set_sensitive(not value)
        self.url.set_sensitive(not value)

    def refresh(self, *_):
        if self.busy:
            return
        url = self.url.get_text().strip()
        self.set_busy(True)
        self.status.set_text("Fetching registry…")
        def worker():
            try:
                result = fetch_catalog(url)
                GLib.idle_add(self.catalog_ready, result, None)
            except Exception as exc:
                GLib.idle_add(self.catalog_ready, None, str(exc))
        threading.Thread(target=worker, daemon=True).start()

    def catalog_ready(self, catalog, error):
        if self.closed:
            return False
        self.set_busy(False)
        if error:
            self.status.set_text("Catalog error: " + error)
            return False
        self.catalog = catalog
        self.store.clear()
        self.language.handler_block_by_func(self.apply_filter)
        self.language.remove_all()
        self.language.append_text("All languages")
        for language in sorted({m.language for m in catalog if m.language}):
            self.language.append_text(language)
        self.language.set_active(0)
        self.language.handler_unblock_by_func(self.apply_filter)
        for i, item in enumerate(catalog):
            self.store.append((item.abbreviation, item.description, item.language,
                               item.kind, item.updated, item.size, i))
        self.apply_filter()
        self.status.set_text(f"{len(catalog)} supported modules available")
        return False

    def visible(self, model, iterator, _data=None):
        query = self.search.get_text().strip().casefold()
        kind = self.kind.get_active_text()
        language = self.language.get_active_text()
        return ((not query or query in (model[iterator][0] + " " + model[iterator][1]).casefold())
                and (kind == "All types" or model[iterator][3] == kind)
                and (language == "All languages" or model[iterator][2] == language))

    def apply_filter(self, *_):
        self.filtered.refilter()

    def download(self, *_):
        if self.busy:
            return
        model, paths = self.view.get_selection().get_selected_rows()
        selected = [self.catalog[model[path][6]] for path in paths]
        if not selected:
            self.status.set_text("Select a module first.")
            return
        self.set_busy(True)
        def worker():
            installed, errors = [], []
            for i, item in enumerate(selected, 1):
                GLib.idle_add(self.progress, f"Downloading {i}/{len(selected)}: {item.abbreviation}")
                try:
                    installed.extend(install_module(item, self.parent_window.library))
                except Exception as exc:
                    errors.append(f"{item.abbreviation}: {exc}")
            GLib.idle_add(self.finished, installed, errors)
        threading.Thread(target=worker, daemon=True).start()

    def progress(self, text):
        if not self.closed:
            self.status.set_text(text)
        return False

    def finished(self, installed, errors):
        if self.closed:
            return False
        self.set_busy(False)
        if installed:
            self.parent_window._populate_modules()
            if self.parent_window.current_bible:
                self.parent_window.navigate(self.parent_window._first_available_ref(self.parent_window.current_bible))
        message = f"Installed {len(installed)} module(s)."
        if errors:
            message += " Errors: " + "; ".join(errors[:3])
        self.status.set_text(message)
        self.parent_window._set_status(message)
        return False
