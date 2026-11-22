"""Native GTK 3/PyGObject desktop interface for WordStudy GTK."""
from __future__ import annotations

import re
import sqlite3
from typing import Callable

from gi.repository import Gdk, Gio, GLib, Gtk, Pango

from .markup import Span, plain_text, styled_spans
from .module_browser import ModuleBrowser
from .modules import Hit, Library, Module, ModuleError
from .preferences import DisplayPreferences, PreferencesStore, compact_module_name
from .references import ABBR, BOOKS, Reference, parse_reference
from .storage import UserData

CSS = b"""
window { background: @theme_bg_color; }
.titleline { font-weight: bold; font-size: 18px; }
.muted { opacity: 0.73; }
.toolbar { padding: 7px; border-bottom: 1px solid alpha(@theme_fg_color, .12); }
.sidebar { border-right: 1px solid alpha(@theme_fg_color, .12); }
.rightbar { border-left: 1px solid alpha(@theme_fg_color, .12); }
.bibleview, .articleview { font-family: 'Noto Serif', 'Liberation Serif', serif; font-size: 15px; }
textview text { background: @theme_base_color; }
.entrylabel { font-size: 12px; font-weight: bold; opacity: .75; }
.statusline { padding: 5px 10px; border-top: 1px solid alpha(@theme_fg_color, .12); }
"""


def _label(text: str, cls: str | None = None, xalign: float = 0) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign)
    if cls:
        lbl.get_style_context().add_class(cls)
    return lbl


def _scroll(child: Gtk.Widget, min_width: int = 0, min_height: int = 0) -> Gtk.ScrolledWindow:
    scroller = Gtk.ScrolledWindow()
    scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroller.set_hexpand(True)
    scroller.set_vexpand(True)
    if min_width or min_height:
        scroller.set_size_request(min_width or -1, min_height or -1)
    scroller.add(child)
    return scroller


def _pixbuf_from_bytes(data: bytes, max_width: int | None = 900):
    try:
        from gi.repository import GdkPixbuf
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pixbuf = loader.get_pixbuf()
        if pixbuf is None or pixbuf.get_width() <= 0:
            return None
        width, height = pixbuf.get_width(), pixbuf.get_height()
        if max_width is not None and width > max_width:
            height = max(1, int(height * (max_width / width)))
            pixbuf = pixbuf.scale_simple(max_width, height, GdkPixbuf.InterpType.BILINEAR)
        return pixbuf
    except Exception:
        return None


def _view(readonly: bool = True, css_class: str = "articleview") -> Gtk.TextView:
    view = Gtk.TextView()
    view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    view.set_left_margin(18)
    view.set_right_margin(18)
    view.set_top_margin(16)
    view.set_bottom_margin(20)
    view.set_editable(not readonly)
    view.set_cursor_visible(not readonly)
    view.set_hexpand(True)
    view.set_vexpand(True)
    view.get_style_context().add_class(css_class)
    return view


class WordStudyApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.wordstudy.WordStudyGTK", flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.window: StudyWindow | None = None

    def do_activate(self) -> None:
        if not self.window:
            self.window = StudyWindow(self)
        self.window.present()


