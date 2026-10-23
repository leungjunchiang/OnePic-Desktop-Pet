"""冻结版本 QQ 模式辅助入口验收：只读 COM 就绪，不启动桌宠、账号或音频。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    executable = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='lili-qq-mode-') as directory:
        reply = Path(directory)/'reply.json'
        subprocess.run([str(executable), '--lili-qq-mode-worker', 'probe', '', '', str(reply)],
                       timeout=20, check=True)
        result = json.loads(reply.read_text(encoding='utf-8'))
        assert result == {'ok': True, 'value': 'ready'}, result
    print('Packaged QQ mode helper/COM startup verified')


if __name__ == '__main__':
    main()
