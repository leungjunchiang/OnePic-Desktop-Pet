"""
本模块提供 Lili 的离线喂食、陪伴动作、牢骚、报时与兜底对话逻辑，不访问网络。

职责范围：
- 定义苹果、饼干、牛奶、咖啡和茶等可喂饮品；
- 根据饱食度、精力和亲密度生成简短反馈；
- 定义专注、加油、爱意、庆祝和安慰等可复用陪伴动作；
- 对工作压力、自我怀疑、疲惫、孤独和爱意等常见话题给出本地规则回复；
- 为开始、暂停、完成工作以及定时休息提醒生成温和话语；
- 返回与回复匹配的宠物表情状态，供窗口层播放现有动作。

所有对话都只在当前进程内处理，不保存聊天文本，也不会发送到网络。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from .behavior import PetMood, PetState


PET_NAME = "六毛"
APP_DISPLAY_NAME = "Lili"


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
    FoodOption("coffee", "咖啡", 5, 14, "咖啡香香的！精神回来一点啦，不过今天也别喝得太晚。"),
    FoodOption("tea", "热茶", 7, 7, "捧住这杯热茶，先缓一缓。我们慢慢把事情做好。"),
)
FOOD_BY_KEY = {food.key: food for food in FOOD_OPTIONS}


@dataclass(frozen=True)
class CompanionAction:
    """描述一个菜单陪伴动作、可选话语和播放时长。"""

    key: str
    label: str
    state: PetState
    messages: tuple[str, ...]
    duration_ms: int = 2600
    sequence: tuple[PetState, ...] = ()


COMPANION_ACTIONS = (
    CompanionAction(
        "focus",
        "陪我专注",
        PetState.SIT,
        (
            "六毛坐好陪你。先专注眼前这一小步，别急着一次做完全部。",
            "开始吧，先安静做十分钟。六毛就在旁边守着你。",
        ),
        3600,
    ),
    CompanionAction(
        "encourage",
        "为我加油",
        PetState.WAVE,
        (
            "你不需要一下子变得很厉害，只要比刚才再前进一步。加油！",
            "六毛给你挥挥手：你已经开始了，这本身就很了不起。",
            "不用等状态完美，慢慢做也算前进。Lili 相信你。",
        ),
    ),
    CompanionAction(
        "love",
        "给我一个抱抱",
        PetState.SHY,
        (
            "抱抱你。辛苦和脆弱都可以被看见，你不用一直逞强。",
            "给你一个软乎乎的抱抱。无论今天完成多少，你都值得被喜欢。",
            "六毛贴贴你一下。休息不是偷懒，是在照顾认真生活的自己。",
        ),
        3200,
    ),
    CompanionAction(
        "celebrate",
        "庆祝完成",
        PetState.HAPPY,
        (
            "完成啦！先认真夸夸自己，再去迎接下一件事。",
            "叮——今日成就已点亮！六毛为你的坚持鼓掌。",
            "做到了！不管成果大小，这一步都值得庆祝。",
        ),
        3000,
    ),
    CompanionAction(
        "comfort",
        "安慰我一下",
        PetState.SHY,
        (
            "没关系，今天走得慢一点也仍然是在向前。六毛陪着你。",
            "先别急着责怪自己。你已经承担了很多，可以喘一口气。",
            "事情没做好，不等于你不好。我们休息一下，再从最小的一步开始。",
        ),
        3400,
    ),
    CompanionAction(
        "rest",
        "提醒我休息",
        PetState.SLEEPY,
        (
            "眼睛离开屏幕一会儿吧。喝口水，转转肩颈，再回来也不迟。",
            "现在适合站起来走两步。真正长久的努力，也要给身体留余地。",
        ),
        3400,
    ),
    CompanionAction(
        "stretch",
        "一起伸个懒腰",
        PetState.WAVE,
        (
            "手臂伸长，肩膀放松——好，别把自己折成办公椅的形状啦。",
            "起来伸展十秒钟吧。六毛也活动一下这六根毛。",
        ),
        3600,
        (PetState.SIT, PetState.WAVE, PetState.HAPPY),
    ),
    CompanionAction(
        "think",
        "一起想办法",
        PetState.CURIOUS,
        (
            "先把问题说成一句话，再列三个可能的下一步。复杂的事也能一点点拆开。",
            "六毛开始转小脑筋了：我们先找最卡住的那一处。",
        ),
        4200,
        (PetState.CURIOUS, PetState.SIT, PetState.CURIOUS),
    ),
    CompanionAction(
        "quiet",
        "安静陪我一会儿",
        PetState.SIT,
        (
            "好，不催你，也不讲话。六毛就坐在这里陪你。",
            "这一会儿不用证明什么，安静呼吸、慢慢做就好。",
        ),
        5200,
        (PetState.SIT, PetState.IDLE, PetState.SIT),
    ),
    CompanionAction(
        "victory",
        "击掌庆祝",
        PetState.HAPPY,
        (
            "啪！漂亮的一步。今天的你值得一个认真击掌！",
            "完成就是完成，不许偷偷把自己的努力打折。击掌！",
        ),
        3400,
        (PetState.WAVE, PetState.HAPPY, PetState.WAVE),
    ),
)
ACTION_BY_KEY = {action.key: action for action in COMPANION_ACTIONS}


@dataclass(frozen=True)
class CompanionReply:
    """一次喂食或对话产生的文字与表情反馈。"""

    text: str
    state: PetState


class CompanionModel:
    """根据会话内心情数值生成 Lili 的喂食和对话反馈。"""

    def __init__(
        self,
        mood: PetMood,
        random_source: random.Random | None = None,
    ) -> None:
        self.mood = mood
        self.random = random_source or random.Random()

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

    def perform_action(self, action_key: str) -> CompanionReply:
        """生成一个由用户主动选择的陪伴动作与话语。"""

        try:
            action = ACTION_BY_KEY[action_key]
        except KeyError as exc:
            raise ValueError(f"未知陪伴动作：{action_key}") from exc
        if action_key in {"love", "comfort"}:
            self.mood.receive_affection()
        return CompanionReply(self.random.choice(action.messages), action.state)

    def work_started(self, resumed: bool = False) -> CompanionReply:
        """返回开始或继续工作计时时的陪伴反馈。"""

        if resumed:
            text = "计时已经在进行啦。别急，保持现在的节奏就很好。"
        else:
            text = "工作计时开始。先把眼前最重要的一小步做好，六毛陪你。"
        return CompanionReply(text, PetState.SIT)

    def work_paused(self, duration_text: str) -> CompanionReply:
        """返回暂停计时时的休息劝慰。"""

        return CompanionReply(
            f"计时暂停，今天已经工作 {duration_text}。喝口水、看看远处再继续吧。",
            PetState.SLEEPY,
        )

    def work_finished(self, duration_text: str) -> CompanionReply:
        """返回完成本次工作时的庆祝反馈。"""

        return CompanionReply(
            f"辛苦啦！今天累计工作 {duration_text}。做到这里已经值得好好夸奖。",
            PetState.HAPPY,
        )

    def work_reminder(self, kind: str, duration_text: str) -> CompanionReply:
        """根据连续工作阶段返回鼓励或休息提醒。"""

        if kind == "focus":
            return CompanionReply(
                f"已经专注 {duration_text} 了，你的投入正在一点点变成成果。继续稳稳地做吧！",
                PetState.HAPPY,
            )
        if kind == "break":
            return CompanionReply(
                f"连续工作 {duration_text} 啦。先离开屏幕、喝水活动几分钟，休息好才走得远。",
                PetState.SLEEPY,
            )
        if kind == "long_break":
            return CompanionReply(
            f"你已经连续工作 {duration_text}。六毛认真劝你停一停：吃点东西、走一走，别拿身体硬撑。",
                PetState.SLEEPY,
            )
        raise ValueError(f"未知工作提醒：{kind}")

    def reply_to(self, message: str) -> CompanionReply:
        """在本地按关键词回应用户输入，并限制用于回显的文本长度。"""

        text = " ".join(message.split())[:120]
        if not text:
            return CompanionReply("你还没说话呢，六毛在认真听。", PetState.CURIOUS)
        if any(word in text for word in ("你好", "嗨", "早上好", "早安")):
            return CompanionReply(
                "在呢！我是六毛。巴布达！今天想先完成哪一件事？",
                PetState.WAVE,
            )
        if any(word in text for word in ("爱你", "喜欢你", "想你", "抱抱", "亲亲")):
            self.mood.receive_affection()
            return CompanionReply(
                "六毛也很爱你。无论今天顺不顺利，你都不是孤零零的一个人。",
                PetState.SHY,
            )
        if any(word in text for word in ("做不到", "没用", "很笨", "差劲", "不配")):
            return CompanionReply(
                "一次做不到，不代表你不行。先别用最重的话责怪自己，我们只试下一小步。",
                PetState.SHY,
            )
        if any(word in text for word in ("崩溃", "忙不过来", "来不及", "事情太多")):
            return CompanionReply(
                "先不要求自己解决全部。写下最急的一件事，其余先放到一边，Lili 陪你慢慢理顺。",
                PetState.SLEEPY,
            )
        if any(word in text for word in ("累", "困", "压力", "烦", "撑不住")):
            return CompanionReply(
                "你已经撑了很久。先停两分钟，喝口水、松松肩；休息不会否定你的努力。",
                PetState.SLEEPY,
            )
        if any(word in text for word in ("拖延", "不想做", "没动力", "摸鱼")):
            return CompanionReply(
                "不用逼自己立刻充满动力。把文件打开，只做五分钟，开始以后会轻一点。",
                PetState.CURIOUS,
            )
        if any(word in text for word in ("出错", "搞砸", "被骂", "挨批", "犯错")):
            return CompanionReply(
                "错误是需要处理的事情，不是对你的判决。先修能修的一处，别把责任变成自我攻击。",
                PetState.SHY,
            )
        if any(word in text for word in ("孤独", "一个人", "没人懂", "没人陪")):
            return CompanionReply(
                "六毛在这里听你说。先照顾好此刻的自己，也可以找信任的人聊一小会儿。",
                PetState.SHY,
            )
        if any(word in text for word in ("工作", "任务", "加班", "学习", "论文", "代码")):
            return CompanionReply(
                "把最重要的一件事先做十分钟，不求完美，只求开始。六毛在旁边陪你。",
                PetState.HAPPY,
            )
        if any(word in text for word in ("难过", "伤心", "不开心", "失败", "委屈")):
            return CompanionReply(
                "今天不顺也没关系。你的感受值得被照顾，先让自己缓一缓，剩下的可以以后再说。",
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
                "我是六毛，住在 Lili 里的小小工作搭子。没网时我也会留在这里陪你。",
                PetState.WAVE,
            )
        if any(word in text for word in ("谢谢", "感谢")):
            self.mood.receive_affection()
            return CompanionReply("不用客气。能陪你认真生活和工作，六毛也很开心。", PetState.SHY)
        if any(word in text for word in ("再见", "拜拜", "晚安")):
            return CompanionReply("好，六毛在桌面等你回来。", PetState.SLEEPY)
        return CompanionReply(
            f"我听见了：“{text}”。我们把它拆成下一小步吧。",
            PetState.CURIOUS,
        )

    def ambient_grumble(self, work_running: bool = False) -> CompanionReply:
        """返回一条不打扰、不会触发联网请求的随机桌面牢骚。"""

        common = (
            ("今天的待办怎么还会自己长出来……算啦，先抓住最重要的一件。", PetState.ANNOYED),
            ("这个屏幕看久了，连六根毛都快坐扁了。要不要伸个懒腰？", PetState.SLEEPY),
            ("六毛刚才认真想了一下：休息五分钟不会让世界停转。", PetState.CURIOUS),
            ("咖啡负责香，工作还是得一小口一小口做。", PetState.SIT),
            ("有些任务看起来像山，其实第一步只是打开文件。", PetState.CURIOUS),
        )
        working = (
            ("你负责专注，六毛负责在旁边替你瞪着进度条。", PetState.SIT),
            ("已经很认真啦，肩膀可以不用跟着一起加班。", PetState.SLEEPY),
        )
        text, state = self.random.choice(common + (working if work_running else ()))
        return CompanionReply(text, state)

    @staticmethod
    def hourly_announcement(hour: int) -> CompanionReply:
        """返回一条克制的整点报时和轻量关怀。"""

        normalized = int(hour) % 24
        if 6 <= normalized < 11:
            advice = "早上好，先喝口水，再开始今天最重要的一件事。"
            state = PetState.WAVE
        elif 11 <= normalized < 14:
            advice = "午间到了，别忘了好好吃饭。"
            state = PetState.HAPPY
        elif 14 <= normalized < 18:
            advice = "下午也不用硬冲，稳稳推进就很好。"
            state = PetState.SIT
        elif 18 <= normalized < 23:
            advice = "晚上啦，记得给工作留一个收尾。"
            state = PetState.SLEEPY
        else:
            advice = "时间不早了，六毛建议你尽快休息。"
            state = PetState.SLEEPY
        return CompanionReply(f"现在是 {normalized:02d}:00。{advice}", state)

    def song_inspiration(self, late_wakeup: bool = False) -> CompanionReply:
        """返回受歌名意象启发的原创短句，不复刻受版权保护的歌词。"""

        if late_wakeup:
            return CompanionReply("起晚啦？别再把今天的光年荒废掉。洗把脸，我们从这一刻出发。", PetState.CURIOUS)
        choices = (
            "风起的时候也别慌，先把手边这一页写完。",
            "有人告诉你也好，无人提醒也好，今天记得照顾自己。",
            "思念可以停一会儿，眼前的小事也值得认真完成。",
            "六毛把今天当成一段前奏：慢一点，也仍然在往前。",
            "经过一段不好走的路，也不妨停下来看看自己走了多远。",
            "山楂花会在自己的季节开，你也不用抢别人的进度。",
            "别把这一整天都判给焦虑，先赎回接下来的十分钟。",
            "窗外的风替你翻篇，六毛替你守住没做完的小愿望。",
            "今天不必证明一切，认真完成一件小事就很了不起。",
            "荒野很大，但你脚下这一小步也算方向。",
            "有些答案要晚一点来，先把茶喝完，再等一会儿。",
            "想念可以轻轻放在桌角，等忙完了再好好抱一抱。",
        )
        return CompanionReply(self.random.choice(choices), PetState.SIT)
