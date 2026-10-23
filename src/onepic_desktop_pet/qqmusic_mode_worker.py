"""QQ 模式专用辅助入口：不加载 Qt/账号，仅在当前用户随机临时目录回传一次结果。"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile


def run_mode_worker(args):
    if len(args) != 4:
        return 2
    action, executable, target, destination = args
    output = Path(destination).resolve()
    temporary = Path(tempfile.gettempdir()).resolve()
    if (output.name != 'reply.json' or not output.parent.name.startswith('lili-qq-mode-')
            or output.parent.parent != temporary or not output.parent.is_dir()):
        return 2
    reply = {'ok': False, 'error': 'QQ 模式请求无效。'}
    try:
        if action == 'probe':
            import uiautomation as auto
            with auto.UIAutomationInitializerInThread():
                assert auto.GetRootControl() is not None
            value = 'ready'
        elif action in {'read', 'set'}:
            from .qqmusic_queue_mode import _WindowsQQModeNativeBackend, MODES
            backend = _WindowsQQModeNativeBackend(lambda: Path(executable) if executable else None)
            if action == 'set' and target not in MODES:
                raise ValueError('不支持的 QQ 播放模式。')
            value = backend.read() if action == 'read' else backend.set(target)
        else:
            raise ValueError('不支持的 QQ 模式操作。')
        reply = {'ok': True, 'value': value}
    except Exception as exc:
        reply = {'ok': False, 'error': f'QQ 模式访问失败（{type(exc).__name__}）。'}
    try:
        with output.open('x', encoding='utf-8') as stream:
            json.dump(reply, stream, ensure_ascii=False)
    except OSError:
        return 2
    return 0 if reply['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(run_mode_worker(sys.argv[1:]))
