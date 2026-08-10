"""
本模块提供“六毛工作搭子”的离线喂食与对话逻辑，不直接创建窗口或访问网络。

职责范围：
- 定义苹果、饼干和牛奶三种可喂食物；
- 根据饱食度、精力和亲密度生成简短反馈；
- 对问候、工作、疲惫、情绪和吃饭等常见话题给出本地规则回复；
- 返回与回复匹配的宠物表情状态，供窗口层播放现有动作。

所有对话都只在当前进程内处理，不保存聊天文本，也不会发送到网络。
"""

from __future__ import annotations

from dataclasses import dataclass

from .behavior import PetMood, PetState


PET_NAME = "六毛"
APP_DISPLAY_NAME = "六毛工作搭子"


@dataclass(frozen=True)
class FoodOption:
    """描述一种菜单食物及其状态增益。"""

    key: str
    label: str
    fullness_gain: int
    energy_gain: int
    reply: str


FOOD_OPTIONS = (
    FoodOption("apple", "苹果", 18, 2, "苹果脆脆的，谢谢你！今天也一起加油。"),
    FoodOption("cookie", "小饼干", 14, 5, "小饼干收到！工作再忙也要记得喝水。"),
    FoodOption("milk", "热牛奶", 20, 8, "暖暖的牛奶，六毛充好电啦。"),
)
FOOD_BY_KEY = {food.key: food for food in FOOD_OPTIONS}


@dataclass(frozen=True)
class CompanionReply:
    """一次喂食或对话产生的文字与表情反馈。"""

    text: str
    state: PetState


class CompanionModel:
    """根据会话内心情数值生成六毛的喂食和对话反馈。"""

    def __init__(self, mood: PetMood) -> None:
        self.mood = mood

    def status_text(self) -> str:
        """返回适合气泡和菜单展示的当前状态。"""

        return (
            f"亲密 {self.mood.affinity} · 精力 {self.mood.energy} · "
            f"饱食 {self.mood.fullness}"
        )

    def feed(self, food_key: str) -> CompanionReply:
        """喂指定食物并返回克制、友好的即时反馈。"""

        try:
            food = FOOD_BY_KEY[food_key]
        except KeyError as exc:
            raise ValueError(f"未知食物：{food_key}") from exc
        if self.mood.fullness >= 95:
            return CompanionReply(
                "肚子已经圆滚滚啦，先陪你工作一会儿再吃吧。",
                PetState.CURIOUS,
            )
        self.mood.receive_food(food.fullness_gain, food.energy_gain)
        state = PetState.SHY if self.mood.affinity >= 70 else PetState.HAPPY
        return CompanionReply(food.reply, state)

    def reply_to(self, message: str) -> CompanionReply:
        """在本地按关键词回应用户输入，并限制用于回显的文本长度。"""

        text = " ".join(message.split())[:120]
        if not text:
            return CompanionReply("你还没说话呢，六毛在认真听。", PetState.CURIOUS)
        if any(word in text for word in ("你好", "嗨", "早上好", "早安")):
            return CompanionReply(
                "在呢！我是六毛。今天想先完成哪一件事？",
                PetState.WAVE,
            )
        if any(word in text for word in ("累", "困", "压力", "烦")):
            return CompanionReply(
                "先停两分钟，喝口水、松松肩。六毛陪你慢慢来。",
                PetState.SLEEPY,
            )
        if any(word in text for word in ("工作", "任务", "加班", "学习", "论文", "代码")):
            return CompanionReply(
                "把最重要的一件事先做十分钟，六毛在旁边陪你。",
                PetState.HAPPY,
            )
        if any(word in text for word in ("难过", "伤心", "不开心", "失败")):
            return CompanionReply(
                "今天不顺也没关系。先把最小的一步做好，剩下的明天再说。",
                PetState.SHY,
            )
        if any(word in text for word in ("开心", "完成", "成功", "搞定")):
            return CompanionReply("太好啦！六毛给你庆祝一下。", PetState.HAPPY)
        if any(word in text for word in ("饿", "吃", "食物")):
            return CompanionReply(
                f"六毛现在饱食度是 {self.mood.fullness}，右键就可以给我喂食。",
                PetState.CURIOUS,
            )
        if any(word in text for word in ("名字", "你是谁")):
            return CompanionReply(
                "我是六毛，你的桌面工作搭子。",
                PetState.WAVE,
            )
        if any(word in text for word in ("谢谢", "喜欢你")):
            self.mood.receive_affection()
            return CompanionReply("嘿嘿，六毛也喜欢和你一起工作。", PetState.SHY)
        if any(word in text for word in ("再见", "拜拜", "晚安")):
            return CompanionReply("好，六毛在桌面等你回来。", PetState.SLEEPY)
        return CompanionReply(
            f"我听见了：“{text}”。我们把它拆成下一小步吧。",
            PetState.CURIOUS,
        )
