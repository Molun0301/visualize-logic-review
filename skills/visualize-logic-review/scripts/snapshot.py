#!/usr/bin/env python3
"""把渲染出的 SVG/HTML 截成 PNG，用来检查实际像素效果（裁切、字体、遮挡、箭头）。

优先使用 Chrome/Chromium/Edge 无头模式，其次 Firefox 无头模式。snap 版浏览器只能读写
~/snap/<浏览器>/common 下的非隐藏目录，脚本会自动把文件中转到那里。
用法：python3 snapshot.py <输出目录或 .svg/.html 文件>... [--html] [--width 1400] [--height 2400] [--scale 1]
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PAD = 140
CHROMES = ('google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'chrome')


def find_browser():
    for name in CHROMES:
        path = shutil.which(name)
        if path:
            return 'chrome', path
    path = shutil.which('firefox')
    if path:
        return 'firefox', path
    for path in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                 '/Applications/Firefox.app/Contents/MacOS/firefox'):
        if os.path.exists(path):
            return ('firefox' if 'Firefox' in path else 'chrome'), path
    return None, None


def svg_size(path):
    head = path.read_text(encoding='utf-8')[:2000]
    w = re.search(r'<svg[^>]*\swidth="([\d.]+)"', head)
    h = re.search(r'<svg[^>]*\sheight="([\d.]+)"', head)
    if not (w and h):
        return None
    return int(float(w.group(1))) + 1, int(float(h.group(1))) + 1


def crop(png, size, scale):
    """有 Pillow 时裁回原尺寸；没有就保留底部空白。"""
    try:
        from PIL import Image
    except ImportError:
        return
    with Image.open(png) as im:
        box = (0, 0, min(im.width, round(size[0] * scale)), min(im.height, round(size[1] * scale)))
        im.crop(box).save(png)


def stage_root(browser_path):
    """snap 版浏览器看不到 /tmp 和隐藏目录，改用它自己的 common 目录中转。"""
    real = os.path.realpath(browser_path)
    if real.startswith('/snap/') or browser_path.startswith('/snap/'):
        name = Path(browser_path).name
        root = Path.home() / 'snap' / name / 'common'
        root.mkdir(parents=True, exist_ok=True)
        return root
    return None


def shoot(kind, browser, source, target, size, scale):
    root = stage_root(browser)
    work = Path(tempfile.mkdtemp(prefix='vlr-shot-', dir=str(root) if root else None))
    try:
        page = work / source.name
        shutil.copyfile(source, page)
        png = work / 'shot.png'
        # 无头浏览器的可视区域比窗口高度少约 80 像素，窗口加高后再裁掉多余部分
        window = '--window-size=%d,%d' % (size[0], size[1] + PAD)
        if kind == 'chrome':
            cmd = [browser, '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
                   '--user-data-dir=' + str(work / 'profile'), '--force-device-scale-factor=%s' % scale,
                   window, '--screenshot=' + str(png), page.as_uri()]
        else:
            profile = work / 'profile'
            profile.mkdir()
            (profile / 'user.js').write_text('user_pref("layout.css.devPixelsPerPx", "%s");\n' % scale, encoding='utf-8')
            cmd = [browser, '--headless', '--no-remote', '--profile', str(profile), '--screenshot', str(png),
                   window, page.as_uri()]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not png.exists():
            raise RuntimeError('截图失败（退出码 %s）：%s' % (result.returncode, (result.stderr or result.stdout).strip()[-400:]))
        crop(png, size, scale)
        shutil.move(str(png), str(target))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('inputs', nargs='+', type=Path, help='render_review.py 的输出目录，或单个 .svg/.html 文件')
    parser.add_argument('--html', action='store_true', help='输出目录时也截 index.html')
    parser.add_argument('--width', type=int, default=1400, help='HTML 截图宽度')
    parser.add_argument('--height', type=int, default=2400, help='HTML 截图高度（只截这一屏）')
    parser.add_argument('--scale', type=float, default=1, help='像素倍率，文字细节看不清时用 2')
    args = parser.parse_args()
    kind, browser = find_browser()
    if not browser:
        print('snapshot: 没有找到 Chrome/Chromium/Edge/Firefox；请直接打开 HTML 查看，并说明视觉检查未完成', file=sys.stderr)
        return 2
    files = []
    for item in args.inputs:
        if item.is_dir():
            files += sorted(item.glob('*.svg'))
            if args.html and (item / 'index.html').exists():
                files.append(item / 'index.html')
        elif item.suffix in ('.svg', '.html') and item.exists():
            files.append(item)
        else:
            print('snapshot: 跳过 %s（不是目录、.svg 或 .html）' % item, file=sys.stderr)
    failed = 0
    for source in files:
        source = source.resolve()
        size = svg_size(source) if source.suffix == '.svg' else (args.width, args.height)
        size = size or (args.width, args.height)
        target = source.with_suffix('.png')
        try:
            shoot(kind, browser, source, target, size, args.scale)
            print(target)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            failed += 1
            print('snapshot: %s: %s' % (source.name, exc), file=sys.stderr)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
