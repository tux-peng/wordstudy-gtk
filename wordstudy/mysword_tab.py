"""MySword catalog tab embedded alongside the MyBible downloader."""
from __future__ import annotations
import threading
from gi.repository import GLib, Gtk
from .mysword_downloader import fetch_mysword_catalog, install_mysword

class MySwordTab(Gtk.Box):
    def __init__(self, parent):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.parent_window = parent
        self.catalog = []
        self.loading = False
        self.loaded = False
        self.closed = False
        self.connect("destroy", lambda *_: setattr(self, "closed", True))
        selectors = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(selectors, False, False, 0)
        selectors.pack_start(Gtk.Label(label="Language:"), False, False, 0)
        self.language = Gtk.ComboBoxText.new_with_entry()
        self.language.append("all", "All languages")
        for name in ("English", "Spanish", "French", "German", "Portuguese",
                     "Russian", "Chinese", "Italian", "Dutch", "Arabic",
                     "Hebrew", "Greek", "Hindi", "Indonesian", "Korean",
                     "Japanese", "Polish", "Romanian", "Ukrainian"):
            self.language.append(name.lower(), name)
        self.language.set_entry_text_column(0)
        self.language.get_child().set_placeholder_text("Choose or type a language")
        self.language.connect("changed", self.selection_changed)
        selectors.pack_start(self.language, True, True, 0)
        selectors.pack_start(Gtk.Label(label="Module type:"), False, False, 0)
        self.kind = Gtk.ComboBoxText()
        for code, name in (("bibles", "Bible"), ("books", "Book"),
                           ("dictionaries", "Dictionary"), ("graphics", "Map"),
                           ("commentaries", "Commentary"), ("journals", "Journal")):
            self.kind.append(code, name)
        self.kind.connect("changed", self.selection_changed)
        selectors.pack_start(self.kind, False, False, 0)
        self.refresh_button = Gtk.Button.new_with_label("Browse selected")
        self.refresh_button.connect("clicked", self.refresh)
        self.refresh_button.set_sensitive(False)
        selectors.pack_start(self.refresh_button, False, False, 0)
        searchbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(searchbar, False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search loaded MySword modules")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", self.filter_changed)
        searchbar.pack_start(self.search, True, True, 0)
        self.store = Gtk.ListStore(str, str, str, int)
        self.filtered = self.store.filter_new()
        self.filtered.set_visible_func(self.visible)
        self.view = Gtk.TreeView(model=self.filtered)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        for idx, heading in enumerate(("Module", "Language", "Type")):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(heading, renderer, text=idx)
            column.set_resizable(True)
            self.view.append_column(column)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.view)
        self.pack_start(scroller, True, True, 0)
        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(bottom, False, False, 0)
        self.status = Gtk.Label(label="Select a language and module type, then click Browse selected. No website requests are made before that.", xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_hexpand(True)
        bottom.pack_start(self.status, True, True, 0)
        self.download_button = Gtk.Button.new_with_label("Download selected")
        self.download_button.connect("clicked", self.download)
        self.download_button.set_sensitive(False)
        bottom.pack_end(self.download_button, False, False, 0)

    def selected_language(self):
        text = self.language.get_active_text()
        return text.strip() if text else ""

    def selection_changed(self, *_):
        # An old category's result must not be mistaken for the new selection.
        self.loaded = False
        self.catalog = []
        self.store.clear()
        self.download_button.set_sensitive(False)
        self.refresh_button.set_sensitive(
            not self.loading and bool(self.kind.get_active_id()) and bool(self.selected_language()))
        self.status.set_text("Click Browse selected to fetch the chosen language and type.")

    def set_busy(self, value):
        self.loading = value
        self.refresh_button.set_sensitive(
            not value and bool(self.kind.get_active_id()) and bool(self.selected_language()))
        self.download_button.set_sensitive(not value and bool(self.catalog))
        self.language.set_sensitive(not value)
        self.kind.set_sensitive(not value)

    def refresh(self, *_):
        if self.loading:
            return
        category = self.kind.get_active_id()
        language = self.selected_language()
        if not category or not language:
            self.status.set_text("Choose a language and a module type first.")
            return
        self.catalog = []
        self.store.clear()
        self.set_busy(True)
        self.status.set_text(f"Reading MySword {self.kind.get_active_text()} modules for {language}…")
        def worker():
            try:
                entries, errors = fetch_mysword_catalog(category, language)
                GLib.idle_add(self.ready, entries, errors)
            except Exception as error:
                GLib.idle_add(self.ready, [], [str(error)])
        threading.Thread(target=worker, daemon=True).start()

    def ready(self, entries, errors):
        if self.closed:
            return False
        self.loaded = True
        self.set_busy(False)
        self.catalog = entries
        self.store.clear()
        self.download_button.set_sensitive(bool(entries))
        for idx, entry in enumerate(entries):
            self.store.append((entry.name, entry.language, entry.kind, idx))
        self.status.set_text(
            f"{len(entries)} MySword modules found."
            + ((" Some sections unavailable: " + "; ".join(errors[:2])) if errors else "")
            + (" The website may restrict automated access." if not entries else "")
        )
        return False

    def visible(self, model, iterator, _data=None):
        q = self.search.get_text().strip().casefold()
        return not q or q in model[iterator][0].casefold()

    def filter_changed(self, *_):
        self.filtered.refilter()

    def download(self, *_):
        if self.loading:
            return
        model, paths = self.view.get_selection().get_selected_rows()
        selected = [self.catalog[model[path][3]] for path in paths]
        if not selected:
            self.status.set_text("Select one or more modules.")
            return
        self.set_busy(True)
        def worker():
            successes, errors = [], []
            for n, entry in enumerate(selected, 1):
                GLib.idle_add(self.set_progress, f"Downloading {n}/{len(selected)}: {entry.name}")
                try:
                    successes.extend(install_mysword(entry, self.parent_window.library))
                except Exception as error:
                    errors.append(f"{entry.name}: {error}")
            GLib.idle_add(self.completed, successes, errors)
        threading.Thread(target=worker, daemon=True).start()

    def set_progress(self, message):
        if not self.closed:
            self.status.set_text(message)
        return False

    def completed(self, successes, errors):
        if self.closed:
            return False
        self.set_busy(False)
        if successes:
            self.parent_window._populate_modules()
            if self.parent_window.current_bible:
                self.parent_window.navigate(
                    self.parent_window._first_available_ref(self.parent_window.current_bible))
        message = f"Installed {len(successes)} MySword module(s)."
        if errors:
            message += " " + "; ".join(errors[:3])
        self.status.set_text(message)
        self.parent_window._set_status(message)
        return False