class StudyWindow(Gtk.ApplicationWindow):
    def __init__(self, application: Gtk.Application):
        super().__init__(application=application)
        self.set_title("WordStudy GTK")
        self.set_default_size(1390, 960)
        self.set_position(Gtk.WindowPosition.CENTER)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.library = Library()
        self.userdata = UserData(self.library.data_dir)
        self.preference_store = PreferencesStore(self.library.data_dir)
        self.preferences = self.preference_store.load()
        self.current_ref = Reference(43, 3, 16)
        self.current_bible: Module | None = None
        self.current_commentary: Module | None = None
        self.current_dictionary: Module | None = None
        self.current_book: Module | None = None
        self.parallel_bible: Module | None = None
        self._changing = False
        self._loaded_note_ref: Reference | None = None
        self._link_tags: dict[Gtk.TextTag, str] = {}
        self._verse_tags: dict[Gtk.TextTag, Reference] = {}
        self._hit_rows: list[Hit] = []
        self._map_zoom: float | None = None  # None means fit
        self._map_article: str | None = None
        self._map_topic: str | None = None

        self._build()
        self._populate_modules()
        self._apply_preferences()
        self.connect("delete-event", self._on_close)
        self.show_all()
        self.search_revealer.set_reveal_child(False)
        self.search_revealer.hide()
        self.parallel_scroller.hide()
        self.bible_reader_btn.hide()
        self.reader_stack.set_visible_child_name("bible")
        if not self.current_bible:
            self._show_welcome()
        else:
            self.navigate(self._first_available_ref(self.current_bible))

    def _build(self) -> None:
        header = Gtk.HeaderBar(title="WordStudy GTK", subtitle="Bible study • native Linux • MySword + MyBible modules")
        header.set_show_close_button(True)
        self.set_titlebar(header)
        import_btn = Gtk.Button.new_with_label("Import Modules")
        import_btn.set_tooltip_text("Import MySword .mybible or MyBible.zone .SQLite3 files, including .gz / .zip")
        import_btn.connect("clicked", self._import_files)
        header.pack_end(import_btn)
        download_btn = Gtk.Button.new_with_label("Download Modules")
        download_btn.set_tooltip_text("Browse the MyBible Android module catalog")
        download_btn.connect("clicked", lambda *_: ModuleBrowser(self))
        header.pack_end(download_btn)
        prefs_btn = Gtk.Button.new_with_label("Preferences")
        prefs_btn.set_tooltip_text("Fonts, wrapping, Strong's, morphology and red letters")
        prefs_btn.connect("clicked", self._open_preferences)
        header.pack_end(prefs_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(outer)
        tool_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tool_area.get_style_context().add_class("toolbar")
        outer.pack_start(tool_area, False, False, 0)
        # Separate navigation from search so neither fights for a very wide
        # minimum window width; long module names are clipped below.
        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        tool_area.pack_start(tools, False, False, 0)
        tools.pack_start(_label("Bible"), False, False, 0)
        self.bible_combo = Gtk.ComboBoxText()
        self.bible_combo.set_size_request(185, -1)
        self.bible_combo.connect("changed", self._on_bible_changed)
        tools.pack_start(self.bible_combo, False, False, 0)

        prev_btn = Gtk.Button.new_with_label("‹")
        prev_btn.set_tooltip_text("Previous chapter")
        prev_btn.connect("clicked", lambda *_: self._move_chapter(-1))
        tools.pack_start(prev_btn, False, False, 0)
        next_btn = Gtk.Button.new_with_label("›")
        next_btn.set_tooltip_text("Next chapter")
        next_btn.connect("clicked", lambda *_: self._move_chapter(1))
        tools.pack_start(next_btn, False, False, 0)
        self.ref_entry = Gtk.Entry()
        self.ref_entry.set_placeholder_text("John 3:16")
        self.ref_entry.set_width_chars(16)
        self.ref_entry.connect("activate", self._go_entry)
        tools.pack_start(self.ref_entry, False, False, 0)
        go_btn = Gtk.Button.new_with_label("Go")
        go_btn.connect("clicked", self._go_entry)
        tools.pack_start(go_btn, False, False, 0)

        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        tool_area.pack_start(search_row, False, False, 0)
        self.parallel_toggle = Gtk.ToggleButton(label="Parallel")
        self.parallel_toggle.set_tooltip_text("Read two translations side by side")
        self.parallel_toggle.connect("toggled", self._toggle_parallel)
        search_row.pack_start(self.parallel_toggle, False, False, 0)
        self.red_letter_check = Gtk.CheckButton.new_with_label("Words of Christ in red")
        self.red_letter_check.set_tooltip_text(
            "Color Christ’s words red when the module marks them (MySword <FR> or MyBible <J>)"
        )
        self.red_letter_check.set_active(self.preferences.words_of_christ_red)
        self.red_letter_check.connect("toggled", self._on_red_letter_toggled)
        search_row.pack_start(self.red_letter_check, False, False, 0)
        self.parallel_combo = Gtk.ComboBoxText()
        self.parallel_combo.set_size_request(115, -1)
        self.parallel_combo.connect("changed", self._on_parallel_changed)
        search_row.pack_start(self.parallel_combo, False, False, 0)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search Bible or library")
        self.search_entry.set_hexpand(True)
        self.search_entry.connect("activate", self._do_search)
        search_row.pack_start(self.search_entry, True, True, 0)
        self.search_scope = Gtk.ComboBoxText()
        self.search_scope.append("bible", "Bible")
        self.search_scope.append("library", "Library")
        self.search_scope.set_active_id("bible")
        search_row.pack_start(self.search_scope, False, False, 0)
        search_btn = Gtk.Button.new_with_label("Search")
        search_btn.connect("clicked", self._do_search)
        search_row.pack_start(search_btn, False, False, 0)

        self.sidebar_pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        horizontal = self.sidebar_pane
        horizontal.set_wide_handle(True)
        horizontal.set_position(185)
        outer.pack_start(horizontal, True, True, 0)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        sidebar.get_style_context().add_class("sidebar")
        sidebar.set_size_request(140, -1)
        sidebar.set_border_width(8)
        horizontal.pack1(sidebar, resize=True, shrink=False)
        sidebar.pack_start(_label("BOOKS", "entrylabel"), False, False, 3)
        self.book_list = Gtk.ListBox()
        self.book_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.book_list.connect("row-selected", self._on_book_selected)
        sidebar.pack_start(_scroll(self.book_list), True, True, 0)
        chapterbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        chapterbar.pack_start(_label("Chapter"), False, False, 0)
        self.chapter_spin = Gtk.SpinButton.new_with_range(1, 150, 1)
        self.chapter_spin.set_numeric(True)
        self.chapter_spin.connect("value-changed", self._on_chapter_changed)
        chapterbar.pack_end(self.chapter_spin, False, False, 0)
        sidebar.pack_start(chapterbar, False, False, 4)
        self.count_label = _label("No Bible loaded", "muted")
        self.count_label.set_line_wrap(True)
        self.count_label.set_width_chars(1)
        sidebar.pack_start(self.count_label, False, False, 4)

        self.main_pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.main_pane.set_wide_handle(True)
        self.main_pane.set_position(850)
        horizontal.pack2(self.main_pane, resize=True, shrink=True)
        self.center_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.main_pane.pack1(self.center_column, resize=True, shrink=True)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.translation_title = _label("Bible reader", "titleline", 0)
        self.translation_title.set_hexpand(True)
        self.translation_title.set_width_chars(1)
        self.translation_title.set_max_width_chars(45)
        self.translation_title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.translation_title.set_margin_start(15)
        self.translation_title.set_margin_top(4)
        self.translation_title.set_margin_bottom(4)
        title_row.pack_start(self.translation_title, True, True, 0)
        self.bible_reader_btn = Gtk.Button.new_with_label("Bible reader")
        self.bible_reader_btn.set_tooltip_text("Return to the Bible chapter view")
        self.bible_reader_btn.connect("clicked", lambda *_: self._show_bible_reader())
        self.bible_reader_btn.set_margin_end(10)
        title_row.pack_end(self.bible_reader_btn, False, False, 0)
        self.center_column.pack_start(title_row, False, False, 0)
        self.map_zoom_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.map_zoom_bar.set_margin_start(12)
        self.map_zoom_bar.set_margin_bottom(5)
        self.map_zoom_bar.set_no_show_all(True)
        for label, value in (("Fit to Window", None), ("100%", 1.0)):
            button = Gtk.Button.new_with_label(label)
            button.connect("clicked", lambda _button, zoom=value: self._set_map_zoom(zoom))
            self.map_zoom_bar.pack_start(button, False, False, 0)
        for label, multiplier in (("−", 0.8), ("+", 1.25)):
            button = Gtk.Button.new_with_label(label)
            button.connect("clicked", lambda _button, factor=multiplier: self._step_map_zoom(factor))
            self.map_zoom_bar.pack_start(button, False, False, 0)
        self.map_zoom_label = Gtk.Label(label="Fit")
        self.map_zoom_bar.pack_start(self.map_zoom_label, False, False, 4)
        self.center_column.pack_start(self.map_zoom_bar, False, False, 0)
        self.reader_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.reader_box.set_vexpand(True)
        self.center_column.set_vexpand(True)
        self.bible_view = _view(css_class="bibleview")
        self.bible_view.connect("button-release-event", self._on_text_click)
        self.primary_scroller = _scroll(self.bible_view)
        self.primary_scroller.get_style_context().add_class("biblescroll")
        self.reader_box.pack_start(self.primary_scroller, True, True, 0)
        self.parallel_view = _view(css_class="bibleview")
        self.parallel_view.connect("button-release-event", self._on_text_click)
        self.parallel_scroller = _scroll(self.parallel_view)
        self.parallel_scroller.get_style_context().add_class("biblescroll")
        self.reader_box.pack_start(self.parallel_scroller, True, True, 0)
        self.book_reader_view = _view(css_class="bibleview")
        self.book_reader_view.connect("button-release-event", self._on_text_click)
        self.book_reader_scroller = _scroll(self.book_reader_view)
        self.book_reader_scroller.get_style_context().add_class("biblescroll")
        self.reader_stack = Gtk.Stack()
        self.reader_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.reader_stack.set_vexpand(True)
        self.reader_stack.add_named(self.reader_box, "bible")
        self.reader_stack.add_named(self.book_reader_scroller, "book")
        self.center_column.pack_start(self.reader_stack, True, True, 0)

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_no_show_all(True)
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        results_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        results_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.result_label = _label("Search results", "entrylabel")
        results_head.set_margin_start(9)
        results_head.pack_start(self.result_label, True, True, 0)
        close_btn = Gtk.Button.new_with_label("Close results")
        close_btn.connect("clicked", self._hide_search_results)
        results_head.pack_end(close_btn, False, False, 0)
        results_col.pack_start(results_head, False, False, 0)
        self.result_list = Gtk.ListBox()
        self.result_list.connect("row-activated", self._on_hit_activated)
        result_scroller = _scroll(self.result_list)
        result_scroller.set_size_request(-1, 195)
        results_col.pack_start(result_scroller, True, True, 0)
        self.search_revealer.add(results_col)
        self.center_column.pack_end(self.search_revealer, False, False, 0)

        study = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        study.get_style_context().add_class("rightbar")
        study.set_size_request(240, -1)
        self.main_pane.pack2(study, resize=True, shrink=False)
        self.notebook = Gtk.Notebook()
        study.pack_start(self.notebook, True, True, 0)
        self._make_commentary_tab()
        self._make_dictionary_tab()
        self._make_books_tab()
        self._make_notes_tab()

        self.status = _label("Ready", xalign=0)
        self.status.get_style_context().add_class("statusline")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        outer.pack_end(self.status, False, False, 0)

    def _make_commentary_tab(self) -> None:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.set_border_width(8)
        self.commentary_combo = Gtk.ComboBoxText()
        self.commentary_combo.connect("changed", self._on_commentary_changed)
        col.pack_start(self.commentary_combo, False, False, 0)
        self.commentary_view = _view()
        self.commentary_view.connect("button-release-event", self._on_text_click)
        col.pack_start(_scroll(self.commentary_view), True, True, 0)
        self.notebook.append_page(col, _label("Commentary"))

    def _make_dictionary_tab(self) -> None:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.set_border_width(8)
        self.dictionary_combo = Gtk.ComboBoxText()
        self.dictionary_combo.connect("changed", self._on_dictionary_changed)
        col.pack_start(self.dictionary_combo, False, False, 0)
        entry_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.dictionary_entry = Gtk.Entry()
        self.dictionary_entry.set_placeholder_text("Word, G26, H2617 …")
        self.dictionary_entry.connect("activate", self._lookup_word)
        entry_bar.pack_start(self.dictionary_entry, True, True, 0)
        lookup_btn = Gtk.Button.new_with_label("Look up")
        lookup_btn.connect("clicked", self._lookup_word)
        entry_bar.pack_start(lookup_btn, False, False, 0)
        col.pack_start(entry_bar, False, False, 0)
        self.dictionary_view = _view()
        self.dictionary_view.connect("button-release-event", self._on_text_click)
        col.pack_start(_scroll(self.dictionary_view), True, True, 0)
        self.notebook.append_page(col, _label("Dictionary"))

    def _make_books_tab(self) -> None:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        col.set_border_width(8)
        self.books_combo = Gtk.ComboBoxText()
        self.books_combo.connect("changed", self._on_books_changed)
        col.pack_start(self.books_combo, False, False, 0)
        self.topic_entry = Gtk.SearchEntry()
        self.topic_entry.set_placeholder_text("Filter topics")
        self.topic_entry.connect("search-changed", self._filter_topics)
        col.pack_start(self.topic_entry, False, False, 0)
        self.topics_list = Gtk.ListBox()
        self.topics_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.topics_list.connect("row-selected", self._on_topic_selected)
        topics_scroll = _scroll(self.topics_list)
        col.pack_start(topics_scroll, True, True, 0)
        hint = _label("Selected topics open in the large center window, including map images.", "muted")
        hint.set_line_wrap(True)
        hint.set_width_chars(1)
        col.pack_start(hint, False, False, 4)
        self.notebook.append_page(col, _label("Books"))

    def _make_notes_tab(self) -> None:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        col.set_border_width(8)
        self.note_header = _label("Select a verse to take notes", "entrylabel")
        self.note_header.set_line_wrap(True)
        self.note_header.set_width_chars(1)
        col.pack_start(self.note_header, False, False, 0)
        self.notes_view = _view(readonly=False)
        col.pack_start(_scroll(self.notes_view), True, True, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save = Gtk.Button.new_with_label("Save note")
        save.connect("clicked", self._save_note)
        buttons.pack_start(save, True, True, 0)
        self.bookmark_button = Gtk.Button.new_with_label("☆ Bookmark")
        self.bookmark_button.connect("clicked", self._toggle_bookmark)
        buttons.pack_start(self.bookmark_button, True, True, 0)
        col.pack_start(buttons, False, False, 0)
        col.pack_start(_label("BOOKMARKS (double-click to navigate)", "entrylabel"), False, False, 0)
        self.bookmark_list = Gtk.ListBox()
        self.bookmark_list.connect("row-activated", self._bookmark_open)
        bookmark_scroll = _scroll(self.bookmark_list)
        bookmark_scroll.set_size_request(-1, 145)
        col.pack_start(bookmark_scroll, False, False, 0)
        self.notebook.append_page(col, _label("Notes"))
        self._refresh_bookmarks()

    def _set_translation_title(self, title: str) -> None:
        self.translation_title.set_text(title)
        self.translation_title.set_tooltip_text(title)

    def _refresh_module_tooltips(self) -> None:
        for combo, module in (
            (self.bible_combo, self.current_bible),
            (self.parallel_combo, self.parallel_bible),
            (self.commentary_combo, self.current_commentary),
            (self.dictionary_combo, self.current_dictionary),
            (self.books_combo, self.current_book),
        ):
            combo.set_tooltip_text(module.label if module else "No module selected")

    def _apply_preferences(self) -> None:
        prefs = self.preferences
        for views, font, wrapping in (
            ((self.bible_view, self.parallel_view), prefs.bible_font, prefs.wrap_bible),
            ((self.commentary_view, self.dictionary_view, self.book_reader_view), prefs.study_font, prefs.wrap_study),
            ((self.notes_view,), prefs.notes_font, prefs.wrap_notes),
        ):
            font_description = Pango.FontDescription.from_string(font)
            for view in views:
                view.override_font(font_description)
                view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if wrapping else Gtk.WrapMode.NONE)
        self.translation_title.override_font(Pango.FontDescription.from_string(prefs.title_font))
        self.translation_title.set_line_wrap(prefs.wrap_titles)
        # A long title must never impose a huge minimum-size requisition.
        self.translation_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.translation_title.set_lines(2 if prefs.wrap_titles else 1)

    def _open_preferences(self, *_args: object) -> None:
        dialog = Gtk.Dialog(
            title="Display Preferences", transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Apply", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        dialog.set_resizable(True)
        content = dialog.get_content_area()
        content.set_border_width(15)
        content.set_spacing(10)
        heading = _label("Fonts and word wrapping", "titleline")
        content.pack_start(heading, False, False, 3)
        grid = Gtk.Grid(column_spacing=15, row_spacing=9)
        content.pack_start(grid, False, False, 0)
        controls = {}
        rows = (
            ("bible_font", "Bible text"),
            ("study_font", "Commentary, dictionary and books"),
            ("notes_font", "Personal notes"),
            ("title_font", "Module / chapter title"),
        )
        for index, (key, label) in enumerate(rows):
            grid.attach(_label(label), 0, index, 1, 1)
            chooser = Gtk.FontButton()
            chooser.set_font_name(getattr(self.preferences, key))
            chooser.set_show_style(True)
            chooser.set_show_size(True)
            chooser.set_hexpand(True)
            grid.attach(chooser, 1, index, 1, 1)
            controls[key] = chooser
        wrap_options = (
            ("wrap_bible", "Wrap Bible verses at the edge of the reader"),
            ("wrap_study", "Wrap commentary, dictionary and book text"),
            ("wrap_notes", "Wrap personal notes"),
            ("wrap_titles", "Wrap long module titles (up to two lines)"),
        )
        for key, label in wrap_options:
            check = Gtk.CheckButton.new_with_label(label)
            check.set_active(getattr(self.preferences, key))
            content.pack_start(check, False, False, 0)
            controls[key] = check
        content.pack_start(_label("Bible study display", "titleline"), False, False, 4)
        bible_options = (
            ("show_strongs", "Show Strong’s numbers (when provided by the module)"),
            ("show_morphology", "Show morphology codes (when provided by the module)"),
            ("words_of_christ_red", "Display words of Christ in red (requires module markup)"),
        )
        for key, label in bible_options:
            check = Gtk.CheckButton.new_with_label(label)
            check.set_active(getattr(self.preferences, key))
            content.pack_start(check, False, False, 0)
            controls[key] = check
        help_label = _label(
            "Full module names are available by hovering over the module selector.\n"
            "When wrapping is off, text can be scrolled horizontally.", "muted"
        )
        help_label.set_line_wrap(True)
        help_label.set_max_width_chars(55)
        content.pack_start(help_label, False, False, 5)
        dialog.show_all()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            updated = DisplayPreferences()
            for key, _ in rows:
                setattr(updated, key, controls[key].get_font_name())
            for key, _ in (*wrap_options, *bible_options):
                setattr(updated, key, controls[key].get_active())
            self.preferences = updated
            self._changing = True
            self.red_letter_check.set_active(updated.words_of_christ_red)
            self._changing = False
            self._apply_preferences()
            if self.current_bible:
                self._render_bible()
            try:
                self.preference_store.save(updated)
                self._set_status("Display preferences saved")
            except OSError as error:
                self._message("Preferences were applied but could not be saved", str(error))
        dialog.destroy()

    def _set_status(self, message: str) -> None:
        self.status.set_text(message)

    @staticmethod
    def _clear_list(widget: Gtk.ListBox) -> None:
        for child in widget.get_children():
            widget.remove(child)

    @staticmethod
    def _module_from_combo(combo: Gtk.ComboBoxText, modules: list[Module]) -> Module | None:
        index = combo.get_active()
        return modules[index] if 0 <= index < len(modules) else None

    def _book_modules(self) -> list[Module]:
        return self.library.by_kind("bok") + self.library.by_kind("jor")

    def _populate_modules(self) -> None:
        self._changing = True
        previous = {kind: getattr(self, name).path if getattr(self, name) else None for kind, name in
                    (("bbl", "current_bible"), ("cmt", "current_commentary"),
                     ("dct", "current_dictionary"), ("bok", "current_book"))}
        combos = {"bbl": self.bible_combo, "cmt": self.commentary_combo,
                  "dct": self.dictionary_combo, "bok": self.books_combo}
        for kind, combo in combos.items():
            combo.remove_all()
            modules = self._book_modules() if kind == "bok" else self.library.by_kind(kind)
            for module in modules:
                combo.append_text(compact_module_name(module.abbreviation, module.title))
            if modules:
                matched = next((idx for idx, mod in enumerate(modules) if mod.path == previous[kind]), 0)
                combo.set_active(matched)
            else:
                combo.set_active(-1)
        for kind, field in (("bbl", "current_bible"), ("cmt", "current_commentary"),
                            ("dct", "current_dictionary"), ("bok", "current_book")):
            setattr(self, field, self._module_from_combo(combos[kind], self._book_modules() if kind == "bok" else self.library.by_kind(kind)))
        self.parallel_combo.remove_all()
        bibles = self.library.by_kind("bbl")
        for bible in bibles:
            self.parallel_combo.append_text(compact_module_name(bible.abbreviation, "", limit=20))
        if len(bibles) > 1:
            desired = next((i for i, mod in enumerate(bibles) if mod != self.current_bible), 0)
            self.parallel_combo.set_active(desired)
        elif bibles:
            self.parallel_combo.set_active(0)
        self.parallel_bible = self._module_from_combo(self.parallel_combo, bibles)
        self.parallel_toggle.set_sensitive(len(bibles) > 1)
        if len(bibles) < 2:
            self.parallel_toggle.set_active(False)
        self._changing = False
        self._refresh_module_tooltips()
        self._refresh_book_list()
        self._refresh_topics()
        self._update_study_panes()

    def _first_available_ref(self, bible: Module) -> Reference:
        try:
            books = self.library.books(bible)
            if books:
                chapters = self.library.chapters(bible, books[0])
                if chapters:
                    verses = self.library.verses(bible, books[0], chapters[0])
                    return Reference(books[0], chapters[0], verses[0].ref.verse if verses else 1)
        except (ModuleError, sqlite3.DatabaseError):
            pass
        return Reference(1, 1, 1)

    def _refresh_book_list(self) -> None:
        self._changing = True
        self._clear_list(self.book_list)
        if self.current_bible:
            try:
                available = self.library.books(self.current_bible)
            except (sqlite3.DatabaseError, ModuleError) as err:
                self._set_status(f"Module error: {err}")
                available = []
            for book in available:
                row = Gtk.ListBoxRow()
                row.book_number = book
                label = _label(BOOKS[book - 1])
                label.set_margin_start(6)
                label.set_margin_top(4)
                label.set_margin_bottom(4)
                row.add(label)
                self.book_list.add(row)
        self.book_list.show_all()
        self._changing = False

    def _select_book(self, book: int) -> None:
        for row in self.book_list.get_children():
            if getattr(row, "book_number", None) == book:
                self.book_list.select_row(row)
                return

    def _on_book_selected(self, widget: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None or self._changing or not self.current_bible:
            return
        book = getattr(row, "book_number", None)
        if book:
            chapters = self.library.chapters(self.current_bible, book)
            if chapters:
                self.navigate(Reference(book, chapters[0], 1))

    def _on_chapter_changed(self, spin: Gtk.SpinButton) -> None:
        if not self._changing and self.current_bible:
            self.navigate(Reference(self.current_ref.book, spin.get_value_as_int(), 1))

    def _on_bible_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._changing:
            return
        self._autosave_note()
        self.current_bible = self._module_from_combo(combo, self.library.by_kind("bbl"))
        self._refresh_module_tooltips()
        self._refresh_book_list()
        if self.current_bible:
            self.navigate(self.current_ref)
        else:
            self._show_welcome()

    def _on_parallel_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._changing:
            return
        self.parallel_bible = self._module_from_combo(combo, self.library.by_kind("bbl"))
        self._refresh_module_tooltips()
        self._render_bible()

    def _toggle_parallel(self, toggle: Gtk.ToggleButton) -> None:
        if not self._changing:
            self.parallel_scroller.set_visible(toggle.get_active() and self.parallel_bible is not None)
            self._render_bible()

    def _on_red_letter_toggled(self, button: Gtk.CheckButton) -> None:
        if self._changing:
            return
        self.preferences.words_of_christ_red = button.get_active()
        if self.current_bible:
            self._render_bible()
        try:
            self.preference_store.save(self.preferences)
        except OSError as error:
            self._set_status(f"Could not save red-letter preference: {error}")

    def _on_commentary_changed(self, combo: Gtk.ComboBoxText) -> None:
        if not self._changing:
            self.current_commentary = self._module_from_combo(combo, self.library.by_kind("cmt"))
            self._refresh_module_tooltips()
            self._refresh_commentary()

    def _on_dictionary_changed(self, combo: Gtk.ComboBoxText) -> None:
        if not self._changing:
            self.current_dictionary = self._module_from_combo(combo, self.library.by_kind("dct"))
            self._refresh_module_tooltips()
            self._lookup_word()

    def _on_books_changed(self, combo: Gtk.ComboBoxText) -> None:
        if not self._changing:
            self.current_book = self._module_from_combo(combo, self._book_modules())
            self._refresh_module_tooltips()
            self._refresh_topics()

    def _go_entry(self, *_args: object) -> None:
        try:
            self.navigate(parse_reference(self.ref_entry.get_text()))
        except ValueError as error:
            self._message("Invalid reference", str(error))

    def _move_chapter(self, delta: int) -> None:
        if not self.current_bible:
            return
        try:
            books = self.library.books(self.current_bible)
            if not books:
                return
            book = self.current_ref.book
            chapters = self.library.chapters(self.current_bible, book)
            if self.current_ref.chapter in chapters:
                next_index = chapters.index(self.current_ref.chapter) + delta
                if 0 <= next_index < len(chapters):
                    self.navigate(Reference(book, chapters[next_index], 1))
                    return
            if book not in books:
                self.navigate(self._first_available_ref(self.current_bible))
                return
            neighbor = books.index(book) + (1 if delta > 0 else -1)
            if 0 <= neighbor < len(books):
                book = books[neighbor]
                next_chapters = self.library.chapters(self.current_bible, book)
                if next_chapters:
                    self.navigate(Reference(book, next_chapters[0] if delta > 0 else next_chapters[-1], 1))
        except (ModuleError, sqlite3.DatabaseError) as err:
            self._set_status(str(err))

    def navigate(self, ref: Reference) -> None:
        if not self.current_bible:
            self._set_status("Import a Bible module first")
            return
        self._autosave_note()
        self.current_ref = ref
        self._changing = True
        self.ref_entry.set_text(str(ref))
        self._select_book(ref.book)
        chapters = self.library.chapters(self.current_bible, ref.book)
        self.chapter_spin.set_range(min(chapters) if chapters else 1, max(chapters) if chapters else 150)
        self.chapter_spin.set_value(ref.chapter)
        self._changing = False
        self._show_bible_reader()
        self._render_bible()
        self._update_study_panes()
        self._load_note()

    def _show_welcome(self) -> None:
        self._set_translation_title("Start your Bible study")
        self._put_message(self.bible_view, "WordStudy GTK\n\nImport a MySword or MyBible Bible module to begin.\n\n"
                          "Choose “Import Modules” above and select a .bbl.mybible or .SQLite3 file, or a .gz / .zip archive.\n\n"
                          "You can also import commentaries, dictionaries, and books.\n\n"
                          "Bundled sample files: demo_modules/ (source) or /usr/share/wordstudy-gtk/demo_modules/ (Debian).")
        self.count_label.set_text("No Bible loaded")

    def _make_tags(self, buffer: Gtk.TextBuffer) -> dict[str, Gtk.TextTag]:
        return {
            "bold": buffer.create_tag(None, weight=Pango.Weight.BOLD),
            "italic": buffer.create_tag(None, style=Pango.Style.ITALIC),
            "underline": buffer.create_tag(None, underline=Pango.Underline.SINGLE),
            "heading": buffer.create_tag(None, weight=Pango.Weight.BOLD, scale=1.20, pixels_above_lines=10),
            "code": buffer.create_tag(None, family="monospace"),
            "red": buffer.create_tag(None, foreground="#c41e3a", weight=Pango.Weight.NORMAL),
            "link": buffer.create_tag(None, foreground="#346bb7", underline=Pango.Underline.SINGLE),
            "muted": buffer.create_tag(None, foreground="#85858c"),
            "number": buffer.create_tag(None, weight=Pango.Weight.BOLD, foreground="#5777a0"),
            "selected": buffer.create_tag(None, background="#d9e6fa", foreground="#182942"),
            "module": buffer.create_tag(None, weight=Pango.Weight.BOLD, scale=1.08, pixels_above_lines=9),
        }

    def _append(self, buffer: Gtk.TextBuffer, text: str, tags: list[Gtk.TextTag] | None = None) -> None:
        start = buffer.get_end_iter().get_offset()
        buffer.insert(buffer.get_end_iter(), text)
        end = buffer.get_end_iter().get_offset()
        if tags and end > start:
            for tag in tags:
                buffer.apply_tag(tag, buffer.get_iter_at_offset(start), buffer.get_iter_at_offset(end))

    def _append_markup(self, buffer: Gtk.TextBuffer, html: str, tagset: dict[str, Gtk.TextTag],
                       bible: bool = False, module: Module | None = None,
                       book: int | None = None, zoom_images: bool = False) -> None:
        options = {}
        if bible:
            options = dict(
                format=module.format if module else "mysword", book=book,
                show_strongs=self.preferences.show_strongs,
                show_morphology=self.preferences.show_morphology,
                words_of_christ_red=self.preferences.words_of_christ_red,
            )
        for span in styled_spans(html, bible=bible, **options):
            if span.image:
                blob = self.library.resource(module, span.image) if module else None
                pixbuf = _pixbuf_from_bytes(blob, max_width=None if zoom_images else 900) if blob else None
                if pixbuf is not None:
                    if zoom_images:
                        scale = self._map_zoom
                        if scale is None:
                            width = max(1, self.book_reader_scroller.get_allocated_width() - 50)
                            height = max(1, self.book_reader_scroller.get_allocated_height() - 85)
                            scale = min(1.0, width / pixbuf.get_width(), height / pixbuf.get_height())
                        w = max(1, round(pixbuf.get_width() * scale))
                        h = max(1, round(pixbuf.get_height() * scale))
                        if (w, h) != (pixbuf.get_width(), pixbuf.get_height()):
                            from gi.repository import GdkPixbuf
                            pixbuf = pixbuf.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
                    buffer.insert_pixbuf(buffer.get_end_iter(), pixbuf)
                    self._append(buffer, "\n")
                else:
                    self._append(buffer, f"[{span.image}]\n", [tagset["muted"]])
                continue
            tags = [tagset[style] for style in span.styles if style in tagset]
            if span.link:
                link_tag = buffer.create_tag(None, foreground="#346bb7", underline=Pango.Underline.SINGLE)
                self._link_tags[link_tag] = span.link
                tags.append(link_tag)
            self._append(buffer, span.text, tags)

    def _put_message(self, view: Gtk.TextView, text: str) -> None:
        buffer = Gtk.TextBuffer()
        buffer.set_text(text)
        view.set_buffer(buffer)

    def _render_bible(self) -> None:
        if not self.current_bible:
            self._show_welcome()
            return
        self._link_tags = {}
        self._verse_tags = {}
        try:
            verses = self.library.verses(self.current_bible, self.current_ref.book, self.current_ref.chapter)
            self._render_translation(self.bible_view, self.current_bible, verses)
            if self.parallel_toggle.get_active() and self.parallel_bible:
                pverses = self.library.verses(self.parallel_bible, self.current_ref.book, self.current_ref.chapter)
                self._render_translation(self.parallel_view, self.parallel_bible, pverses)
            self._set_translation_title(
                f"{BOOKS[self.current_ref.book-1]} {self.current_ref.chapter}  ·  {self.current_bible.label}"
            )
            self.count_label.set_text(f"{len(verses)} verses")
            self._set_status(f"{self.current_ref}  |  {self.current_bible.label}")
        except (ModuleError, sqlite3.DatabaseError, ValueError) as error:
            self._put_message(self.bible_view, f"Cannot read chapter: {error}")
            self._set_status(str(error))

    def _render_translation(self, view: Gtk.TextView, module: Module, verses: list) -> None:
        buffer = Gtk.TextBuffer()
        tagset = self._make_tags(buffer)
        self._append(buffer, f"{module.label}\n", [tagset["heading"]])
        if module.rtl:
            view.set_direction(Gtk.TextDirection.RTL)
        else:
            view.set_direction(Gtk.TextDirection.LTR)
        if not verses:
            self._append(buffer, "\nNo verses for this chapter in this translation.")
        for verse in verses:
            start = buffer.get_end_iter().get_offset()
            self._append(buffer, f"{verse.ref.verse}  ", [tagset["number"]])
            self._append_markup(buffer, verse.raw, tagset, bible=True,
                                module=module, book=verse.ref.book)
            self._append(buffer, "\n\n")
            tag = buffer.create_tag(None)
            self._verse_tags[tag] = verse.ref
            buffer.apply_tag(tag, buffer.get_iter_at_offset(start), buffer.get_end_iter())
            if verse.ref.verse == self.current_ref.verse:
                buffer.apply_tag(tagset["selected"], buffer.get_iter_at_offset(start),
                                 buffer.get_iter_at_offset(min(start + len(str(verse.ref.verse)), buffer.get_char_count())))
        view.set_buffer(buffer)

    def _display_hits(self, view: Gtk.TextView, hits: list[Hit], placeholder: str) -> None:
        if not hits:
            self._put_message(view, placeholder)
            return
        buffer = Gtk.TextBuffer()
        tags = self._make_tags(buffer)
        for hit in hits:
            self._append(buffer, f"{hit.module.abbreviation} · {hit.title}\n", [tags["module"]])
            self._append_markup(buffer, hit.raw, tags, module=hit.module)
            self._append(buffer, "\n\n")
        view.set_buffer(buffer)

    def _refresh_commentary(self) -> None:
        if not self.current_commentary:
            self._put_message(self.commentary_view, "Import a .cmt.mybible commentary module to see linked study notes.")
            return
        try:
            entries = self.library.commentaries(self.current_commentary, self.current_ref)
            self._display_hits(self.commentary_view, entries, "No commentary on this passage.")
        except (ModuleError, sqlite3.DatabaseError) as error:
            self._put_message(self.commentary_view, f"Commentary could not be read: {error}")

    def _lookup_word(self, *_args: object) -> None:
        term = self.dictionary_entry.get_text().strip()
        if not self.current_dictionary:
            self._put_message(self.dictionary_view, "Import a .dct.mybible dictionary to look up words or Strong’s numbers.")
            return
        if not term:
            self._put_message(self.dictionary_view, "Enter a word above, or click a word / Strong’s link in the Bible.")
            return
        try:
            entries = self.library.dictionary(self.current_dictionary, term)
            self._display_hits(self.dictionary_view, entries, f"No dictionary entry for “{term}”.")
        except (ModuleError, sqlite3.DatabaseError) as error:
            self._put_message(self.dictionary_view, f"Dictionary could not be read: {error}")

    def _open_dictionary_term(self, term: str) -> None:
        self.dictionary_entry.set_text(term)
        self.notebook.set_current_page(1)
        self._lookup_word()

    def _show_bible_reader(self) -> None:
        self.map_zoom_bar.hide()
        self.reader_stack.set_visible_child_name("bible")
        self.bible_reader_btn.hide()
        if self.current_bible:
            self._set_translation_title(
                f"{BOOKS[self.current_ref.book-1]} {self.current_ref.chapter}  ·  {self.current_bible.label}"
            )

    def _set_map_zoom(self, zoom: float | None) -> None:
        if self._map_article is None:
            return
        self._map_zoom = zoom
        self.map_zoom_label.set_text("Fit" if zoom is None else f"{round(zoom * 100)}%")
        self._render_book_article()

    def _step_map_zoom(self, factor: float) -> None:
        zoom = self._map_zoom
        if zoom is None:
            zoom = self._fit_map_zoom()
        self._set_map_zoom(max(0.1, min(4.0, zoom * factor)))

    def _fit_map_zoom(self) -> float:
        if self._map_article is None or not self.current_book:
            return 1.0
        width = max(1, self.book_reader_scroller.get_allocated_width() - 50)
        height = max(1, self.book_reader_scroller.get_allocated_height() - 85)
        scales = []
        for span in styled_spans(self._map_article):
            if span.image:
                blob = self.library.resource(self.current_book, span.image)
                pixbuf = _pixbuf_from_bytes(blob, max_width=None) if blob else None
                if pixbuf:
                    scales.append(min(1.0, width / pixbuf.get_width(), height / pixbuf.get_height()))
        return min(scales) if scales else 1.0

    def _render_book_article(self) -> None:
        if self._map_article is None or self._map_topic is None or not self.current_book:
            return
        buffer = Gtk.TextBuffer()
        tags = self._make_tags(buffer)
        self._append(buffer, f"{self.current_book.label}\\n{self._map_topic}\\n\\n", [tags["heading"]])
        self._append_markup(buffer, self._map_article, tags, module=self.current_book,
                            zoom_images=self.map_zoom_bar.get_visible())
        self.book_reader_view.set_buffer(buffer)

    def _open_book_topic(self, topic: str) -> None:
        if not self.current_book:
            return
        try:
            article = self.library.article(self.current_book, topic) or ""
        except (ModuleError, sqlite3.DatabaseError) as error:
            self._put_message(self.book_reader_view, str(error))
            self.reader_stack.set_visible_child_name("book")
            self.bible_reader_btn.show()
            return
        self._map_article = article
        self._map_topic = topic
        self._map_zoom = None
        if any(span.image for span in styled_spans(article)):
            self.map_zoom_bar.show_all()
            self.map_zoom_label.set_text("Fit")
        else:
            self.map_zoom_bar.hide()
        self._render_book_article()
        self.reader_stack.set_visible_child_name("book")
        self.bible_reader_btn.show()
        self._set_translation_title(f"{topic}  ·  {self.current_book.label}")
        self._set_status(f"Book  |  {self.current_book.label}  |  {topic}")
        self.notebook.set_current_page(2)

    def _refresh_topics(self) -> None:
        self._changing = True
        self._clear_list(self.topics_list)
        if not self.current_book:
            self._changing = False
            return
        try:
            topics = self.library.topics(self.current_book, self.topic_entry.get_text())
            for topic in topics:
                row = Gtk.ListBoxRow()
                row.topic_name = topic
                label = _label(topic)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_width_chars(1)
                label.set_max_width_chars(32)
                label.set_tooltip_text(topic)
                label.set_margin_start(6)
                label.set_margin_top(3)
                label.set_margin_bottom(3)
                row.add(label)
                self.topics_list.add(row)
            self.topics_list.show_all()
        except (ModuleError, sqlite3.DatabaseError) as error:
            self._set_status(f"Book could not be read: {error}")
        self._changing = False

    def _filter_topics(self, *_args: object) -> None:
        if not self._changing:
            self._refresh_topics()

    def _on_topic_selected(self, widget: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._changing or not row or not self.current_book:
            return
        self._open_book_topic(row.topic_name)

    def _update_study_panes(self) -> None:
        self._refresh_commentary()
        if self.current_dictionary:
            self._lookup_word()
        else:
            self._put_message(self.dictionary_view, "Import a dictionary to look up Bible words and Strong’s numbers.")

    def _load_note(self) -> None:
        self._loaded_note_ref = self.current_ref
        self.notes_view.get_buffer().set_text(self.userdata.get_note(self.current_ref))
        self.note_header.set_text(f"MY NOTES · {self.current_ref}")
        self.bookmark_button.set_label("★ Bookmarked" if self.userdata.has_bookmark(self.current_ref) else "☆ Bookmark")

    def _autosave_note(self) -> None:
        if self._loaded_note_ref:
            buffer = self.notes_view.get_buffer()
            body = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            if self.userdata.get_note(self._loaded_note_ref) != body:
                self.userdata.save_note(self._loaded_note_ref, body)

    def _save_note(self, *_args: object) -> None:
        self._autosave_note()
        self._set_status(f"Note saved for {self.current_ref}")

    def _toggle_bookmark(self, *_args: object) -> None:
        active = self.userdata.toggle_bookmark(self.current_ref)
        self.bookmark_button.set_label("★ Bookmarked" if active else "☆ Bookmark")
        self._refresh_bookmarks()

    def _refresh_bookmarks(self) -> None:
        self._clear_list(self.bookmark_list)
        for ref in self.userdata.bookmarks():
            row = Gtk.ListBoxRow()
            row.reference = ref
            label = _label(str(ref))
            label.set_margin_start(8)
            label.set_margin_top(3)
            label.set_margin_bottom(3)
            row.add(label)
            self.bookmark_list.add(row)
        self.bookmark_list.show_all()

    def _bookmark_open(self, widget: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        if getattr(row, "reference", None):
            self.navigate(row.reference)

    def _hide_search_results(self, *_args: object) -> None:
        """Return all vertical space to the chapter reader."""
        self.search_revealer.set_reveal_child(False)
        self.search_revealer.hide()

    def _do_search(self, *_args: object) -> None:
        term = self.search_entry.get_text().strip()
        if not term:
            self._set_status("Enter text to search")
            return
        try:
            if self.search_scope.get_active_id() == "library":
                hits = self.library.search_library(term)
            elif self.current_bible:
                hits = self.library.search_bible(self.current_bible, term)
            else:
                self._set_status("Import a Bible or select Library search")
                return
            self._hit_rows = hits
            self._clear_list(self.result_list)
            for index, hit in enumerate(hits):
                row = Gtk.ListBoxRow()
                row.hit_index = index
                snippet = plain_text(
                    hit.raw, bible=hit.ref is not None,
                    **(dict(format=hit.module.format, book=hit.ref.book,
                            show_strongs=self.preferences.show_strongs,
                            show_morphology=self.preferences.show_morphology,
                            words_of_christ_red=self.preferences.words_of_christ_red)
                       if hit.ref is not None else {}),
                ).replace("\n", " ").strip()
                if len(snippet) > 130:
                    snippet = snippet[:127] + "…"
                label = _label(f"{hit.module.abbreviation} · {hit.title} — {snippet}")
                label.set_line_wrap(True)
                label.set_width_chars(1)
                label.set_max_width_chars(70)
                label.set_margin_start(9)
                label.set_margin_end(9)
                label.set_margin_top(5)
                label.set_margin_bottom(5)
                row.add(label)
                self.result_list.add(row)
            self.result_list.show_all()
            self.result_label.set_text(f"SEARCH RESULTS · {len(hits)} (max 200) · double-click to open")
            self.search_revealer.show()
            self.search_revealer.set_reveal_child(True)
            self._set_status(f"Found {len(hits)} result(s) for “{term}”")
        except (ModuleError, sqlite3.DatabaseError) as error:
            self._message("Search error", str(error))

    def _on_hit_activated(self, widget: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        idx = getattr(row, "hit_index", -1)
        if not (0 <= idx < len(self._hit_rows)):
            return
        hit = self._hit_rows[idx]
        if hit.ref:
            self.navigate(hit.ref)
            return
        if hit.module.kind == "dct":
            for i, module in enumerate(self.library.by_kind("dct")):
                if module == hit.module:
                    self.dictionary_combo.set_active(i)
                    break
            self._open_dictionary_term(hit.title)
        elif hit.module.kind == "cmt":
            self.notebook.set_current_page(0)
            self._display_hits(self.commentary_view, [hit], "No entry")
        elif hit.module.kind in ("bok", "jor"):
            for i, module in enumerate(self._book_modules()):
                if module == hit.module:
                    self.books_combo.set_active(i)
                    break
            self._open_book_topic(hit.title)

    def _on_text_click(self, view: Gtk.TextView, event: Gdk.EventButton) -> bool:
        if event.button != 1 or view.get_buffer().get_char_count() == 0:
            return False
        try:
            x, y = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(event.x), int(event.y))
            result = view.get_iter_at_location(x, y)
            itr = next((part for part in result if isinstance(part, Gtk.TextIter)), None) if isinstance(result, tuple) else result
            if itr is None:
                return False
            tags = itr.get_tags()
            for tag in tags:
                if tag in self._link_tags:
                    self._follow_link(self._link_tags[tag])
                    return True
            if view in (self.bible_view, self.parallel_view):
                ref = next((self._verse_tags[tag] for tag in tags if tag in self._verse_tags), None)
                if ref and ref != self.current_ref:
                    self.navigate(ref)
                # Click a Bible word for dictionary lookup (unless clicking a verse number).
                word_start, word_end = itr.copy(), itr.copy()
                if not word_start.starts_word():
                    word_start.backward_word_start()
                word_end.forward_word_end()
                term = view.get_buffer().get_text(word_start, word_end, False).strip()
                if term and re.match(r"^[\w'-]+$", term, flags=re.UNICODE) and self.current_dictionary:
                    self._open_dictionary_term(term)
            return False
        except (AttributeError, TypeError, ValueError):
            return False

    def _follow_link(self, link: str) -> None:
        if link.startswith("s") and len(link) > 2 and link[1] in ("G", "H"):
            self._open_dictionary_term(link[1:])
        elif link.startswith("b"):
            self._navigate_link_reference(link[1:])
        elif link.startswith("d"):
            term = link[1:]
            if term.startswith("-") and " " in term:
                abbr, term = term[1:].split(" ", 1)
                self._choose_module_by_abbr(self.dictionary_combo, self.library.by_kind("dct"), abbr)
            self._open_dictionary_term(term)
        elif link.startswith("c"):
            self.notebook.set_current_page(0)
            target = link[1:]
            if target.startswith("-") and " " in target:
                abbr, target = target[1:].split(" ", 1)
                self._choose_module_by_abbr(self.commentary_combo, self.library.by_kind("cmt"), abbr)
            self._navigate_link_reference(target)
        elif link.startswith("m"):
            self._open_dictionary_term(link[1:])
        elif link.startswith(("k-", "j-")):
            target = link[2:]
            # A book/journal link can specify the abbreviation followed by a topic.
            if " " in target:
                abbr, topic = target.split(" ", 1)
                if self._choose_module_by_abbr(self.books_combo, self._book_modules(), abbr):
                    target = topic
            if self.current_book:
                try:
                    html = self.library.article(self.current_book, target)
                    if html is not None:
                        self._open_book_topic(target)
                    else:
                        self._set_status(f"Book topic unavailable: {target}")
                except (ModuleError, sqlite3.DatabaseError) as error:
                    self._set_status(str(error))
        elif link.startswith("r"):
            self._message("Module note", link[1:][:3000])

    def _choose_module_by_abbr(self, combo: Gtk.ComboBoxText, modules: list[Module], abbreviation: str) -> bool:
        for i, module in enumerate(modules):
            if module.abbreviation.casefold() == abbreviation.casefold():
                combo.set_active(i)
                return True
        return False

    def _navigate_link_reference(self, target: str) -> None:
        # MySword permits a translation suffix in links, e.g. bJoh 3:16/NET.
        target = target.split("&", 1)[0].split("/", 1)[0]
        if re.match(r"^\d+\.\d+\.\d+\.[A-Za-z]+$", target):
            target = target.rsplit(".", 1)[0]
        try:
            self.navigate(parse_reference(target))
        except ValueError:
            self._set_status(f"Unsupported Bible reference: {target}")

    def _import_files(self, *_args: object) -> None:
        dialog = Gtk.FileChooserDialog(
            title="Import MySword / MyBible modules", transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
            buttons=(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK),
        )
        dialog.set_select_multiple(True)
        flt = Gtk.FileFilter()
        flt.set_name("MySword / MyBible SQLite modules / archives")
        for extension in ("*.mybible", "*.mybible.gz", "*.mybible.zip", "*.SQLite3", "*.sqlite3",
                          "*.SQLite3.gz", "*.sqlite3.gz", "*.zip", "*.gz"):
            flt.add_pattern(extension)
        dialog.add_filter(flt)
        allfiles = Gtk.FileFilter()
        allfiles.set_name("All files")
        allfiles.add_pattern("*")
        dialog.add_filter(allfiles)
        response = dialog.run()
        files = dialog.get_filenames() if response == Gtk.ResponseType.OK else []
        dialog.destroy()
        if not files:
            return
        installed: list[Module] = []
        errors: list[str] = []
        for file in files:
            try:
                installed.extend(self.library.import_file(file))
            except (OSError, ModuleError) as error:
                errors.append(f"{file}: {error}")
        if installed:
            self._populate_modules()
            if self.current_bible:
                self.navigate(self._first_available_ref(self.current_bible))
            names = ", ".join(m.abbreviation for m in installed)
            self._set_status(f"Imported {len(installed)} module(s): {names}")
        if errors:
            self._message("Some modules could not be imported", "\n".join(errors))

    def _message(self, title: str, body: str) -> None:
        dlg = Gtk.MessageDialog(
            transient_for=self, flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.WARNING, buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dlg.format_secondary_text(body)
        dlg.run()
        dlg.destroy()

    def _on_close(self, *_args: object) -> bool:
        self._autosave_note()
        return False
