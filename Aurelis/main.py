"""
main.py

Entry point for Aurelis. Coordinates first-run initialization, opens the
database connection, builds the AudioEngine, and starts the GUI.

Uses the real `database_manager.database.DatabaseManager`:
`initialize()` creates the required folders/tables/indexes (idempotent —
safe to call on every startup, not just the first), then
`get_connection()` hands back an open sqlite3.Connection with
row_factory = sqlite3.Row already set.
"""

import sqlite3
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

from database_manager.database import DatabaseManager
from player.audio_engine import AudioEngine
from ui.main_window import MainWindow

# ----------------------------------------------------------------------
# Project paths (relative to this file, per the spec's folder layout)
# ----------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "music.db"
CONFIG_DIR = BASE_DIR / "config"

# DatabaseManager.initialize() already creates database/ and
# cache/thumbnails/ itself, so only config/ (which it doesn't know about)
# needs to be ensured here.


def ensure_config_directory() -> None:
    """Create config/ at first run if missing (idempotent)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def initialize_database() -> "tuple[DatabaseManager, sqlite3.Connection]":
    """
    Initialize (first run: create folders/tables/indexes, otherwise a
    no-op check) and open the SQLite database.

    Returns:
        A (db_manager, connection) pair. `db_manager` is kept around so
        it can be closed cleanly on shutdown; `connection` is what the
        rest of the app (songs.py, MainWindow) actually queries.
    """
    db_manager = DatabaseManager(DATABASE_PATH)
    db_manager.initialize()
    return db_manager, db_manager.get_connection()


def main() -> int:
    ensure_config_directory()
    db_manager, connection = initialize_database()

    app = QApplication(sys.argv)

    # AudioEngine is a QObject and needs a running QApplication/event
    # loop to be constructed safely (QMediaPlayer/QAudioOutput depend on
    # it), so it's built after QApplication, not before.
    audio_engine = AudioEngine()

    window = MainWindow(connection=connection, db_manager=db_manager, audio_engine=audio_engine)
    window.show()

    exit_code = app.exec()

    # DatabaseManager owns the connection's lifecycle, so it's the one
    # that should close it — rather than closing `connection` directly
    # and leaving db_manager holding a stale, already-closed reference.
    db_manager.close()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
