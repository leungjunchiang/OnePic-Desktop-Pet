"""验证聊天流式回复不会因频繁重绘造成闪烁。"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from onepic_desktop_pet.chat import ChatDialog


def test_streaming_reply_buffers_deltas_and_renders_final_answer_once() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = ChatDialog()

    dialog.begin_streaming_message("六毛")
    dialog.append_streaming_delta("你")
    dialog.append_streaming_delta("好")

    assert not dialog._stream_flush_timer.isActive()
    assert dialog._transcript_entries[-1] == ("六毛", "")

    dialog.finish_streaming_message("你好，今天也陪你。")
    app.processEvents()

    assert dialog._transcript_entries[-1] == ("六毛", "你好，今天也陪你。")
    assert "你好，今天也陪你。" in dialog.transcript.toPlainText()

    dialog.close()
    dialog.deleteLater()
    app.processEvents()
