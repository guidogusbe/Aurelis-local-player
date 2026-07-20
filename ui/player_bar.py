"""
player_bar.py

Bottom playback bar for Aurelis: cover thumbnail, track title/artist,
transport controls (previous/play-pause/next), position slider with time
labels, and a volume slider.

Design notes:
    - This widget is UI-only for now: buttons emit signals and expose
      public setter methods (set_track_info, set_position, set_duration,
      set_playing_state), but nothing is wired to player/audio_engine.py
      yet. That wiring is intentionally left for a later phase — the
      public API below already matches what AudioEngine will need:
        * play_pause_clicked / next_clicked / previous_clicked
        * seek_requested(position_ms)
        * volume_changed(volume_0_100)
    - The cover "thumbnail" is a plain QLabel styled via QSS as a rounded
      colored square showing the track's initials — a lightweight mock
      standing in for a real decoded cover image (added in a later
      phase, backed by cache/thumbnails/).
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

PLAYER_BAR_STYLESHEET = """
QWidget#PlayerBar {
    background-color: #14151A;
    border-top: 1px solid #33353F;
}

QLabel#CoverThumbnail {
    background-color: #2A2C36;
    color: #7C9EFF;
    border-radius: 6px;
    font-weight: 700;
    font-size: 14px;
}

QLabel#TrackTitleLabel {
    color: #ECEDF1;
    font-size: 13px;
    font-weight: 600;
}
QLabel#TrackArtistLabel {
    color: #9497A6;
    font-size: 12px;
}

QLabel#TimeLabel {
    color: #9497A6;
    font-size: 11px;
}

ControlButton {
    background-color: transparent;
    border: none;
    color: #ECEDF1;
    font-size: 15px;
    padding: 4px;
}
ControlButton:hover {
    color: #7C9EFF;
}

PlayButton {
    background-color: #ECEDF1;
    color: #14151A;
    border: none;
    border-radius: 18px;
    font-size: 14px;
    min-width: 36px;
    min-height: 36px;
    max-width: 36px;
    max-height: 36px;
}
PlayButton:hover {
    background-color: #7C9EFF;
}

QSlider#PositionSlider::groove:horizontal,
QSlider#VolumeSlider::groove:horizontal {
    height: 4px;
    background: #33353F;
    border-radius: 2px;
}
QSlider#PositionSlider::sub-page:horizontal,
QSlider#VolumeSlider::sub-page:horizontal {
    background: #7C9EFF;
    border-radius: 2px;
}
QSlider#PositionSlider::handle:horizontal,
QSlider#VolumeSlider::handle:horizontal {
    background: #ECEDF1;
    width: 11px;
    height: 11px;
    margin: -4px 0;
    border-radius: 5px;
}
QSlider#PositionSlider::handle:horizontal:hover,
QSlider#VolumeSlider::handle:horizontal:hover {
    background: #7C9EFF;
}

QLabel#VolumeIcon {
    color: #9497A6;
    font-size: 14px;
}
"""


class ControlButton(QPushButton):
    """Small flat transport button (previous/next). Own subclass for QSS."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)


