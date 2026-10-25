"""音乐入口的统一渠道名称，供菜单、快捷口袋和操作提示共同使用。"""

QQ_RANDOM_LABEL = "随机听陈楚生（QQ音乐渠道）"
NETEASE_WEB_LABEL = "听陈楚生（网易云网页渠道）"
OTHER_WEB_LABEL = "其他网页渠道"
OTHER_WEB_CHANNELS = (
    ("qq", "QQ音乐网页渠道"),
    ("apple", "Apple Music网页渠道"),
    ("kugou", "酷狗网页渠道"),
    ("qishui", "汽水网页渠道"),
    ("auto", "系统默认网页渠道"),
)
WEB_CHANNEL_NAMES = {"netease": "网易云网页渠道", **dict(OTHER_WEB_CHANNELS)}
