"""
database.py

Handles the SQLite database connection and schema initialization for Aurelis.
Responsible for:
    - Creating required directories (database/, cache/thumbnails/) if missing.
    - Creating the `songs` table with proper constraints.
    - Creating indexes to speed up lookups (unique path index + search indexes).
    - Providing a single point of access to the database connection.

Design notes:
    - Uses only `pathlib` for filesystem operations (per project rules).
    - Uses the standard library `sqlite3` module (no external ORM).
    - `DatabaseManager` is intentionally simple: it owns the connection and
      exposes it to the query layer (songs.py), which performs actual CRUD.
"""

import sqlite3
from pathlib import Path


class DatabaseManager:
    """
    Manages the lifecycle of the SQLite connection and schema for Aurelis.

    Typical usage:
        db_manager = DatabaseManager()
        db_manager.initialize()   # creates folders, tables, indexes if needed
        conn = db_manager.get_connection()
        ...
        db_manager.close()
    """

    # Default paths, relative to the project root (Aurelis/).
    DEFAULT_DB_PATH = Path("database") / "music.db"
    DEFAULT_CACHE_THUMBNAILS_PATH = Path("cache") / "thumbnails"

    def __init__(self, db_path: Path | None = None) -> None:
        """
        Initialize the DatabaseManager.

        Args:
            db_path: Optional custom path to the SQLite database file.
                     Defaults to `database/music.db` relative to the CWD.
        """
        self._db_path: Path = db_path if db_path is not None else self.DEFAULT_DB_PATH
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def initialize(self) -> None:
        """
        Perform first-run / startup initialization:
            1. Ensure required directories exist (database/, cache/thumbnails/).
            2. Open (or create) the SQLite database file.
            3. Create the `songs` table if it does not already exist.
            4. Create indexes (unique on `path`, plus search indexes).

        This method is idempotent: it is safe to call on every application
        startup, not only on first run.
        """
        self._ensure_directories_exist()
        self._connect()
        self._create_tables()
        self._create_indexes()

    def get_connection(self) -> sqlite3.Connection:
        """
        Return the active SQLite connection, connecting first if needed.

        Returns:
            An open sqlite3.Connection instance with row_factory set to
            sqlite3.Row (so query results can be accessed by column name).
        """
        if self._connection is None:
            self._connect()
        return self._connection

    def close(self) -> None:
        """Close the database connection, if open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_directories_exist(self) -> None:
        """Create the folders required by the app if they don't exist yet."""
        # Parent folder of the database file (e.g. "database/").
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        # Cache folder for resized cover thumbnails.
        self.DEFAULT_CACHE_THUMBNAILS_PATH.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> None:
        """
        Open the SQLite connection.

        `check_same_thread=False` is set because, per project rules, heavy
        operations (e.g. the folder scanner) will run on background
        QThread/QRunnable workers rather than the GUI thread, and may need
        to reuse this connection object. Callers are still responsible for
        serializing writes appropriately (e.g. via a mutex or a dedicated
        worker) to avoid concurrent write conflicts.
        """
        self._connection = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
        )
        # Return rows as sqlite3.Row objects -> allows dict-like access
        # (e.g. row["title"]) in addition to positional access.
        self._connection.row_factory = sqlite3.Row

        # Enforce foreign key constraints (good practice, even though the
        # current schema has no FKs yet; keeps future migrations safe).
        self._connection.execute("PRAGMA foreign_keys = ON;")

    def _create_tables(self) -> None:
        """Create the `songs` table if it does not already exist."""
        create_songs_table_sql = """
            CREATE TABLE IF NOT EXISTS songs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT,
                artist       TEXT,
                album        TEXT,
                genre        TEXT,
                track_number INTEGER,
                year         INTEGER,
                duration     INTEGER,
                format       TEXT,
                path         TEXT UNIQUE NOT NULL,
                cover_path   TEXT,
                play_count   INTEGER DEFAULT 0,
                favorite     INTEGER DEFAULT 0,
                date_added   DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """
        self._connection.execute(create_songs_table_sql)
        self._connection.commit()

    def _create_indexes(self) -> None:
        """
        Create indexes used to speed up common queries:
            - Unique index on `path`: speeds up duplicate checks during
              folder scanning (also enforced by the UNIQUE constraint above,
              but an explicit index makes the intent clear and guarantees
              the lookup is indexed even if the constraint implementation
              changes).
            - Indexes on `artist` and `album`: speed up library browsing
              and filtering by artist/album.
            - Index on `favorite`: speeds up the "Favorites" page query.
        """
        index_statements = [
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_songs_path ON songs (path);",
            "CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs (artist);",
            "CREATE INDEX IF NOT EXISTS idx_songs_album ON songs (album);",
            "CREATE INDEX IF NOT EXISTS idx_songs_favorite ON songs (favorite);",
        ]
        for statement in index_statements:
            self._connection.execute(statement)
        self._connection.commit()
