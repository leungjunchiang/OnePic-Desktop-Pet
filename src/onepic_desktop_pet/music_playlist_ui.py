"""展示六毛保存的三十首陈楚生在线歌单，共用实际播放线程和位置。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout


class MusicPlaylistDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle('六毛 · 陈楚生歌单')
        self.resize(560, 620)
        layout = QVBoxLayout(self)
        self.summary = QLabel(self)
        layout.addWidget(self.summary)
        hint = QLabel('六毛保存这 30 首歌的顺序，上一首 / 下一首按此列表播放。\n在线音源由 QQ 音乐提供，需登录并具备相应播放权限。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.songs = QListWidget(self)
        layout.addWidget(self.songs)
        row = QHBoxLayout()
        self.buttons = []
        for label, action in (('上一首', 'previous'), ('播放 / 暂停', 'toggle'), ('下一首', 'next')):
            button = QPushButton(label, self)
            button.clicked.connect(lambda _checked=False, command=action: controller.perform(command))
            self.buttons.append(button)
            row.addWidget(button)
        new = QPushButton('重新随机 30 首', self)
        new.clicked.connect(lambda: controller.play_qq_song())
        self.buttons.append(new)
        row.addWidget(new)
        layout.addLayout(row)
        self.feedback = QLabel(self)
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        controller.result_ready.connect(self._result)
        controller.busy_changed.connect(self._busy)
        self.refresh()
        self._busy(controller.busy)

    def refresh(self):
        queue = self.controller.manager.qq_playlist
        self.summary.setText(f'陈楚生 · {len(queue.entries)} 首 · 当前第 {queue.index + 1} 首'
                             if queue.entries else '还没有歌单，点击“重新随机 30 首”开始。')
        self.songs.clear()
        for index, song in enumerate(queue.entries):
            self.songs.addItem(f'{index + 1:02d}. {song.title} — {song.artist}')
        self.songs.setCurrentRow(queue.index)
        if queue.index >= 0:
            self.songs.scrollToItem(self.songs.item(queue.index))

    def _result(self, result):
        self.refresh()
        self.feedback.setText(result.message)

    def _busy(self, busy):
        for button in self.buttons:
            button.setEnabled(not busy)
