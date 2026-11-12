"""volume.json 缩放：按 N×N×N 归并 → 尺寸缩小到 1/N。

为什么要单独一个脚本、而不是直接用 `vox.py --courses 64` 重切：

- 重切要重新走 150 万面 × 7 点采样 + 全量排序，峰值 1.2 GB 以上。本机页面文件被关闭
  （可提交内存 = 物理内存），空闲只剩 1.2 GB 时 `pal_index` 连 17 MB 都分配不出来
  （`numpy._core._exceptions._ArrayMemoryError`，或 OpenBLAS "Memory allocation still failed"）；
- 归并只读已经验收的 volume.json，几十 MB、2 秒，输出确定性。

代价（必须在交付时说明）：1 格宽的细结构（腿、鹿角尖、手指、衣带）归并后变粗或断开。
所以**先按高分辨率切，再决定要不要减半**，不要一开始就切 64 层。

用法：
  python downscale.py --src work/volume.json --out work_half/volume.json            # 一半
  python downscale.py --src work/volume.json --out work_q/volume.json --factor 4    # 四分之一
"""
import argparse
import json
import math
from collections import Counter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True, help='源 volume.json（高分辨率那份）')
    ap.add_argument('--out', required=True, help='输出去处')
    ap.add_argument('--factor', type=int, default=2, help='归并边长 N：2 = 每个方向减半，总体积 1/8')
    a = ap.parse_args()
    f = max(1, a.factor)

    V = json.load(open(a.src))
    buckets = {}
    for x, y, z, c in V['cells']:
        buckets.setdefault((x // f, y // f, z // f), Counter())[c] += 1

    cells = []
    for (x, y, z), cnt in buckets.items():
        # 众数定色；平票时先到的赢（Counter 保留插入顺序），不做插值——插值会造出模型里没有的颜色
        cells.append([x, y, z, cnt.most_common(1)[0][0]])
    cells.sort()

    out = {
        'nx': math.ceil(V['nx'] / f),
        'ny': math.ceil(V['ny'] / f),
        'nz': math.ceil(V['nz'] / f),
        'up': V['up'], 'front': V['front'], 'cam': V.get('cam', 1),
        'cells': cells,
    }
    json.dump(out, open(a.out, 'w'), separators=(',', ':'))

    print('source %d cells  %dx%dx%d  (%d courses)' % (len(V['cells']), V['nx'], V['ny'], V['nz'], V['ny']))
    print('scaled %d cells  %dx%dx%d  (%d courses)  factor %d'
          % (len(cells), out['nx'], out['ny'], out['nz'], out['ny'], f))
    print('colours', Counter(c[3] for c in cells).most_common())


if __name__ == '__main__':
    main()
