"""
scanner.py

Background folder scanner for Aurelis. Recursively walks a music folder
using `pathlib`, identifies supported audio files, extracts their
metadata (via player/metadata.py) and persists new songs to the database
(via database_manager/songs.py).

Design notes:
    - Runs entirely on a background `QThread` (ScannerWorker) so the GUI
      thread is never blocked while scanning large libraries, per project
      rules.
    - Progress and results are communicated back to the GUI exclusively
      through Qt signals (`progress_updated`, `scan_finished`,
      `error_occurred`), never by returning values directly, since the
      worker runs on a different thread.
    - Skips files already present in the database (checked via
      `song_exists`) so re-scanning an existing library is fast and does
      not create duplicates.
    - The worker holds a reference to the shared `DatabaseManager`
      connection. Because the connection was opened with
      `check_same_thread=False`, it can be used from this background
      thread. Callers should avoid issuing writes to the same connection
      from the GUI thread *while a scan is running*, to prevent
      overlapping write operations.
"""

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from database_manager.database import DatabaseManager
from database_manager.songs import insert_song, song_exists
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
