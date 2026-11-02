#!/usr/bin/env python3
"""安装、更新或卸载逻辑图技能。"""
import argparse
import os
from pathlib import Path
from lifecycle import Installation
ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('action', choices=['install', 'uninstall'])
p.add_argument('--target', choices=['codex', 'claude', 'both'], default='both')
p.add_argument('--home', type=Path, default=Path.home())
p.add_argument('--replace', action='store_true', help='明确替换已有的同名技能；卸载可恢复旧文件')
a = p.parse_args()
try:
    for target in (['codex', 'claude'] if a.target == 'both' else [a.target]):
        directory = a.home / ('.agents' if target == 'codex' else '.claude') / 'skills'
        install = Installation(directory, 'visualize-logic-review', '1.0.0')
        if a.action == 'uninstall':
            install.uninstall()
        else:
            source = ROOT / 'skills'
            files = {str(f.relative_to(source)): f.read_bytes() for f in source.rglob('*') if f.is_file() and '__pycache__' not in f.parts}
            install.install(files, a.replace)
        print(f'{target}: {a.action} 完成，{directory}')
except (ValueError, OSError) as exc:
    p.exit(1, str(exc) + '\n')
