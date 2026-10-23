"""手动打开的轻量歌单：独立可最小化窗口，关闭不影响后台连续播放。"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)
from .music_playback import MusicPlaybackOutcome


class MusicPlaylistDialog(QDialog):
    def __init__(self, controller, parent=None):
        # 不作为置顶桌宠的 owned dialog，避免跟随桌宠压在其他窗口前面。
        super().__init__(None)
        if parent is not None:
            parent.destroyed.connect(self.deleteLater)
            self.setWindowIcon(parent.windowIcon())
        self.controller = controller
        self._entries = ()
        self._index = -1
        self._playing = False
        self.setObjectName('musicPlaylist')
        self.setWindowTitle('陈楚生 · 歌单')
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(350, 410)
        self.setMinimumSize(300, 270)
        font = self.font()
        font.setPointSizeF(9.5)
        self.setFont(font)
        self.setStyleSheet('''
            QDialog#musicPlaylist { background: #f7f9f8; color: #25342e; }
            QLabel#playlistTitle { color: #223d30; font-size: 16px; font-weight: 600; }
            QLabel#playlistSummary, QLabel#playlistState { color: #77857d; font-size: 11px; }
            QListWidget { background: white; color: #33463c; border: 1px solid #e4eae6;
                border-radius: 11px; padding: 5px; outline: none; }
            QListWidget::item { border: none; border-radius: 6px; padding-left: 7px; }
            QListWidget::item:hover { background: #f2f6f3; }
            QListWidget::item:selected { background: #e5f1e9; color: #28583d; }
            QPushButton { background: white; color: #42574a; border: 1px solid #e0e7e2;
                border-radius: 9px; min-height: 31px; padding: 0 10px; }
            QPushButton:hover { background: #edf4ef; border-color: #bdd2c3; }
            QPushButton:pressed { background: #dcece1; }
            QPushButton:disabled { color: #abb4ae; background: #f6f8f6; }
            QPushButton#playlistToggle { background: #e4eee7; color: #315c40; border-color: #d4e3d8; }
            QScrollBar:vertical { background: transparent; width: 6px; margin: 4px 0; }
            QScrollBar::handle:vertical { background: #ced9d1; border-radius: 3px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        title = QLabel('陈楚生', self)
        title.setObjectName('playlistTitle')
        heading.addWidget(title)
        heading.addStretch()
        self.state = QLabel('已就绪', self)
        self.state.setObjectName('playlistState')
        heading.addWidget(self.state)
        layout.addLayout(heading)
        self.summary = QLabel(self)
        self.summary.setObjectName('playlistSummary')
        layout.addWidget(self.summary)
        self.songs = QListWidget(self)
        self.songs.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.songs.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.songs.setUniformItemSizes(True)
        self.songs.setToolTip('双击播放 · 关闭窗口后继续在后台播放')
        self.songs.itemDoubleClicked.connect(
            lambda item: controller.play_qq_playlist_index(self.songs.row(item)))
        layout.addWidget(self.songs, 1)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.buttons = []
        for label, action, name in (('‹', 'previous', '上一首'), ('播放', 'toggle', '播放或暂停'),
                                     ('›', 'next', '下一首')):
            button = QPushButton(label, self)
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setFixedWidth(66 if action == 'toggle' else 34)
            if action == 'toggle':
                button.setObjectName('playlistToggle')
            button.clicked.connect(lambda _checked=False, command=action: controller.perform(command))
            self.buttons.append(button)
            row.addWidget(button)
        row.addStretch()
        new = QPushButton('换一轮', self)
        new.setToolTip('重新随机生成 30 首')
        new.clicked.connect(lambda: controller.play_qq_song())
        self.buttons.append(new)
        row.addWidget(new)
        layout.addLayout(row)
        controller.result_ready.connect(self._result)
        controller.busy_changed.connect(self._busy)
        self.refresh()
        if controller.manager.qq_playlist.auto_advance:
            self._playing = True
            self.state.setText('正在播放')
            self.buttons[1].setText('暂停')
        self._busy(controller.busy)

    def refresh(self):
        queue = self.controller.manager.qq_playlist
        self.summary.setText(f'第 {queue.round_number} 轮  ·  {queue.index + 1:02d} / {len(queue.entries)} 首'
                             if queue.entries else '暂无歌单')
        entries = tuple((s.identifier, s.title, s.artist) for s in queue.entries)
        changed = entries != self._entries
        if changed:
            self.songs.clear()
            for index, song in enumerate(queue.entries):
                item = QListWidgetItem(f'{index + 1:02d}   {song.title}')
                item.setToolTip(f'{song.title}\n{song.artist}')
                item.setSizeHint(QSize(0, 31))
                self.songs.addItem(item)
            self._entries = entries
        if changed or queue.index != self._index:
            self.songs.setCurrentRow(queue.index)
            if queue.index >= 0:
                self.songs.scrollToItem(self.songs.item(queue.index))
            self._index = queue.index

    def _result(self, result):
        self.refresh()
        track = getattr(getattr(result, 'status', None), 'track', None)
        if getattr(result, 'outcome', None) is MusicPlaybackOutcome.PLAYBACK_CONFIRMED:
            self._playing = True
        elif track is not None:
            self._playing = track.playback_status.casefold() == 'playing'
        confirmed = track is not None or getattr(result, 'outcome', None) is MusicPlaybackOutcome.PLAYBACK_CONFIRMED
        self.state.setText(('正在播放' if self._playing else '已暂停') if result.success and confirmed else '请查看 QQ 音乐')
        self.state.setToolTip(result.message)
        self.buttons[1].setText('暂停' if self._playing else '播放')

    def _busy(self, busy):
        self.buttons[1].setText('暂停' if busy or self._playing else '播放')
        for index, button in enumerate(self.buttons):
            button.setEnabled(index == 1 or not busy)
