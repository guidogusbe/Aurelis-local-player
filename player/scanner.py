"""
scanner.py

Background folder scanner for Aurelis. Recursively walks a music folder
using `pathlib`, identifies supported audio files, extracts their
metadata (via player/metadata.py) and persists new songs to the database
(via database_manager/songs.py).

Also provides `LibraryCleanupWorker`, a separate background worker that
verifies every song already in the database still exists on disk, and
removes the ones that don't (moved/renamed/deleted files, or files on
removable storage that isn't currently plugged in).

Design notes:
    - Both workers run entirely on a background `QThread` so the GUI
      thread is never blocked, per project rules.
    - Progress and results are communicated back to the GUI exclusively
      through Qt signals, never by returning values directly, since the
      workers run on a different thread.
    - `ScannerWorker` skips files already present in the database
      (checked via `song_exists`) so re-scanning an existing library only
      pays the cost of a filesystem walk + an indexed path lookup per
      file, not a full re-extraction of every file's metadata — only new
      files get their tags read via Mutagen.
    - Both workers hold a reference to the shared `DatabaseManager`
      instance and call `get_connection()` themselves. Because the
      connection was opened with `check_same_thread=False`, it can be
      used from a background thread. Callers should avoid running the
      scanner and the cleanup worker at the same time to prevent
      overlapping write operations on the same connection.
"""

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from database_manager.database import DatabaseManager
from database_manager.songs import delete_songs, get_all_songs, insert_song, song_exists
from player.metadata import SUPPORTED_FORMATS, extract_metadata


class ScannerWorker(QThread):
    """
    Background worker that scans a music folder and populates the
    database with any newly found songs.

    Signals:
        progress_updated(int, int):
            Emitted after each file is processed. Args are
            (files_processed_so_far, total_files_to_process).
        scan_finished(int, int):
            Emitted when the scan completes normally. Args are
            (songs_added, songs_skipped).
        error_occurred(str):
            Emitted if an unrecoverable error stops the scan (e.g. the
            root folder does not exist, or a database error occurs).
            Per-file metadata errors are handled internally and do NOT
            trigger this signal (see player/metadata.py fallback logic).
    """

    progress_updated = Signal(int, int)
    scan_finished = Signal(int, int)
    error_occurred = Signal(str)

    def __init__(
        self,
        root_folder: Path,
        db_manager: DatabaseManager,
        parent=None,
    ) -> None:
        """
        Args:
            root_folder: The music folder to scan recursively.
            db_manager: The shared DatabaseManager instance (already
                initialized) used to obtain the database connection.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._root_folder = root_folder
        self._db_manager = db_manager
        self._is_cancelled = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cancel(self) -> None:
        """
        Request cancellation of an in-progress scan. The worker checks
        this flag between files and stops as soon as possible; already
        processed songs remain in the database.
        """
        self._is_cancelled = True

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Main worker routine, executed on the background thread when
        `start()` is called on this QThread instance.
        """
        if not self._root_folder.is_dir():
            self.error_occurred.emit(
                f"Folder not found or not a directory: {self._root_folder}"
            )
            return

        try:
            connection = self._db_manager.get_connection()
        except Exception as exc:
            self.error_occurred.emit(f"Database connection error: {exc}")
            return

        audio_files = self._collect_audio_files(self._root_folder)
        total_files = len(audio_files)

        songs_added = 0
        songs_skipped = 0

        for index, file_path in enumerate(audio_files, start=1):
            if self._is_cancelled:
                break

            try:
                if song_exists(connection, file_path):
                    songs_skipped += 1
                else:
                    song_data = extract_metadata(file_path)
                    insert_song(connection, song_data)
                    songs_added += 1
            except Exception:
                # A single problematic file (e.g. a race condition where
                # it was deleted mid-scan, or an unexpected DB error on
                # this row) should not abort the whole scan.
                songs_skipped += 1

            self.progress_updated.emit(index, total_files)

        self.scan_finished.emit(songs_added, songs_skipped)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_audio_files(root_folder: Path) -> list[Path]:
        """
        Recursively collect all files under `root_folder` whose extension
        matches one of the supported audio formats.

        Args:
            root_folder: The folder to scan recursively.

        Returns:
            A list of absolute paths to supported audio files.
        """
        return [
            path
            for path in root_folder.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_FORMATS
        ]


class LibraryCleanupWorker(QThread):
    """
    Background worker for the "Verifica libreria" action: checks every
    song currently stored in the database and removes the ones whose
    `path` no longer points to an existing file (moved, renamed, deleted,
    or living on removable/network storage that isn't available right
    now).

    Only the database row is removed — the worker never touches files on
    disk.

    Signals:
        progress_updated(int, int):
            Emitted after each song is checked. Args are
            (songs_checked_so_far, total_songs).
        cleanup_finished(int):
            Emitted when the check completes. Arg is the number of
            entries removed from the database.
        error_occurred(str):
            Emitted if an unrecoverable error stops the cleanup (e.g. a
            database access failure).
    """

    progress_updated = Signal(int, int)
    cleanup_finished = Signal(int)
    error_occurred = Signal(str)

    def __init__(self, db_manager: DatabaseManager, parent=None) -> None:
        """
        Args:
            db_manager: The shared DatabaseManager instance (already
                initialized) used to obtain the database connection.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._db_manager = db_manager

    def run(self) -> None:
        try:
            connection = self._db_manager.get_connection()
        except Exception as exc:
            self.error_occurred.emit(f"Database connection error: {exc}")
            return

        try:
            all_songs = get_all_songs(connection)
        except Exception as exc:
            self.error_occurred.emit(f"Could not read library: {exc}")
            return

        total_songs = len(all_songs)
        missing_ids: list[int] = []

        for index, song in enumerate(all_songs, start=1):
            try:
                if not Path(song["path"]).exists():
                    missing_ids.append(song["id"])
            except Exception:
                # A single unreadable path (e.g. a permissions error
                # while stat-ing it) shouldn't abort the whole check —
                # just leave that entry alone this run.
                pass
            self.progress_updated.emit(index, total_songs)

        try:
            removed_count = delete_songs(connection, missing_ids)
        except Exception as exc:
            self.error_occurred.emit(f"Could not remove missing songs: {exc}")
            return

        self.cleanup_finished.emit(removed_count)
