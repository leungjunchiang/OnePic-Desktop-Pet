"""QQ 隔离入口回传阶段耗时和匿名控件计数；诊断写入冲突不能中断控制。"""
from __future__ import annotations
import json
from pathlib import Path
import sys
import tempfile
import time


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
    started, stages, operating = time.monotonic(), [], False
    progress_write_failures = 0
    def progress(stage):
        nonlocal progress_write_failures
        stamp = {'stage': stage, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        if not stages or stages[-1]['stage'] != stage:
            stages.append(stamp)
        # Only fixed phase names and times: no paths, UI labels or account data.
        pending = output.with_name('progress.tmp')
        try:
            pending.write_text(json.dumps({'stage': stage, 'stages': stages[-80:],
                                          'operating': operating}), encoding='utf-8')
            pending.replace(output.with_name('progress.json'))
        except OSError:
            # Windows readers/antivirus can briefly deny replacement. This is
            # optional telemetry, not a failed QQ command. Final reply retains
            # all stages after the parent has stopped reading the progress file.
            progress_write_failures += 1
    try:
        progress('import-uia')
        import uiautomation as auto
        progress('initialize-uia')
        with auto.UIAutomationInitializerInThread():
            operating = True
            progress('operation-start')
            if action == 'probe':
                assert auto.GetRootControl() is not None
                value = 'ready'
            elif action in {'read', 'set', 'startup'}:
                from .qqmusic_queue_mode import _WindowsQQModeNativeBackend, MODES
                backend = _WindowsQQModeNativeBackend(lambda: Path(executable) if executable else None,
                                                      progress=progress)
                if action == 'set' and target not in MODES:
                    raise ValueError('不支持的 QQ 播放模式。')
                value = backend.startup_state() if action == 'startup' else backend.read() if action == 'read' else backend.set(target)
            else:
                raise ValueError('不支持的 QQ 模式操作。')
        reply = {'ok': True, 'value': value}
        if action == 'startup':
            reply['startup_diagnostics'] = backend.startup_diagnostics
    except Exception as exc:
        reply = {'ok': False, 'error': f'QQ 模式访问失败（{type(exc).__name__}）。',
                 'failed_stage': stages[-1]['stage'] if stages else 'worker-start'}
    try:
        progress('complete')
        reply['stages'] = stages[-80:]
        reply['progress_write_failures'] = progress_write_failures
        with output.open('x', encoding='utf-8') as stream:
            json.dump(reply, stream, ensure_ascii=False)
    except OSError:
        return 2
    return 0 if reply['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(run_mode_worker(sys.argv[1:]))
