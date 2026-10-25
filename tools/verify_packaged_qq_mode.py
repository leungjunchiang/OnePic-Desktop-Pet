"""冻结 QQ 辅助入口验收：核对只读结果及阶段诊断，不启动桌宠、QQ 或音频。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def verify_reply(result, expected):
    assert result.get('ok') is True and result.get('value') == expected, result
    stages = result.get('stages')
    assert isinstance(stages, list) and stages, result
    assert {'import-uia', 'initialize-uia', 'operation-start', 'complete'} <= {s['stage'] for s in stages}
    allowed = {'import-uia', 'initialize-uia', 'operation-start', 'startup-controls',
               'read-controls', 'native-mode-command', 'mode-readback', 'uia-mode-action', 'complete'}
    assert all(set(s) == {'stage', 'elapsed_ms'} and s['stage'] in allowed
               and type(s['elapsed_ms']) is int and s['elapsed_ms'] >= 0 for s in stages), result
    assert [s['elapsed_ms'] for s in stages] == sorted(s['elapsed_ms'] for s in stages)
    failures = result.get('progress_write_failures')
    assert type(failures) is int and failures >= 0, result


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='lili-qq-mode-') as directory:
        reply = Path(directory)/'reply.json'
        subprocess.run([str(executable), '--lili-qq-mode-worker', 'probe', '', '', str(reply)],
                       timeout=25, check=True)
        result = json.loads(reply.read_text(encoding='utf-8'))
        verify_reply(result, 'ready')
    with tempfile.TemporaryDirectory(prefix='lili-qq-mode-') as directory:
        reply = Path(directory)/'reply.json'
        subprocess.run([str(executable), '--lili-qq-mode-worker', 'startup', '', '', str(reply)],
                       timeout=25, check=True)
        verify_reply(json.loads(reply.read_text(encoding='utf-8')), 'loading')
    print('Packaged QQ mode/COM and safe startup-readiness helper verified')


if __name__ == '__main__':
    main()
