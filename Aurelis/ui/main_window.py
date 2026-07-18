"""
main_window.py

Main application window for Aurelis.

Layout:
    - Sidebar (left): navigation between Home, Library, Favorites, Settings.
    - Main Content (center): a QStackedWidget hosting each page.
    - Player Bar (bottom): playback controls, spans the full width.

Design notes (Phase 5 — Wiring):
    - MainWindow no longer owns mock data. It receives its dependencies
      (an open sqlite3.Connection and an AudioEngine instance) via the
      constructor, built by main.py. This keeps MainWindow a pure
      "controller/glue" layer: it doesn't create the DB connection or the
      audio backend itself, it just wires their signals to the UI widgets
      and vice versa.
    - LibraryPage and PlayerBar remain UI-only widgets (see their own
      files) — all knowledge of sqlite3 / QtMultimedia specifics is
      confined to this class's slots.
    - `self._songs_by_path` is a lookup built every time the library is
      (re)loaded, used to resolve "title/artist to show in the player
      bar" whenever AudioEngine reports a new track loaded — AudioEngine
      itself only knows about a Path, not song metadata.
    - A single QSS stylesheet (MAIN_STYLESHEET) is applied at the window
      level and cascades to all child widgets.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from database_manager import songs as songs_repo
from database_manager.database import DatabaseManager
from player.audio_engine import AudioEngine
from player.scanner import ScannerWorker
from ui.library_page import LibraryPage
from ui.player_bar import PlayerBar

# ----------------------------------------------------------------------
# Palette (sober, elegant dark theme — not a direct Spotify clone)
# ----------------------------------------------------------------------
MAIN_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1A1B21;
    color: #ECEDF1;
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

QWidget#Sidebar {
    background-color: #14151A;
    border-right: 1px solid #33353F;
}

QLabel#AppLogo {
    color: #ECEDF1;
    font-size: 20px;
    font-weight: 600;
    padding: 22px 20px 18px 20px;
}

NavButton {
    background-color: transparent;
    color: #9497A6;
    border: none;
    border-radius: 8px;
    text-align: left;
    padding: 10px 16px;
    margin: 2px 12px;
    font-size: 13px;
    font-weight: 500;
}
NavButton:hover {
    background-color: #22232B;
    color: #ECEDF1;
}
NavButton:checked {
    background-color: #2A2C36;
    color: #ECEDF1;
    border-left: 3px solid #7C9EFF;
}

QLabel#PlaceholderPage {
    color: #9497A6;
    font-size: 16px;
}

QPushButton#LoadFolderButton {
    background-color: #7C9EFF;
    color: #14151A;
    border: none;
    border-radius: 18px;
    padding: 9px 22px;
    font-weight: 600;
}
QPushButton#LoadFolderButton:hover {
    background-color: #93B0FF;
}
QPushButton#LoadFolderButton:pressed {
    background-color: #6C8AE0;
}

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0px;
}
QScrollBar::handle:vertical {
    background: #33353F;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #454858;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


class NavButton(QPushButton):
    """Checkable sidebar navigation button (own subclass for clean QSS targeting)."""

    def __init__(self, label: str, parent: QWidget | None = None) -> None:
        super().__init__(label, parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)


class MainWindow(QMainWindow):
    """
    Aurelis' main window: Sidebar + Main Content (stacked pages) + Player
    Bar, wired to a real database connection and AudioEngine.

    Args:
        connection: An open sqlite3.Connection (row_factory = sqlite3.Row),
            owned and closed by main.py — MainWindow only reads from it.
        db_manager: The same DatabaseManager that opened `connection`.
            Needed (not just the raw connection) because ScannerWorker
            calls `db_manager.get_connection()` itself from its own
            background thread.
        audio_engine: A live AudioEngine instance, owned by main.py so its
            lifetime isn't tied to any single window.
    """

    PAGE_HOME = 0
    PAGE_LIBRARY = 1
    PAGE_FAVORITES = 2
    PAGE_SETTINGS = 3

    def __init__(
        self,
        connection,
        db_manager: DatabaseManager,
        audio_engine: AudioEngine,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Aurelis")
        self.resize(1200, 760)
        self.setMinimumSize(960, 600)

        self._connection = connection
        self._db_manager = db_manager
        self._audio_engine = audio_engine

        # Holds the currently running ScannerWorker (QThread), or None
        # when no scan is in progress. Kept as an attribute (not a local
        # variable) so the QThread object isn't garbage-collected while
        # it's still running.
        self._scanner_worker: ScannerWorker | None = None

        # path (str) -> song dict, rebuilt on every library reload. Lets us
        # show title/artist in the player bar from just a Path, since
        # AudioEngine only deals in Paths, not metadata.
        self._songs_by_path: dict[str, dict] = {}

        self._build_ui()
        self._connect_ui_signals()
        self._connect_audio_engine_signals()
        self.setStyleSheet(MAIN_STYLESHEET)

        self._nav_buttons[self.PAGE_LIBRARY].setChecked(True)
        self._stacked_pages.setCurrentIndex(self.PAGE_LIBRARY)

        self.refresh_library()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        top_area = QWidget(central_widget)
        top_layout = QHBoxLayout(top_area)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        top_layout.addWidget(self._build_sidebar())
        top_layout.addWidget(self._build_main_content(), stretch=1)

        root_layout.addWidget(top_area, stretch=1)

        self.player_bar = PlayerBar(central_widget)
        root_layout.addWidget(self.player_bar)
        # Reflect the AudioEngine's actual starting volume, no mock data.
        self.player_bar.set_volume(self._audio_engine.get_volume())

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        logo = QLabel("♪  Aurelis")
        logo.setObjectName("AppLogo")
        layout.addWidget(logo)

        nav_labels = ["Home", "Libreria", "Preferiti", "Impostazioni"]
        self._nav_buttons: list[NavButton] = []
        self._nav_group = QButtonGroup(sidebar)
        self._nav_group.setExclusive(True)

        for index, label in enumerate(nav_labels):
            button = NavButton(label)
            self._nav_group.addButton(button, index)
            layout.addWidget(button)
            self._nav_buttons.append(button)

        layout.addStretch(1)
        return sidebar

    def _build_main_content(self) -> QWidget:
        self._stacked_pages = QStackedWidget()

        self._stacked_pages.addWidget(self._build_placeholder_page("🏠  Home — coming soon"))

        self.library_page = LibraryPage()
        self._stacked_pages.addWidget(self.library_page)

        self._stacked_pages.addWidget(self._build_placeholder_page("♥  Preferiti — coming soon"))
        self._stacked_pages.addWidget(self._build_placeholder_page("⚙  Impostazioni — coming soon"))

        return self._stacked_pages

    @staticmethod
    def _build_placeholder_page(text: str) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        label = QLabel(text)
        label.setObjectName("PlaceholderPage")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        return page

    # ------------------------------------------------------------------
    # Signal wiring: UI -> controller
    # ------------------------------------------------------------------

    def _connect_ui_signals(self) -> None:
        self._nav_group.idClicked.connect(self._stacked_pages.setCurrentIndex)

        self.library_page.load_folder_requested.connect(self._on_load_folder_requested)
        self.library_page.song_activated.connect(self._on_song_activated)

        self.player_bar.play_pause_clicked.connect(self._audio_engine.toggle_play_pause)
        self.player_bar.next_clicked.connect(self._on_next_requested)
        self.player_bar.previous_clicked.connect(self._on_previous_requested)
        self.player_bar.seek_requested.connect(self._audio_engine.set_position)
        self.player_bar.volume_changed.connect(self._audio_engine.set_volume)

    # ------------------------------------------------------------------
    # Signal wiring: AudioEngine -> UI
    # ------------------------------------------------------------------

    def _connect_audio_engine_signals(self) -> None:
        self._audio_engine.position_changed.connect(self.player_bar.set_position)
        self._audio_engine.duration_changed.connect(self.player_bar.set_duration)
        self._audio_engine.track_loaded.connect(self._on_track_loaded)

        # Keep the play/pause icon truthful even when playback state
        # changes for reasons other than the user clicking the button
        # (auto-advance, next/previous, song ending).
        self._audio_engine.playback_started.connect(lambda: self.player_bar.set_playing_state(True))
        self._audio_engine.playback_paused.connect(lambda: self.player_bar.set_playing_state(False))
        self._audio_engine.playback_stopped.connect(lambda: self.player_bar.set_playing_state(False))

        # Auto-advance to the next track when the current one ends
        # naturally; if there is none, just stop and reset the icon.
        self._audio_engine.song_finished.connect(self._on_song_finished)

        self._audio_engine.error_occurred.connect(self._on_audio_error)

    # ------------------------------------------------------------------
    # Slots — Library
    # ------------------------------------------------------------------

    def refresh_library(self) -> None:
        """Reload the library table from the database (real data, no mock)."""
        rows = songs_repo.get_all_songs(self._connection)
        song_dicts = [dict(row) for row in rows]
        self.library_page.load_songs(song_dicts)
        self._songs_by_path = {song["path"]: song for song in song_dicts}

    def _on_scan_progress(self, processed: int, total: int) -> None:
        self.statusBar().showMessage(f"Scansione in corso: {processed}/{total} file")

    def _on_scan_finished(self, songs_added: int, songs_skipped: int) -> None:
        self.statusBar().showMessage(
            f"Scansione completata: {songs_added} brani aggiunti, "
            f"{songs_skipped} ignorati (già presenti o non leggibili).",
            6000,
        )
        self.library_page.set_load_folder_button_enabled(True)
        self._scanner_worker = None
        # New rows are now in the database — reload the table so the
        # user sees them without needing to restart the app.
        self.refresh_library()

    def _on_scan_error(self, message: str) -> None:
        self.statusBar().showMessage(f"Errore durante la scansione: {message}", 6000)
        self.library_page.set_load_folder_button_enabled(True)
        self._scanner_worker = None

    def _on_load_folder_requested(self) -> None:
        """
        Open a native folder picker and, if the user picks one, start a
        real background scan of it via ScannerWorker.
        """
        if self._scanner_worker is not None and self._scanner_worker.isRunning():
            # A scan is already in progress; ignore extra clicks rather
            # than starting a second worker against the same connection.
            return

        folder = QFileDialog.getExistingDirectory(self, "Seleziona la cartella musicale")
        if folder:
            self._start_scan(Path(folder))

    def _start_scan(self, folder: Path) -> None:
        self._scanner_worker = ScannerWorker(folder, self._db_manager, parent=self)
        self._scanner_worker.progress_updated.connect(self._on_scan_progress)
        self._scanner_worker.scan_finished.connect(self._on_scan_finished)
        self._scanner_worker.error_occurred.connect(self._on_scan_error)

        self.library_page.set_load_folder_button_enabled(False)
        self.statusBar().showMessage(f"Scansione di {folder} in corso…")
        self._scanner_worker.start()

    # ------------------------------------------------------------------
    # Slots — Playback control from the UI
    # ------------------------------------------------------------------

    def _on_song_activated(self, song: dict) -> None:
        """A song was double-clicked in the library: build the playlist
        from what's currently visible in the table (respecting sort/
        search), load the chosen track, and start playing it."""
        playlist_songs = self.library_page.get_visible_playlist()
        paths = [Path(item["path"]) for item in playlist_songs]

        try:
            start_index = next(
                index for index, item in enumerate(playlist_songs) if item["path"] == song["path"]
            )
        except StopIteration:
            # Song wasn't in the visible/filtered set for some reason —
            # fall back to a single-track "playlist".
            paths = [Path(song["path"])]
            start_index = 0

        self._audio_engine.set_playlist(paths, start_index)
        self._audio_engine.play()

    def _on_next_requested(self) -> None:
        if not self._audio_engine.next_track():
            self.player_bar.set_playing_state(False)

    def _on_previous_requested(self) -> None:
        if not self._audio_engine.previous_track():
            self.player_bar.set_playing_state(False)

    def _on_song_finished(self) -> None:
        if not self._audio_engine.next_track():
            self.player_bar.set_playing_state(False)

    # ------------------------------------------------------------------
    # Slots — AudioEngine feedback
    # ------------------------------------------------------------------

    def _on_track_loaded(self, path: Path) -> None:
        """AudioEngine only knows the Path; look up metadata to display."""
        song = self._songs_by_path.get(str(path))
        if song is not None:
            title = song.get("title") or path.stem
            artist = song.get("artist") or "Artista sconosciuto"
        else:
            title = path.stem
            artist = "Artista sconosciuto"

        initials = "".join(word[0] for word in title.split()[:2]).upper() or "??"
        self.player_bar.set_track_info(title=title, artist=artist, cover_text=initials)
        self.player_bar.set_position(0)

    def _on_audio_error(self, message: str) -> None:
        # Minimal handling for this phase: surface it in the window title
        # bar so playback errors aren't silently swallowed. A proper
        # toast/status-bar notification can replace this later.
        self.statusBar().showMessage(f"Errore di riproduzione: {message}", 5000)
