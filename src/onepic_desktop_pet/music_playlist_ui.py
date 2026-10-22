"""展示陈楚生三十首连续轮播歌单，支持选曲及处理中优先暂停。"""

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
        hint = QLabel('每轮随机 30 首，播完自动生成下一轮；可随时暂停。\n双击选曲，上一首 / 下一首按此列表播放。音源由 QQ 音乐在线提供。', self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.songs = QListWidget(self)
        self.songs.itemDoubleClicked.connect(
            lambda item: controller.play_qq_playlist_index(self.songs.row(item)))
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
        self.summary.setText(f'陈楚生 · 第 {queue.round_number} 轮 · {len(queue.entries)} 首 · 当前第 {queue.index + 1} 首'
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
        for index, button in enumerate(self.buttons):
            # 即使正在查询或切歌，播放/暂停仍可提交优先暂停。
            button.setEnabled(index == 1 or not busy)
