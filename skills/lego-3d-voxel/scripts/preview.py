"""volume.json → 三视图 + 分层图（验收用，必须在 build 之前看）。

  python preview.py --vol volume.json --outdir .

产出：
  view_front.png / view_back.png / view_side.png  正交三视图
  layers.png                                      若干层的俯视切片拼版（看"是不是真的一层一层"）

判读方法：
  - 正视图轮廓要和模型对得上，不能是一根根竖直柱子；
  - 抬手臂/裙摆下方要能透出白底（说明悬垂空腔保住了）；
  - 各层切片轮廓应当逐层变化，而不是恒定形状（恒定 = 被按列填实了）。
"""
import argparse
import json
import os

from PIL import Image, ImageDraw

PAL = {'Red': (200, 30, 25), 'Dark Red': (110, 20, 22), 'Bright Light Orange': (248, 180, 60),
       'Yellow': (245, 205, 60), 'Orange': (250, 125, 20), 'Dark Orange': (170, 85, 10),
       'Green': (35, 120, 60), 'Bright Green': (90, 170, 80), 'Black': (30, 33, 36),
       'White': (245, 245, 245), 'Tan': (225, 200, 150), 'Light Nougat': (245, 215, 180),
       'Reddish Brown': (95, 45, 25), 'Dark Tan': (150, 140, 118), 'Dark Brown': (60, 40, 28),
       'Medium Azure': (70, 170, 200), 'Light Bluish Gray': (160, 165, 169),
       # 中间调（与 vox.py 的 PAL_DEFAULT 一一对应，缺了就会渲染成洋红）
       'Medium Nougat': (170, 125, 85), 'Nougat': (208, 145, 104),
       'Medium Tan': (204, 163, 115), 'Bright Light Yellow': (255, 240, 58),
       'Dark Green': (24, 70, 50), 'Sand Green': (160, 188, 172),
       'Dark Bluish Gray': (108, 110, 104), 'Pearl Gold': (170, 127, 46)}


def col(c):
    return PAL.get(c, (255, 0, 255))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vol', required=True)
    ap.add_argument('--outdir', required=True)
    ap.add_argument('--px', type=int, default=9, help='每格像素')
    ap.add_argument('--floors', type=int, default=8, help='分层图抽几层')
    args = ap.parse_args()

    import os
    os.makedirs(args.outdir, exist_ok=True)   # 少这一步会在新目录上直接 FileNotFoundError

    V = json.load(open(args.vol))
    nx, ny, nz = V['nx'], V['ny'], V['nz']
    cells = V['cells']
    P = args.px

    front, back, side = {}, {}, {}
    for a, b, d, c in cells:
        if (a, b) not in front or d > front[(a, b)][0]:
            front[(a, b)] = (d, c)
        if (a, b) not in back or d < back[(a, b)][0]:
            back[(a, b)] = (d, c)
        if (d, b) not in side or a > side[(d, b)][0]:
            side[(d, b)] = (a, c)

    for name, mp, w, hgt, flip in (('front', front, nx, ny, False),
                                   ('back', back, nx, ny, True),
                                   ('side', side, nz, ny, False)):
        im = Image.new('RGB', (w * P, hgt * P), (255, 255, 255))
        dr = ImageDraw.Draw(im)
        for (a, b), (_, c) in mp.items():
            x = (w - 1 - a if flip else a) * P
            y = (hgt - 1 - b) * P
            dr.rectangle([x, y, x + P - 1, y + P - 1], fill=col(c))
        im.save(os.path.join(args.outdir, f'view_{name}.png'))
    print('views ok')

    # 分层图：从下往上等距抽层，每层画成俯视切片
    floors = [int(round(i * (ny - 1) / max(1, args.floors - 1))) for i in range(args.floors)]
    per = {}
    for a, b, d, c in cells:
        per.setdefault(b, {})[(a, d)] = c
    lab = 14
    tiles = []
    for b in floors:
        im = Image.new('RGB', (nx * P, nz * P + lab), (255, 255, 255))
        dr = ImageDraw.Draw(im)
        for (a, d), c in per.get(b, {}).items():
            dr.rectangle([a * P, d * P + lab, a * P + P - 1, d * P + lab + P - 1], fill=col(c))
        dr.text((2, 1), f'layer {b}', fill=(0, 0, 0))
        tiles.append(im)
    sheet = Image.new('RGB', (nx * P * len(tiles), nz * P + lab), (255, 255, 255))
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * nx * P, 0))
    sheet.save(os.path.join(args.outdir, 'layers.png'))
    print('layers ok', floors)


if __name__ == '__main__':
    main()
