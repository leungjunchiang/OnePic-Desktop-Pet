"""把本机计时与互动统计渲染成可分享的“六毛工作日报”PNG。

工作卡不含任务名称、聊天内容或窗口信息，只展示累计时长、完成段数、最长专注、互动次数、
正向心情和当天成长奖励。生成文件只写入本机六毛相册目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen, QPixmap

from .diary import album_directory
from .growth import growth_progress_text, positive_mood, stage_for_seconds
from .work_timer import format_work_duration


def _report_font(point_size: int, *, bold: bool = False) -> QFont:
    """加载系统中文字体，兼顾无界面测试和 Windows/macOS 安装包。"""

    candidates: tuple[Path, ...]
    if sys.platform == "win32":
        candidates = (
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        )
    elif sys.platform == "darwin":
        candidates = (
            Path("/System/Library/Fonts/PingFang.ttc"),
            Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        )
    else:
        candidates = (
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        )
    family = ""
    for path in candidates:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            family = families[0]
            break
    font = QFont(family or "sans-serif", point_size)
    font.setBold(bold)
    return font


def _plain_mood_text(text: str) -> str:
    """工作卡使用纯文字，避免不同系统缺少彩色表情字体时出现方框。"""

    for prefix in ("❤️ ", "✨ ", "🌱 ", "😴 "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def render_daily_report(
    today_seconds: int,
    stats: dict[str, int | str],
    pet_photo: QPixmap,
    output_dir: Path | None = None,
) -> Path:
    """渲染并保存当天工作卡，返回 PNG 路径。"""

    seconds = max(0, int(today_seconds))
    stage = stage_for_seconds(seconds)
    width, height = 760, 980
    card = QPixmap(width, height)
    card.fill(QColor("#eaf1f5"))
    painter = QPainter(card)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 232))
    painter.drawRoundedRect(QRectF(38, 34, width - 76, height - 68), 32, 32)

    title_font = _report_font(25, bold=True)
    body_font = _report_font(14)
    small_font = _report_font(11)
    painter.setFont(title_font); painter.setPen(QColor("#263442"))
    painter.drawText(QRectF(72, 62, 616, 48), Qt.AlignmentFlag.AlignCenter, "六毛工作日报")
    painter.setFont(small_font); painter.setPen(QColor("#667685"))
    painter.drawText(QRectF(72, 112, 616, 28), Qt.AlignmentFlag.AlignCenter, str(stats.get("date", "")))

    photo = QPixmap(pet_photo)
    if not photo.isNull():
        photo = photo.scaled(330, 330, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((width - photo.width()) // 2, 145, photo)

    painter.setPen(QPen(QColor(210, 222, 229), 2))
    painter.drawLine(92, 495, width - 92, 495)
    painter.setFont(body_font); painter.setPen(QColor("#2c3945"))
    rows = (
        ("今天陪你工作", format_work_duration(seconds)),
        ("完成专注段", f"{int(stats.get('completed_tasks', 0))} 个"),
        ("最长连续专注", format_work_duration(int(stats.get("longest_focus_seconds", 0)))),
        ("摸六毛", f"{int(stats.get('touches', 0))} 次"),
        ("六毛睡觉", f"{int(stats.get('sleeps', 0))} 次"),
        ("随机相遇", f"{int(stats.get('random_events', 0))} 次"),
    )
    y = 525
    for label, value in rows:
        painter.drawText(QRectF(105, y, 310, 34), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        painter.drawText(QRectF(415, y, 235, 34), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, value)
        y += 46

    painter.setBrush(QColor("#dff3ee")); painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRectF(84, 808, width - 168, 105), 20, 20)
    painter.setFont(body_font); painter.setPen(QColor("#245b50"))
    painter.drawText(
        QRectF(105, 822, width - 210, 30),
        Qt.AlignmentFlag.AlignCenter,
        _plain_mood_text(positive_mood(seconds)),
    )
    painter.setFont(small_font)
    painter.drawText(QRectF(105, 854, width - 210, 24), Qt.AlignmentFlag.AlignCenter, f"今日解锁：{stage.reward}")
    painter.drawText(QRectF(105, 879, width - 210, 24), Qt.AlignmentFlag.AlignCenter, growth_progress_text(seconds))
    painter.end()

    target_dir = output_dir or album_directory()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{stats.get('date', 'today')}-六毛工作日报.png"
    if not card.save(str(target), "PNG"):
        raise OSError(f"无法保存六毛工作日报：{target}")
    return target
