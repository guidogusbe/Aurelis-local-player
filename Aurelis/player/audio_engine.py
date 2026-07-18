"""
audio_engine.py

Core playback engine for Aurelis, built on top of PySide6.QtMultimedia
(QMediaPlayer + QAudioOutput).

Responsibilities:
    - Load a local audio file for playback.
    - Play / pause / stop control.
    - Move to the next / previous track within an internal playlist queue.
    - Volume control (0-100 scale, as commonly exposed in UI sliders).
    - Track playback position and total duration (in milliseconds),
      broadcast to the UI via Qt signals.
    - Emit a dedicated signal when a song finishes, so the UI/controller
      layer can react (e.g. auto-advance to the next track).

Design notes:
    - QMediaPlayer already does its decoding/playback work off the GUI
      thread internally (it is backed by the platform's native media
      framework), so no extra QThread is needed here. AudioEngine simply
      wraps QMediaPlayer + QAudioOutput and re-exposes their state changes
      as clean, Aurelis-specific signals, decoupling the rest of the app
      from QtMultimedia's own API surface.
    - AudioEngine owns a simple internal playlist (a list of file paths)
      so that `next_track()` / `previous_track()` have something to
      navigate. Higher-level playlist logic (e.g. shuffle, repeat modes)
      belongs to the UI/controller layer, which can call `set_playlist()`
      whenever the active queue changes (e.g. user opens the Library page
      or a Favorites view).
"""

from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