class PlayButton(QPushButton):
    """Circular primary play/pause button. Own subclass for QSS."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("▶", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)


class PlayerBar(QWidget):
    """
    Bottom playback bar.

    Signals:
        play_pause_clicked(): the play/pause button was pressed. The
            caller (controller layer) decides the resulting state and
            should call `set_playing_state()` back to update the icon.
        next_clicked(): the "next track" button was pressed.
        previous_clicked(): the "previous track" button was pressed.
        seek_requested(int): the user released the position slider at a
            new position, in milliseconds. Intended to be connected to
            AudioEngine.set_position().
        volume_changed(int): the volume slider moved to a new value on a
            0-100 scale. Intended to be connected to
            AudioEngine.set_volume().
    """

    play_pause_clicked = Signal()
    next_clicked = Signal()
    previous_clicked = Signal()
    seek_requested = Signal(int)
    volume_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.setFixedHeight(88)
        self.setStyleSheet(PLAYER_BAR_STYLESHEET)

        self._is_playing = False
        self._duration_ms = 0

        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(16)

        layout.addLayout(self._build_track_info_section(), stretch=2)
        layout.addLayout(self._build_transport_section(), stretch=3)
        layout.addLayout(self._build_volume_section(), stretch=2)

    def _build_track_info_section(self) -> QHBoxLayout:
        section = QHBoxLayout()
        section.setSpacing(10)

        self._cover_label = QLabel("")
        self._cover_label.setObjectName("CoverThumbnail")
        self._cover_label.setFixedSize(56, 56)
        self._cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self._title_label = QLabel("No track loaded")
        self._title_label.setObjectName("TrackTitleLabel")
        self._artist_label = QLabel("—")
        self._artist_label.setObjectName("TrackArtistLabel")
        text_column.addStretch(1)
        text_column.addWidget(self._title_label)
        text_column.addWidget(self._artist_label)
        text_column.addStretch(1)

        section.addWidget(self._cover_label)
        section.addLayout(text_column)
        section.addStretch(1)
        return section

    def _build_transport_section(self) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setSpacing(4)

        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(14)
        self._previous_button = ControlButton("⏮")
        self._play_pause_button = PlayButton()
        self._next_button = ControlButton("⏭")

        buttons_row.addStretch(1)
        buttons_row.addWidget(self._previous_button)
        buttons_row.addWidget(self._play_pause_button)
        buttons_row.addWidget(self._next_button)
        buttons_row.addStretch(1)

        slider_row = QHBoxLayout()
        slider_row.setSpacing(8)
        self._current_time_label = QLabel("0:00")
        self._current_time_label.setObjectName("TimeLabel")
        self._total_time_label = QLabel("0:00")
        self._total_time_label.setObjectName("TimeLabel")

        self._position_slider = QSlider(Qt.Orientation.Horizontal)
        self._position_slider.setObjectName("PositionSlider")
        self._position_slider.setRange(0, 0)

        slider_row.addWidget(self._current_time_label)
        slider_row.addWidget(self._position_slider, stretch=1)
        slider_row.addWidget(self._total_time_label)

        section.addLayout(buttons_row)
        section.addLayout(slider_row)
        return section

    def _build_volume_section(self) -> QHBoxLayout:
        section = QHBoxLayout()
        section.setSpacing(8)

        volume_icon = QLabel("🔊")
        volume_icon.setObjectName("VolumeIcon")

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setObjectName("VolumeSlider")
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(110)

        section.addStretch(1)
        section.addWidget(volume_icon)
        section.addWidget(self._volume_slider)
        return section

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._play_pause_button.clicked.connect(self._on_play_pause_clicked)
        self._next_button.clicked.connect(self.next_clicked.emit)
        self._previous_button.clicked.connect(self.previous_clicked.emit)
        self._position_slider.sliderReleased.connect(self._on_seek_released)
        self._volume_slider.valueChanged.connect(self.volume_changed.emit)

    def _on_play_pause_clicked(self) -> None:
        # Toggle locally so the icon updates immediately even before the
        # controller layer (AudioEngine, in a later phase) confirms the
        # new state via set_playing_state().
        self.set_playing_state(not self._is_playing)
        self.play_pause_clicked.emit()

    def _on_seek_released(self) -> None:
        self.seek_requested.emit(self._position_slider.value())

    # ------------------------------------------------------------------
    # Public API (for the controller / AudioEngine to call)
    # ------------------------------------------------------------------

    def set_track_info(self, title: str, artist: str, cover_text: str = "") -> None:
        """Update the displayed track title, artist, and cover placeholder."""
        self._title_label.setText(title)
        self._artist_label.setText(artist)
        self._cover_label.setText(cover_text[:2].upper())

    def set_playing_state(self, is_playing: bool) -> None:
        """Update the play/pause button icon to reflect the current state."""
        self._is_playing = is_playing
        self._play_pause_button.setText("⏸" if is_playing else "▶")

    def set_duration(self, duration_ms: int) -> None:
        """Set the total track duration, in milliseconds, and update the slider range."""
        self._duration_ms = duration_ms
        self._position_slider.setRange(0, duration_ms)
        self._total_time_label.setText(self._format_time(duration_ms))

    def set_position(self, position_ms: int) -> None:
        """Update the current playback position, in milliseconds."""
        # Avoid fighting the user while they are actively dragging the handle.
        if not self._position_slider.isSliderDown():
            self._position_slider.setValue(position_ms)
        self._current_time_label.setText(self._format_time(position_ms))

    def set_volume(self, volume: int) -> None:
        """Set the volume slider position (0-100) without emitting a signal loop."""
        self._volume_slider.blockSignals(True)
        self._volume_slider.setValue(max(0, min(100, volume)))
        self._volume_slider.blockSignals(False)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time(milliseconds: int) -> str:
        """Format milliseconds as M:SS for display."""
        total_seconds = max(0, milliseconds) // 1000
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"
