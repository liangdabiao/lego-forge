"""切片结果体检：颜色分布 / 某区域 ASCII 色图 / 单点深度剖面。

用法（在本目录下，或把本目录加入 sys.path）：
  python probe.py --vol volume.json --stats
  python probe.py --vol volume.json --layers 88:104 --cols 34:72
  python probe.py --vol volume.json --profile 41,72

三类问题一查就清楚：
  1. --stats          各层颜色分布，看有没有"整层单色"或"某个颜色爆量"（数值量级错）
  2. --layers/--cols  把正面（+Z 最前）打成 ASCII 色图，看五官/花纹在不在（缺细节）
  3. --profile        某一格从远到近逐格列颜色，看"该露出来的面是不是被别的格子挡住了"（几何被埋）

深度剖面的判读最关键：如果目标色出现在 z=42，而 z=44/45 还有别的格子，
说明可见面被回填盖住了 —— 这不是颜色问题，是 carve / 洪泛的问题。
"""
import argparse
import json
from collections import Counter

CH = {'Bright Light Orange': 'S', 'Black': '#', 'Orange': 'o', 'Yellow': 'y', 'Red': 'R',
      'Dark Red': 'r', 'Green': 'G', 'Bright Green': 'g', 'White': 'W', 'Tan': 't',
      'Light Nougat': 'n', 'Reddish Brown': 'b', 'Dark Tan': 'T', 'Dark Brown': 'B',
      'Dark Orange': 'd'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vol', required=True)
    ap.add_argument('--stats', action='store_true', help='整体 + 逐层颜色分布')
    ap.add_argument('--layers', help='层范围 b0:b1（不含 b1），配合 --cols 打正面 ASCII 图')
    ap.add_argument('--cols', help='列范围 a0:a1（不含 a1）')
    ap.add_argument('--profile', help='列,层 —— 打印该格从远到近的颜色剖面')
    args = ap.parse_args()

    V = json.load(open(args.vol, encoding='utf-8'))
    cells = V['cells']
    ny = V['ny']
    print('grid %d x %d x %d, cells %d' % (V['nx'], ny, V['nz'], len(cells)))

    if args.stats:
        print('\n整体颜色:', Counter(c for _, _, _, c in cells).most_common())
        by = {}
        for a, b, d, c in cells:
            by.setdefault(b, Counter())[c] += 1
        print('\n逐层颜色（只列前 4）:')
        for b in sorted(by):
            print('  layer %3d  %5d cells  %s' % (b, sum(by[b].values()), by[b].most_common(4)))

    front = {}
    for a, b, d, c in cells:
        k = (a, b)
        if k not in front or d > front[k][0]:
            front[k] = (d, c)

    if args.layers:
        b0, b1 = (int(x) for x in args.layers.split(':'))
        a0, a1 = (int(x) for x in args.cols.split(':')) if args.cols else (0, V['nx'])
        print('\n正面 ASCII（行 = 层，从高到低；列 = %d..%d）' % (a0, a1))
        print('     ' + ''.join(str(a // 10 % 10) for a in range(a0, a1)))
        print('     ' + ''.join(str(a % 10) for a in range(a0, a1)))
        for b in range(b1 - 1, b0 - 1, -1):
            line = ''.join(CH.get(front[(a, b)][1], '?') if (a, b) in front else '.'
                           for a in range(a0, a1))
            print('%3d  %s' % (b, line))
        print('     ' + ''.join(str(a // 10 % 10) for a in range(a0, a1)))
        print('     ' + ''.join(str(a % 10) for a in range(a0, a1)))

    if args.profile:
        a, b = (int(x) for x in args.profile.split(','))
        row = {d: c for aa, bb, d, c in cells if aa == a and bb == b}
        print('\n深度剖面 a=%d b=%d（z 从小到大 = 从后到前）' % (a, b))
        for d in sorted(row):
            print('  z=%3d  %s' % (d, row[d]))
        if row:
            print('  最前 = z=%d %s' % (max(row), row[max(row)]))


if __name__ == '__main__':
    main()
