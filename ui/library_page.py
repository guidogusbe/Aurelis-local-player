"""
library_page.py

Central "Library" page: a searchable table of songs plus a "Load Folder"
action button.

Design notes:
    - REAL DATA: this page no longer generates mock songs. It is fed real
      rows from `database_manager.songs.get_all_songs()` via `load_songs()`,
      called by MainWindow (this widget still has no direct knowledge of
      sqlite3, keeping it UI-only).
    - Each row's `id`/`path` (and the rest of the song's data) is stored as
      Qt.UserRole data on the title cell, NOT via a parallel Python list
      indexed by row number. This is important because QTableWidget's
      built-in sorting (setSortingEnabled(True)) physically reorders rows,
      which would silently desync any `list[row_index] -> song` mapping.
      Reading the data back off the item itself is always correct
      regardless of the current sort order.
    - Exposes `load_folder_requested` (Load Folder button clicked) and
      `song_activated(dict)` signals so MainWindow can wire them up
      without LibraryPage needing to know about the database, scanner, or
      audio engine directly.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

LIBRARY_PAGE_STYLESHEET = """
QWidget#LibraryPage {
    background-color: #1A1B21;
}

QLabel#PageTitle {
    color: #ECEDF1;
    font-size: 22px;
    font-weight: 700;
}
QLabel#SongCountLabel {
    color: #9497A6;
    font-size: 12px;
}

QLineEdit#SearchBox {
    background-color: #22232B;
    color: #ECEDF1;
    border: 1px solid #33353F;
    border-radius: 14px;
    padding: 6px 14px;
    font-size: 12px;
    min-width: 220px;
}
QLineEdit#SearchBox:focus {
    border: 1px solid #7C9EFF;
}

QTableWidget#LibraryTable {
    background-color: #1A1B21;
    alternate-background-color: #1E1F26;
    gridline-color: transparent;
    border: none;
    color: #ECEDF1;
    selection-background-color: #2A2C36;
    selection-color: #ECEDF1;
}
QTableWidget#LibraryTable::item {
    padding: 8px 6px;
    border: none;
}
QTableWidget#LibraryTable::item:hover {
    background-color: #22232B;
}
QHeaderView::section {
    background-color: #1A1B21;
    color: #9497A6;
    border: none;
    border-bottom: 1px solid #33353F;
    padding: 8px 6px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}
