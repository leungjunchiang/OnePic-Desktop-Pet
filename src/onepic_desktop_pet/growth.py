"""定义六毛的每日成长线、动作素材目录和正向陪伴事件。

本模块只包含不可变配置与纯函数，不创建窗口、不读写文件、不访问网络。每日成长按当天累计
专注时长推进，0–8 小时各有明确状态与奖励；未工作不会扣分、饥饿或生病。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GrowthStage:
    hour: int
    title: str
    reward: str
    activity: str
    message: str


DAILY_GROWTH = (
    GrowthStage(0, "刚来上班", "默认六毛", "stand", "今天刚刚开始，先把第一件小事放到桌面上。"),
    GrowthStage(1, "渐入佳境", "兔子胡萝卜造型", "bunny-carrot", "第一小时完成，六毛换上胡萝卜小外套。"),
    GrowthStage(2, "认真工作", "夜读动作", "night-reading", "两小时啦，你工作，六毛也认真读书。"),
    GrowthStage(3, "摸鱼一下", "海边椰子彩蛋", "coconut", "认真之后也可以摸鱼一下，海风给你留了位置。"),
    GrowthStage(4, "半日达成", "摩托稀有造型", "motorcycle", "半日达成，六毛骑摩托来接你透口气。"),
    GrowthStage(5, "下午犯困", "趴桌睡觉动作", "sleep", "困了不是失败，闭眼几分钟也算照顾今天。"),
    GrowthStage(6, "满血复活", "吉他演出", "guitar", "六小时，六毛给重新出发的你弹一段。"),
    GrowthStage(7, "快下班了", "耳机听歌", "headphones", "快下班啦，剩下的事情慢慢收好就行。"),
    GrowthStage(8, "今日毕业", "荒野国王纪念卡", "wild-king", "今日毕业！不是继续硬撑，是为今天认真收尾。"),
)


ACTION_SPRITES = {
    "stand": "01-stand.png",
    "computer": "02-office.png",
    "office": "02-office.png",
    "headphones": "03-headphones.png",
    "guitar": "04-guitar.png",
    "drums": "05-drums.png",
    "coconut": "06-coconut.png",
    "piano": "07-piano.png",
    "love": "08-love.png",
    "night-reading": "09-night-reading.png",
    "reading": "09-night-reading.png",
    "writing": "02-office.png",
    "sunbath": "10-sunbath.png",
    "pointing": "11-pointing.png",
    "climbing": "12-climbing.png",
    "bunny-carrot": "13-bunny-carrot.png",
    "babuda": "14-babuda.png",
    "overhead-heart": "15-overhead-heart.png",
    "ufo": "16-ufo.png",
    "sleep": "17-sleep.png",
    "aquarium": "18-aquarium.png",
    "tea": "19-tea.png",
    "motorcycle": "20-motorcycle.png",
    "singing": "21-singing.png",
    "thermos": "22-thermos.png",
    "movie": "23-movie.png",
    "tennis": "24-tennis.png",
    "dolphin": "25-dolphin.png",
    "fishing": "26-fishing.png",
    "wild-king": "27-wild-king.png",
    "seagull": "28-seagull.png",
    "flowers": "29-flowers.png",
    "milk-tea": "30-milk-tea.png",
    "feast": "31-feast.png",
    "shells": "32-shells.png",
    "football": "33-football.png",
    "group-photo": "34-group-photo.png",
    "whale": "35-whale.png",
    "demon": "36-demon.png",
    "flower-dance": "37-flower-dance.png",
    "work-cheer": "38-work-cheer.png",
    "work-study": "39-work-study.png",
    "work-flow": "40-work-flow.png",
    "desk-nap": "41-desk-nap.png",
    "daydream": "42-daydream.png",
    "deep-focus": "43-deep-focus.png",
    "work-complete": "44-work-complete.png",
    "overtime": "45-overtime.png",
    "overwhelmed": "46-overwhelmed.png",
}


ACTION_GROUPS = (
    ("专注工作", (("默认站姿", "stand"), ("办公室工作", "office"), ("夜读", "night-reading"), ("指着说", "pointing"), ("拿保温杯", "thermos"))),
    ("休息一下", (("海边椰子", "coconut"), ("日光浴", "sunbath"), ("兔子胡萝卜", "bunny-carrot"), ("巴布达", "babuda"), ("睡觉", "sleep"), ("看金鱼", "aquarium"), ("喝茶", "tea"), ("看电影", "movie"), ("喝奶茶", "milk-tea"), ("饕餮一餐", "feast"))),
    ("音乐演出", (("戴耳机", "headphones"), ("弹吉他", "guitar"), ("打鼓", "drums"), ("唱歌", "singing"), ("弹钢琴", "piano"))),
    ("爱与庆祝", (("爱心比心", "love"), ("头顶比心", "overhead-heart"), ("送花", "flowers"), ("合影", "group-photo"), ("荒野国王", "wild-king"))),
    ("出门冒险", (("登山", "climbing"), ("骑摩托", "motorcycle"), ("UFO 悬挂", "ufo"), ("打网球", "tennis"), ("与海豚游泳", "dolphin"), ("钓螃蟹", "fishing"), ("与海鸥", "seagull"), ("捡贝壳", "shells"), ("踢足球", "football"), ("乘鲸云游", "whale"), ("恶魔毛毛", "demon"))),
    ("工作搭子", (("花环舞步", "flower-dance"), ("开心开工", "work-cheer"), ("埋头读写", "work-study"), ("工作流转", "work-flow"), ("趴桌小睡", "desk-nap"), ("摸鱼走神", "daydream"), ("深度专注", "deep-focus"), ("完成挥手", "work-complete"), ("夜间加班", "overtime"), ("忙到转圈", "overwhelmed"))),
)


FOCUS_ACTIONS = ("office", "work-cheer", "work-study", "deep-focus", "night-reading", "thermos")
REST_ACTIONS = ("tea", "coconut", "sunbath", "sleep", "desk-nap", "daydream", "movie", "headphones")
COMPLETE_ACTIONS = ("love", "overhead-heart", "work-complete", "guitar", "drums", "flower-dance", "flowers", "group-photo")
RANDOM_ACTIONS = ("fishing", "seagull", "football", "aquarium", "singing", "ufo", "whale")


def stage_for_seconds(seconds: int) -> GrowthStage:
    """返回今天累计专注秒数对应的 0–8 小时阶段。"""

    hour = min(8, max(0, int(seconds)) // 3600)
    return DAILY_GROWTH[hour]


def growth_progress_text(seconds: int) -> str:
    """返回当前阶段及到下一节点的正向进度文案。"""

    safe = max(0, int(seconds))
    stage = stage_for_seconds(safe)
    if stage.hour >= 8:
        return f"8/8 今日毕业 · {stage.reward}"
    remaining = (stage.hour + 1) * 3600 - safe
    minutes = max(1, (remaining + 59) // 60)
    next_stage = DAILY_GROWTH[stage.hour + 1]
    return f"{stage.hour}/8 {stage.title} · 再专注 {minutes} 分钟：{next_stage.reward}"


def positive_mood(seconds: int, session_seconds: int = 0) -> str:
    """只根据投入程度给正向心情，不因少工作而惩罚。"""

    if session_seconds >= 90 * 60:
        return "❤️ 默契满格，也该一起休息了"
    hours = max(0, int(seconds)) // 3600
    if hours >= 6:
        return "✨ 精神满满"
    if hours >= 2:
        return "❤️ 默契 +1"
    if seconds > 0:
        return "🌱 慢慢进入状态"
    return "😴 悠闲的一天"


def time_of_day_activity(now: datetime, work_running: bool) -> tuple[str, str]:
    """返回当前时段适合六毛主动出现的动作与文案。"""

    hour = now.hour
    if hour < 5:
        return "sleep", "这么晚还亮着屏幕呀。六毛先躺下，等你一起收工。"
    if hour < 10:
        return "babuda", "早上好，巴布达。先喝口水，再开始第一件事。"
    if hour < 13:
        return "feast", "到饭点啦，工作可以等一会儿，肚子不应该一直等。"
    if hour < 17:
        return ("thermos" if work_running else "tea"), "下午容易犯困，喝口水，肩膀也松一松。"
    if hour < 22:
        return "night-reading", "晚上适合慢慢收尾，不用把所有明天都塞进今天。"
    return "sleep", "六毛已经穿好睡意了。今天做到这里，也很完整。"
