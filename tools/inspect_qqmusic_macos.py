"""在 macOS 临时目录只读检查官方 QQ 客户端的脚本字典与 URL 接口，不安装或运行客户端。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import sys
import tempfile
from urllib.request import urlopen
import xml.etree.ElementTree as ET


def main():
    if sys.platform != 'darwin':
        raise SystemExit('This contract inspection requires macOS hdiutil.')
    with urlopen('https://formulae.brew.sh/api/cask/qqmusic.json', timeout=30) as response:
        metadata = json.load(response)
    with tempfile.TemporaryDirectory(prefix='qq-contract-') as directory:
        root = Path(directory)
        dmg = root/'qqmusic.dmg'
        digest = hashlib.sha256()
        size = 0
        with urlopen(metadata['url'], timeout=45) as response, dmg.open('wb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 300_000_000:
                    raise ValueError('QQ package exceeds inspection size limit')
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != metadata['sha256']:
            raise ValueError('Official package hash did not match the Homebrew metadata')
        mount = root/'mounted'
        subprocess.run(['hdiutil','attach',str(dmg),'-readonly','-nobrowse','-mountpoint',str(mount)], check=True)
        try:
            app = mount/'QQMusic.app'
            info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
            result = {'version': info.get('CFBundleShortVersionString'),
                      'bundle_id': info.get('CFBundleIdentifier'),
                      'url_types': info.get('CFBundleURLTypes'),
                      'scriptable': info.get('NSAppleScriptEnabled'),
                      'scripting_definition': info.get('OSAScriptingDefinition'),
                      'commands': [], 'properties': [], 'url_tokens': []}
            for definition in (app/'Contents').rglob('*.sdef'):
                tree = ET.parse(definition)
                result['commands'].extend(e.attrib for e in tree.iter('command'))
                result['properties'].extend(e.attrib for e in tree.iter('property'))
            executable = app/'Contents/MacOS'/info['CFBundleExecutable']
            strings = subprocess.run(['strings',str(executable)],capture_output=True,text=True,check=True).stdout
            matches = (line for line in strings.splitlines()
                       if re.search(r'playSonglist|pauseSong|resumeSong|qqmusic://|songmid|songid|playerState|AppleScript|ScriptingDefinition',line,re.I))
            result['url_tokens'] = [line[:350] for line in matches if len(line)<1000][:100]
            Path('qqmusic-macos-contract.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=False,indent=2))
        finally:
            subprocess.run(['hdiutil','detach',str(mount)],check=True)


if __name__ == '__main__':
    main()
