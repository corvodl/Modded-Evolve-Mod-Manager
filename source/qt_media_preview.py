"""Local media preview; never loads remote URLs or modifies asset bytes."""
from pathlib import Path
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSlider

AUDIO_SUFFIXES = {'.wav', '.mp3', '.ogg', '.oga', '.flac', '.m4a', '.aac', '.opus', '.wma'}
VIDEO_SUFFIXES = {'.mp4', '.webm', '.mkv', '.avi', '.mov', '.wmv', '.ogv'}
BANK_SUFFIXES = {'.fsb', '.fev', '.bank', '.bnk', '.wem'}


def is_media(path):
    return Path(path).suffix.casefold() in AUDIO_SUFFIXES | VIDEO_SUFFIXES | BANK_SUFFIXES


class MediaPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = None
        self.video = None
        layout = QVBoxLayout(self)
        self.message = QLabel('Choose an audio or video file.')
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        controls = QHBoxLayout()
        self.play = QPushButton('Play / Pause')
        self.stop = QPushButton('Stop')
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.setAccessibleName('Playback position')
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(50)
        self.volume.setAccessibleName('Volume')
        self.volume.setMaximumWidth(130)
        controls.addWidget(self.play)
        controls.addWidget(self.stop)
        controls.addWidget(self.seek, 1)
        controls.addWidget(QLabel('Volume'))
        controls.addWidget(self.volume)
        layout.addLayout(controls)
        self.controls = (self.play, self.stop, self.seek, self.volume)
        self._enabled(False)
        self.play.clicked.connect(self._toggle)
        self.stop.clicked.connect(self._stop)

    def _enabled(self, enabled):
        for control in self.controls:
            control.setEnabled(enabled)

    def _ensure_player(self):
        if self.player is not None:
            return
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        from PySide6.QtMultimediaWidgets import QVideoWidget
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(self.volume.value() / 100)
        self.player.setAudioOutput(self.audio)
        self.video = QVideoWidget(self)
        self.layout().insertWidget(1, self.video, 1)
        self.player.setVideoOutput(self.video)
        self.player.durationChanged.connect(lambda duration: self.seek.setMaximum(min(duration, 2147483647)))
        self.player.positionChanged.connect(lambda position: self.seek.setValue(position) if not self.seek.isSliderDown() else None)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        self.player.errorOccurred.connect(self._error)

    def _error(self, *_):
        self.message.setText('Playback unavailable: ' + self.player.errorString() +
                             '\nRaw export remains available. Codec support depends on the media backend.')
        self._enabled(False)

    def open_file(self, path):
        self.clear()
        path = Path(path)
        suffix = path.suffix.casefold()
        if suffix in BANK_SUFFIXES:
            self.message.setText(path.name + '\nGame audio bank / encoded audio. A matching game-audio decoder is required; raw export is available.')
            return
        try:
            self._ensure_player()
            self.video.setVisible(suffix in VIDEO_SUFFIXES)
            self.message.setText(path.name + '\nLocal preview • press Play to listen/watch. Codec support varies.')
            self._enabled(True)
            self.player.setSource(QUrl.fromLocalFile(str(path.resolve(strict=True))))
        except (ImportError, OSError, RuntimeError) as exc:
            self.message.setText('Media preview unavailable: ' + str(exc) + '\nRaw export is available.')
            self._enabled(False)

    def _toggle(self):
        if self.player:
            if self.player.playbackState() == self.player.PlaybackState.PlayingState:
                self.player.pause()
            else:
                self.player.play()

    def _stop(self):
        if self.player:
            self.player.stop()

    def clear(self):
        if self.player:
            self.player.stop()
            self.player.setSource(QUrl())
        if self.video:
            self.video.hide()
        self.seek.setRange(0, 0)
        self._enabled(False)

    def hideEvent(self, event):
        self._stop()
        super().hideEvent(event)
