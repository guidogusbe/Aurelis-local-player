"""
songs.py

Query layer for the `songs` table. Contains functions to:
    - Insert a new song into the database.
    - Check whether a song already exists by its file path (anti-duplicate
      check used by the folder scanner).
    - Retrieve all songs from the library.
    - Mark/unmark a song as favorite.
    - Delete one or more songs (used by library cleanup and manual removal).

Design notes:
    - Functions receive an open `sqlite3.Connection` as their first argument
      rather than owning a connection themselves. This keeps this module a
      pure query layer, decoupled from connection lifecycle management
      (which is `DatabaseManager`'s responsibility in database.py).
    - `SongData` is a lightweight dataclass used to pass song metadata into
      `insert_song`, avoiding long, error-prone positional argument lists
      and making call sites self-documenting.
    - All functions are safe to call from background QThread/QRunnable
      workers (e.g. the scanner), as long as each thread uses its own
      cursor on the shared connection and writes are not issued
      concurrently from multiple threads at once.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SongData:
    """
    Plain data container for the metadata of a single song, typically
    produced by the metadata extraction module (player/metadata.py) before
    being persisted to the database.
    """

    path: Path
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    track_number: int | None = None
    year: int | None = None
    duration: int | None = None
    format: str | None = None
    cover_path: Path | None = None


# ----------------------------------------------------------------------
# Insert
# ----------------------------------------------------------------------

def insert_song(connection: sqlite3.Connection, song: SongData) -> int:
    """
    Insert a new song into the `songs` table.

    Args:
        connection: An open sqlite3 connection (see DatabaseManager).
        song: A SongData instance holding the song's metadata.

    Returns:
        The `id` (primary key) of the newly inserted row.

    Raises:
        sqlite3.IntegrityError: If a song with the same `path` already
            exists (the `path` column is UNIQUE). Callers should use
            `song_exists()` beforehand to avoid this during a normal
            scanning workflow.
    """
    insert_sql = """
        INSERT INTO songs (
            title, artist, album, genre, track_number,
            year, duration, format, path, cover_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """
    cursor = connection.execute(
        insert_sql,
        (
            song.title,
            song.artist,
            song.album,
            song.genre,
            song.track_number,
            song.year,
            song.duration,
            song.format,
            str(song.path),
            str(song.cover_path) if song.cover_path is not None else None,
        ),
    )
    connection.commit()
    return cursor.lastrowid


# ----------------------------------------------------------------------
# Anti-duplicate check
# ----------------------------------------------------------------------

def song_exists(connection: sqlite3.Connection, path: Path) -> bool:
    """
    Check whether a song with the given file path is already present in
    the database. Used by the scanner to skip files that were already
    indexed on a previous run.

    Args:
        connection: An open sqlite3 connection.
        path: Absolute path of the file to check.

    Returns:
        True if a song with this path already exists, False otherwise.
    """
    query_sql = "SELECT 1 FROM songs WHERE path = ? LIMIT 1;"
    cursor = connection.execute(query_sql, (str(path),))
    return cursor.fetchone() is not None


# ----------------------------------------------------------------------
# Retrieval
# ----------------------------------------------------------------------

def get_all_songs(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """
    Retrieve all songs currently stored in the library.

    Args:
        connection: An open sqlite3 connection.

    Returns:
        A list of sqlite3.Row objects, each representing one song.
        Columns can be accessed either by name (row["title"]) or by
        index (row[1]), since the connection's row_factory is set to
        sqlite3.Row in DatabaseManager.

    Note:
        Results are ordered by artist then album then track_number to
        make it convenient to display a sensibly grouped library view
        without extra sorting logic in the UI layer.
    """
    query_sql = """
        SELECT *
        FROM songs
        ORDER BY artist COLLATE NOCASE, album COLLATE NOCASE, track_number;
    """
    cursor = connection.execute(query_sql)
    return cursor.fetchall()


# ----------------------------------------------------------------------
# Favorites
# ----------------------------------------------------------------------

def set_favorite(connection: sqlite3.Connection, song_id: int, favorite: bool) -> None:
    """
    Mark or unmark a song as favorite.

    Args:
        connection: An open sqlite3 connection.
        song_id: The `id` of the song to update.
        favorite: True to mark as favorite, False to unmark.
    """
    update_sql = "UPDATE songs SET favorite = ? WHERE id = ?;"
    connection.execute(update_sql, (1 if favorite else 0, song_id))
    connection.commit()


# ----------------------------------------------------------------------
# Deletion
# ----------------------------------------------------------------------

def delete_song(connection: sqlite3.Connection, song_id: int) -> None:
    """
    Remove a single song from the library by its `id`.

    Used by the "Remove from library" context-menu action: the file on
    disk is left untouched, only the database row is deleted.

    Args:
        connection: An open sqlite3 connection.
        song_id: The `id` of the song to delete.
    """
    connection.execute("DELETE FROM songs WHERE id = ?;", (song_id,))
    connection.commit()


def delete_songs(connection: sqlite3.Connection, song_ids: list[int]) -> int:
    """
    Remove multiple songs from the library in a single transaction.
    Used by the library "refresh"/cleanup worker to bulk-remove entries
    whose file no longer exists on disk.

    Args:
        connection: An open sqlite3 connection.
        song_ids: List of `id` values to delete. An empty list is a
            no-op (returns 0 without touching the database).

    Returns:
        The number of rows actually deleted.
    """
    if not song_ids:
        return 0
    placeholders = ",".join("?" for _ in song_ids)
    cursor = connection.execute(
        f"DELETE FROM songs WHERE id IN ({placeholders});",
        song_ids,
    )
    connection.commit()
    return cursor.rowcount
