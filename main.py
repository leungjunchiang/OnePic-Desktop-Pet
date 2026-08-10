"""
本模块是 Lili 桌面工作搭子的命令行启动入口。

职责范围：
- 解析可选的 `--smoke-test-ms` 自动退出参数；
- 调用 onepic_desktop_pet.app.run() 启动 Qt 应用；
- 不包含窗口、行为或配置业务逻辑。

Agent 快速定位：
- 命令行参数在 parse_args() 中定义；
- 主入口位于 main()；
- 具体应用生命周期请阅读 src/onepic_desktop_pet/app.py。

输入为命令行参数，输出为进程退出码。正常启动会显示桌面窗口和托盘图标，退出时应用
可能在当前用户本地应用数据目录保存窗口位置。

使用示例：
    py main.py
    py main.py --smoke-test-ms 1500
"""

from __future__ import annotations

import argparse

from onepic_desktop_pet.app import run


def parse_args() -> argparse.Namespace:
    """解析启动参数。"""

    parser = argparse.ArgumentParser(description="启动 Lili 桌面工作搭子")
    parser.add_argument(
        "--smoke-test-ms",
        type=int,
        default=None,
        help="在指定毫秒后自动退出，仅用于启动验证",
    )
    return parser.parse_args()


def main() -> int:
    """启动应用并返回 Qt 事件循环退出码。"""

    args = parse_args()
    return run(smoke_test_ms=args.smoke_test_ms)


if __name__ == "__main__":
    raise SystemExit(main())