class AudioEngine(QObject):
    """
    Wraps QMediaPlayer/QAudioOutput to provide a simple, Aurelis-specific
    playback API and signal set for the UI to consume.

    Signals:
        position_changed(int):
            Current playback position, in milliseconds.
        duration_changed(int):
            Total duration of the currently loaded track, in milliseconds.
        volume_changed(int):
            Current volume, on a 0-100 scale.
        playback_started():
            Emitted when playback (re)starts (play() was called and
            succeeded).
        playback_paused():
            Emitted when playback is paused.
        playback_stopped():
            Emitted when playback is stopped.
        track_loaded(Path):
            Emitted when a new track has been successfully loaded.
        song_finished():
            Emitted when the current track reaches its natural end.
            Intended to be connected by the controller layer to trigger
            auto-advance to the next track.
        error_occurred(str):
            Emitted when QMediaPlayer reports a playback error (e.g.
            unsupported/corrupted file).
    """

    position_changed = Signal(int)
    duration_changed = Signal(int)
    volume_changed = Signal(int)
    playback_started = Signal()
    playback_paused = Signal()
    playback_stopped = Signal()
    track_loaded = Signal(Path)
    song_finished = Signal()
    error_occurred = Signal(str)

    # Default volume applied when the engine is created (0-100 scale).
    DEFAULT_VOLUME = 80

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # --- QtMultimedia backend objects -----------------------------
        self._player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_output)

        # --- Internal playlist state -----------------------------------
        self._playlist: list[Path] = []
        self._current_index: int = -1

        self._connect_signals()
        self.set_volume(self.DEFAULT_VOLUME)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        """Connect QMediaPlayer's native signals to our internal handlers."""
        self._player.positionChanged.connect(self.position_changed.emit)
        self._player.durationChanged.connect(self.duration_changed.emit)
        self._player.mediaStatusChanged.connect(self._on_media_status_changed)
        self._player.errorOccurred.connect(self._on_error_occurred)

    def _on_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        """
        React to changes in the media's loading/playback status.

        We only care about EndOfMedia here: it signals that the current
        track has finished playing naturally (as opposed to being
        manually stopped), which is what should trigger auto-advance.
        """
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self.song_finished.emit()

    def _on_error_occurred(
        self,
        error: QMediaPlayer.Error,
        error_string: str,
    ) -> None:
        """Forward QMediaPlayer errors (e.g. corrupted/unsupported file)."""
        if error != QMediaPlayer.Error.NoError:
            self.error_occurred.emit(error_string)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, file_path: Path) -> None:
        """
        Load a single audio file for playback, without touching the
        internal playlist queue. Useful when the caller manages its own
        playlist (e.g. via `set_playlist`) or just wants to play one
        specific file.

        Args:
            file_path: Absolute path to the audio file to load.
        """
        self._player.setSource(QUrl.fromLocalFile(str(file_path)))
        self.track_loaded.emit(file_path)

    def set_playlist(self, tracks: list[Path], start_index: int = 0) -> None:
        """
        Replace the internal playlist queue and load the track at
        `start_index`, without starting playback automatically.

        Args:
            tracks: Ordered list of audio file paths making up the queue
                (e.g. the currently viewed library page, or a favorites
                list).
            start_index: Index within `tracks` to load initially.

        Raises:
            ValueError: If `tracks` is empty or `start_index` is out of
                bounds.
        """
        if not tracks:
            raise ValueError("Playlist cannot be empty.")
        if not (0 <= start_index < len(tracks)):
            raise ValueError("start_index is out of range for the given playlist.")

        self._playlist = list(tracks)
        self._current_index = start_index
        self.load(self._playlist[self._current_index])

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    def play(self) -> None:
        """Start or resume playback of the currently loaded track."""
        self._player.play()
        self.playback_started.emit()

    def pause(self) -> None:
        """Pause playback, keeping the current position."""
        self._player.pause()
        self.playback_paused.emit()

    def stop(self) -> None:
        """Stop playback and reset the position to the beginning."""
        self._player.stop()
        self.playback_stopped.emit()

    def toggle_play_pause(self) -> None:
        """Convenience method: pause if playing, play if paused/stopped."""
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def next_track(self) -> bool:
        """
        Advance to the next track in the internal playlist and start
        playing it.

        Returns:
            True if there was a next track to move to, False if the
            current track is already the last one in the playlist (in
            which case playback is left unchanged).
        """
        if not self._playlist or self._current_index + 1 >= len(self._playlist):
            return False

        self._current_index += 1
        self.load(self._playlist[self._current_index])
        self.play()
        return True

    def previous_track(self) -> bool:
        """
        Go back to the previous track in the internal playlist and start
        playing it.

        Returns:
            True if there was a previous track to move to, False if the
            current track is already the first one in the playlist.
        """
        if not self._playlist or self._current_index - 1 < 0:
            return False

        self._current_index -= 1
        self.load(self._playlist[self._current_index])
        self.play()
        return True

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    def set_volume(self, volume: int) -> None:
        """
        Set the playback volume.

        Args:
            volume: Volume level on a 0-100 scale (clamped to this range).
        """
        clamped_volume = max(0, min(100, volume))
        # QAudioOutput expects a linear float in the 0.0-1.0 range.
        self._audio_output.setVolume(clamped_volume / 100.0)
        self.volume_changed.emit(clamped_volume)

    def get_volume(self) -> int:
        """Return the current volume on a 0-100 scale."""
        return round(self._audio_output.volume() * 100)

    # ------------------------------------------------------------------
    # Position / seeking
    # ------------------------------------------------------------------

    def set_position(self, position_ms: int) -> None:
        """
        Seek to a specific position in the current track. Typically
        called when the user drags the position slider in the UI.

        Args:
            position_ms: Target position, in milliseconds.
        """
        self._player.setPosition(position_ms)

    def get_position(self) -> int:
        """Return the current playback position, in milliseconds."""
        return self._player.position()

    def get_duration(self) -> int:
        """Return the total duration of the current track, in milliseconds."""
        return self._player.duration()

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_playing(self) -> bool:
        """Return True if a track is currently playing."""
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def current_track_path(self) -> Path | None:
        """Return the path of the currently loaded track, if any."""
        if 0 <= self._current_index < len(self._playlist):
            return self._playlist[self._current_index]
        return None