QTableWidget#LibraryTable QScrollBar:vertical {
    background: transparent;
    width: 10px;
}
QTableWidget#LibraryTable QScrollBar::handle:vertical {
    background: #33353F;
    border-radius: 5px;
    min-height: 24px;
}
"""

# Column indices, named for readability.
COLUMN_INDEX = 0
COLUMN_TITLE = 1
COLUMN_ARTIST = 2
COLUMN_ALBUM = 3
COLUMN_DURATION = 4

# Qt.UserRole is used to stash the full song row on the title cell. Custom
# roles must start at Qt.UserRole (256) or above to avoid clashing with
# Qt's own built-in roles.
SONG_DATA_ROLE = Qt.ItemDataRole.UserRole


class LibraryPage(QWidget):
    """
    Library page: search box, "Load Folder" button, and a scrollable
    table of songs.

    Signals:
        load_folder_requested(): the "Load Folder" button was clicked.
            MainWindow is responsible for opening the folder picker and
            starting a real scan.
        add_song_requested(): the "Aggiungi brano" button was clicked.
            MainWindow is responsible for opening a single-file picker
            and inserting that one song directly (no background thread
            needed for a single file).
        refresh_db_requested(): the "Verifica libreria" button was
            clicked. MainWindow is responsible for starting a
            LibraryCleanupWorker to drop DB entries whose file no longer
            exists on disk.
        song_activated(dict): a song row was double-clicked. Emits the
            full song dict (title, artist, album, duration, path, ...)
            so the controller layer can hand the path straight to
            AudioEngine.load() without a second DB lookup.
        song_removal_requested(dict): "Rimuovi dalla libreria" was chosen
            from a row's right-click context menu. Emits the full song
            dict; MainWindow deletes it from the database (the file on
            disk is left untouched).
    """

    load_folder_requested = Signal()
    add_song_requested = Signal()
    refresh_db_requested = Signal()
    song_activated = Signal(dict)
    song_removal_requested = Signal(dict)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LibraryPage")
        self.setStyleSheet(LIBRARY_PAGE_STYLESHEET)

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(16)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_table(), stretch=1)

    def _build_header(self) -> QVBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        title_label = QLabel("Libreria")
        title_label.setObjectName("PageTitle")
        title_row.addWidget(title_label)
        title_row.addStretch(1)

        self._search_box = QLineEdit()
        self._search_box.setObjectName("SearchBox")
        self._search_box.setPlaceholderText("Cerca per titolo o artista…")
        title_row.addWidget(self._search_box)

        self._load_folder_button = QPushButton("Carica cartella")
        self._load_folder_button.setObjectName("ActionButton")
        self._load_folder_button.setCursor(Qt.CursorShape.PointingHandCursor)
        title_row.addWidget(self._load_folder_button)

        self._add_song_button = QPushButton("Aggiungi brano")
        self._add_song_button.setObjectName("ActionButton")
        self._add_song_button.setCursor(Qt.CursorShape.PointingHandCursor)
        title_row.addWidget(self._add_song_button)

        self._refresh_db_button = QPushButton("Verifica libreria")
        self._refresh_db_button.setObjectName("ActionButton")
        self._refresh_db_button.setCursor(Qt.CursorShape.PointingHandCursor)
        title_row.addWidget(self._refresh_db_button)

        header.addLayout(title_row)

        self._song_count_label = QLabel("")
        self._song_count_label.setObjectName("SongCountLabel")
        header.addWidget(self._song_count_label)

        return header

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, 5)
        self._table.setObjectName("LibraryTable")
        self._table.setHorizontalHeaderLabels(["#", "Titolo", "Artista", "Album", "Durata"])

        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setSortingEnabled(True)
        self._table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(COLUMN_INDEX, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(COLUMN_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_ARTIST, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_ALBUM, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COLUMN_DURATION, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COLUMN_INDEX, 40)
        self._table.setColumnWidth(COLUMN_DURATION, 70)

        return self._table

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._load_folder_button.clicked.connect(self.load_folder_requested.emit)
        self._add_song_button.clicked.connect(self.add_song_requested.emit)
        self._refresh_db_button.clicked.connect(self.refresh_db_requested.emit)
        self._search_box.textChanged.connect(self._on_search_text_changed)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._table.customContextMenuRequested.connect(self._on_context_menu_requested)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_songs(self, songs) -> None:
        """
        Populate the table from real database rows.

        Args:
            songs: An iterable of sqlite3.Row (or dict) objects shaped
                like the `songs` table — as returned by
                `database_manager.songs.get_all_songs()`. Each row must
                expose at least: title, artist, album, duration, path.
                `duration` is expected in whole seconds (as stored in
                the DB). Missing/NULL fields degrade gracefully.
        """
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(songs))

        for row_index, song in enumerate(songs):
            song_dict = dict(song)  # works for both sqlite3.Row and dict

            title = song_dict.get("title") or "Sconosciuto"
            artist = song_dict.get("artist") or "Artista sconosciuto"
            album = song_dict.get("album") or "—"
            duration_seconds = song_dict.get("duration") or 0

            title_item = self._make_item(title)
            # Stash the full row on the title cell so it survives sorting
            # and search-filtering; retrieved again on double-click.
            title_item.setData(SONG_DATA_ROLE, song_dict)

            self._table.setItem(row_index, COLUMN_INDEX, self._make_item(str(row_index + 1)))
            self._table.setItem(row_index, COLUMN_TITLE, title_item)
            self._table.setItem(row_index, COLUMN_ARTIST, self._make_item(artist))
            self._table.setItem(row_index, COLUMN_ALBUM, self._make_item(album))
            self._table.setItem(
                row_index,
                COLUMN_DURATION,
                self._make_item(self._format_duration(duration_seconds)),
            )

        self._table.setSortingEnabled(True)
        self._song_count_label.setText(f"{len(songs)} brani")

    def set_action_buttons_enabled(self, enabled: bool) -> None:
        """
        Enable/disable all three library-modifying buttons at once
        ("Carica cartella", "Aggiungi brano", "Verifica libreria"). Used
        by MainWindow to prevent overlapping operations against the same
        (single, shared) database connection — e.g. starting a folder
        scan while a cleanup pass is still running.
        """
        self._load_folder_button.setEnabled(enabled)
        self._add_song_button.setEnabled(enabled)
        self._refresh_db_button.setEnabled(enabled)

    def get_visible_playlist(self) -> list[dict]:
        """
        Return the song dicts for every currently visible (non-hidden,
        non-search-filtered) row, in their current display order —
        i.e. respecting whatever column the user last sorted by.

        Intended to be used as the AudioEngine playlist queue when the
        user activates a song, so next_track()/previous_track() walk the
        same order the user sees in the table.
        """
        playlist = []
        for row_index in range(self._table.rowCount()):
            if self._table.isRowHidden(row_index):
                continue
            item = self._table.item(row_index, COLUMN_TITLE)
            song_dict = item.data(SONG_DATA_ROLE)
            if song_dict is not None:
                playlist.append(song_dict)
        return playlist

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_search_text_changed(self, text: str) -> None:
        """Hide rows that don't match the search text (title or artist)."""
        query = text.strip().lower()
        for row_index in range(self._table.rowCount()):
            title = self._table.item(row_index, COLUMN_TITLE).text().lower()
            artist = self._table.item(row_index, COLUMN_ARTIST).text().lower()
            matches = query in title or query in artist
            self._table.setRowHidden(row_index, not matches)

    def _on_row_double_clicked(self, row: int, _column: int) -> None:
        item = self._table.item(row, COLUMN_TITLE)
        song_dict = item.data(SONG_DATA_ROLE)
        if song_dict is not None:
            self.song_activated.emit(song_dict)

    def _on_context_menu_requested(self, position) -> None:
        row = self._table.rowAt(position.y())
        if row < 0:
            return  # right-click landed outside any row (empty area)

        item = self._table.item(row, COLUMN_TITLE)
        song_dict = item.data(SONG_DATA_ROLE)
        if song_dict is None:
            return

        menu = QMenu(self)
        remove_action = menu.addAction("Rimuovi dalla libreria")
        chosen_action = menu.exec(self._table.viewport().mapToGlobal(position))

        if chosen_action == remove_action:
            self.song_removal_requested.emit(song_dict)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    @staticmethod
    def _format_duration(total_seconds: int) -> str:
        minutes, seconds = divmod(max(0, total_seconds), 60)
        return f"{minutes}:{seconds:02d}"
