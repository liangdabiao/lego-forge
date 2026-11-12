"""GLB 3D 模型 → 积木体素切片（每格一颗 1×1×3 砖，逐层叠起来）。

几何和颜色唯一来源 = 模型文件本身（顶点 + 法线 + UV + baseColor 贴图）。
输出 volume.json：{"nx","ny","nz","cells":[[列, 层, 深, 色名], ...]}

六步：
  1. 解析 GLB（顶点/法线/UV/索引/baseColor 贴图）
  2. 应用节点旋转 → 找朝上轴；量包围盒；切砖层（层高 = 高/层数，格宽 = 层高/1.2）
  3. 每颗三角面采 7 点，按法线朝向分进 6 个方向仓 → 每格知道「朝外那个面」是什么颜色
  4. 三维洪泛判内外（保留悬垂空腔，不要按列填满）
  5. 表面格取朝外面颜色，内部格 BFS 最近色传播
  6. 量化乐高色板 + 3×3×3 去噪 → volume.json

用法：
  python vox.py --glb model.glb --out volume.json --courses 96
  python vox.py --glb model.glb --out volume.json --courses 128 --merge none   # 关掉归并，看原始贴图
  python vox.py --glb model.glb --out v.json --courses 96 --palette my_palette.json
  python vox.py --glb dummy --out v.json --ball          # 24 层每层一色的验证球（不需要真实模型）

只依赖 numpy + pillow，不读本文件以外的任何东西：色板、归并表、算法全部内置。
"""
import argparse
import io
import json
import struct
from collections import deque

import numpy as np
from PIL import Image

CT = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2), 5123: ('H', 2), 5125: ('I', 4), 5126: ('f', 4)}
NC = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}

# 默认乐高色板。--palette 可覆盖（JSON: {"Red": [200,30,25], ...}）。
# 经验：Orange 在多数渲染器里偏粉，想要饱和大红/橙要优先 Red / Bright Light Orange / Dark Orange。
PAL_DEFAULT = [
    ('Red', (200, 30, 25)), ('Dark Red', (110, 20, 22)),
    ('Bright Light Orange', (248, 180, 60)), ('Yellow', (245, 205, 60)),
    ('Orange', (250, 125, 20)), ('Dark Orange', (170, 85, 10)),
    ('Green', (35, 120, 60)), ('Bright Green', (90, 170, 80)),
    ('Black', (30, 33, 36)), ('White', (245, 245, 245)),
    ('Tan', (225, 200, 150)), ('Light Nougat', (245, 215, 180)),
    ('Reddish Brown', (95, 45, 25)), ('Dark Tan', (150, 140, 118)),
    ('Dark Brown', (60, 40, 28)),
]

# 6 个方向仓的顺序必须和 binid 一致：0:+X 1:-X 2:+Y 3:-Y 4:+Z 5:-Z
# 曾经写成 -X,+X,-Y,+Y,-Z,+Z，结果每个体素都在读对面那一侧的颜色（整件东西颜色错乱）。
NB = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]

# 默认颜色归并表（按「材料」分组，键 = 目标色，值 = 并入它的色）。
# 内置在这里是为了本 skill 自包含：不传 --merge 也能跑，不需要外部 json。
# 分组原则：同一件衣服/同一种材质上被贴图抹开的近似色并成一个；
# 不同材料即使色相接近也绝不合（肤色 Bright Light Orange 与红袍 Red 都是暖色，但不是同一种材料）。
# 后果是可预期的一句话：Dark Red / Orange 的**成片区域**保持原色（那是真实的明暗分区），
# 只有碎条纹被并进区域主色——所以红袍变干净，但阴影层次没有丢。
MERGE_DEFAULT = {
    'Red': ['Dark Red', 'Orange', 'Dark Orange'],
    'Green': ['Bright Green'],
    'Bright Light Orange': ['Yellow', 'Tan', 'Light Nougat', 'Dark Tan'],
    # Reddish Brown / Dark Brown 单独留着：胡须、木座的深色不是红袍的阴影。
}


def load_glb(path):
    data = open(path, 'rb').read()
    off, chunks = 12, []
    while off < len(data):
        clen, ctype = struct.unpack_from('<II', data, off)
        off += 8
        chunks.append((ctype, data[off:off + clen]))
        off += clen
    js = json.loads(chunks[0][1].decode('utf-8'))
    binc = next(c[1] for c in chunks if c[0] == 0x004E4942)
    return js, binc


def accessor(js, binc, idx):
    a = js['accessors'][idx]
    bv = js['bufferViews'][a['bufferView']]
    dt, sz = CT[a['componentType']]
    n = NC[a['type']]
    start = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    stride = bv.get('byteStride') or sz * n
    if stride == sz * n:
        return np.frombuffer(binc, dtype=np.dtype(dt), count=a['count'] * n, offset=start).reshape(a['count'], n)
    out = np.zeros((a['count'], n), dtype=dt)
    raw = np.frombuffer(binc, dtype=np.uint8, count=a['count'] * stride, offset=start).reshape(a['count'], stride)
    for i in range(n):
        out[:, i] = raw[:, i * sz:(i + 1) * sz].copy().view(np.dtype(dt)).ravel()
    return out


def dilate(a):
    g = a.copy()
    g[1:, :, :] |= a[:-1, :, :]
    g[:-1, :, :] |= a[1:, :, :]
    g[:, 1:, :] |= a[:, :-1, :]
    g[:, :-1, :] |= a[:, 1:, :]
    g[:, :, 1:] |= a[:, :, :-1]
    g[:, :, :-1] |= a[:, :, 1:]
    return g


def erode(a):
    g = a.copy()
    g[1:, :, :] &= a[:-1, :, :]
    g[:-1, :, :] &= a[1:, :, :]
    g[:, 1:, :] &= a[:, :-1, :]
    g[:, :-1, :] &= a[:, 1:, :]
    g[:, :, 1:] &= a[:, :, :-1]
    g[:, :, :-1] &= a[:, :, 1:]
    g[0, :, :] = g[-1, :, :] = False
    g[:, 0, :] = g[:, -1, :] = False
    g[:, :, 0] = g[:, :, -1] = False
    return g


def pal_index(rgb, palc):
    """RGB(0..255) → 最近色板色下标。分块 + 矩阵乘，省内存（只剩 1 GB 空闲时也能跑）。"""
    w3 = np.array([0.30, 0.59, 0.11], np.float32)   # 加权距离：人眼对绿最敏感
    out = np.empty(len(rgb), np.int16)
    STEP = 100000
    for s0 in range(0, len(rgb), STEP):
        ch = rgb[s0:s0 + STEP].astype(np.float32)
        d = (ch[:, None, :] - palc[None]) ** 2      # (n,15,3)
        out[s0:s0 + len(ch)] = (d @ w3).argmin(1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--glb', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--courses', type=int, default=96, help='砖层数（0.9 m 高的坐像用 96）')
    ap.add_argument('--ratio', type=float, default=1.2, help='格宽 = 层高 / ratio（真实砖 8mm:9.6mm = 1.2）')
    ap.add_argument('--palette', help='JSON 文件 {"色名": [r,g,b]}，覆盖默认色板')
    ap.add_argument('--cam', type=int, default=1, help='正视图从哪一侧看（只影响预览标记）')
    ap.add_argument('--denoise', type=int, default=2, help='3×3×3 去噪轮数；0 = 关闭（查小特征丢失时用）')
    ap.add_argument('--merge', default='default',
                    help="颜色归并表：'default'=内置材质表（不依赖外部文件）；'none'=关闭；或 JSON 路径 "
                         '{"Red":["Dark Red","Orange"]} = 后者并入 Red')
    ap.add_argument('--absorb', type=int, default=3, help='<=N 格的孤立色块并进邻域主色；0 = 关闭')
    ap.add_argument('--protect', default='Black,White', help='不参与吸收的颜色（细节色，用逗号分隔）')
    ap.add_argument('--darkdetail', type=float, default=0.32,
                    help='暗细节保底：某格里 Black 覆盖面积占比 >= 此值就取黑（眼睛/嘴缝/刻线）；0=关闭')
    ap.add_argument('--ball', action='store_true', help='忽略模型，输出 24 层每层一色的验证球')
    args = ap.parse_args()

    PAL = PAL_DEFAULT
    if args.palette:
        raw = json.load(open(args.palette, encoding='utf-8'))
        PAL = [(k, tuple(v)) for k, v in raw.items()]
    PALC = np.array([c for _, c in PAL], np.float32)

    if args.ball:
        make_ball(args, PAL)
        return

    js, binc = load_glb(args.glb)
    prim = js['meshes'][0]['primitives'][0]
    pos = accessor(js, binc, prim['attributes']['POSITION']).astype(np.float32)
    nrm = accessor(js, binc, prim['attributes']['NORMAL']).astype(np.float32)
    uvv = accessor(js, binc, prim['attributes']['TEXCOORD_0']).astype(np.float32)
    idx = accessor(js, binc, prim['indices']).ravel().astype(np.int64)

    # 节点旋转（四元数）→ 世界坐标
    x, y, z, w = js['nodes'][0].get('rotation', [0, 0, 0, 1])
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]], np.float32)
    pos = pos @ R.T
    nrm = nrm @ R.T
    lo, hi = pos.min(0), pos.max(0)
    ext = hi - lo
    up = int(np.argmax(ext))
    rest = [i for i in range(3) if i != up]
    front = int(min(rest, key=lambda i: ext[i]))
    print('bbox', np.round(lo, 3), np.round(hi, 3), 'ext', np.round(ext, 3))
    print('up', 'XYZ'[up], 'front', 'XYZ'[front], 'courses', args.courses)

    # baseColor 贴图
    tex_idx = js['materials'][0]['pbrMetallicRoughness']['baseColorTexture']['index']
    t = js['textures'][tex_idx]
    img = js['images'][t['source']]
    bv = js['bufferViews'][img['bufferView']]
    bo = bv.get('byteOffset', 0)
    tex = np.asarray(Image.open(io.BytesIO(binc[bo:bo + bv['byteLength']])).convert('RGB')).astype(np.uint8)
    th, tw, _ = tex.shape

    def sample_uv(u, v):
        tx = np.clip((u % 1.0) * (tw - 1), 0, tw - 1).astype(np.int32)
        ty = np.clip((v % 1.0) * (th - 1), 0, th - 1).astype(np.int32)   # glTF: V 原点在左上角，不翻转
        return tex[ty, tx].astype(np.float32)

    h = ext[up] / args.courses
    stud = h / args.ratio
    ns = [max(1, int(round(ext[i] / stud))) for i in range(3)]
    ns[up] = args.courses
    print('grid', ns, 'stud', round(float(stud), 4), 'course', round(float(h), 4))

    W3 = np.array([0.30, 0.59, 0.11], np.float32)

    def pal_q(rgb):
        return pal_index(rgb, PALC)

    # --- 1. 每颗三角面采 7 点（重心/3 顶点/3 边中点）→ 量化 → 投给 (格, 朝外法线方向) 票箱
    tris = idx.reshape(-1, 3)
    bc = np.array([[1 / 3, 1 / 3, 1 / 3], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                   [.5, .5, 0], [0, .5, .5], [.5, 0, .5]], np.float32)
    cf_parts, pi_parts, p_parts = [], [], []
    BATCH = 50000
    for s0 in range(0, len(tris), BATCH):
        tr = tris[s0:s0 + BATCH]
        P = np.einsum('kb,nbc->nkc', bc, pos[tr]).reshape(-1, 3)
        U = np.einsum('kb,nbc->nkc', bc, uvv[tr]).reshape(-1, 2)
        C = sample_uv(U[:, 0], U[:, 1])
        N = np.repeat(nrm[tr].mean(1), 7, axis=0)
        ax = np.abs(N).argmax(1)
        sgn = np.sign(N[np.arange(len(N)), ax])
        binid = ax * 2 + (sgn < 0)                                       # 0 +X,1 -X,2 +Y,3 -Y,4 +Z,5 -Z
        gi = np.empty((len(P), 3), np.int64)
        cen = np.empty((len(P), 3), np.float32)
        for k in range(3):
            u = stud if k != up else h
            gi[:, k] = np.clip(((P[:, k] - lo[k]) / u).astype(int), 0, ns[k] - 1)
            cen[:, k] = (P[:, k] - lo[k]) / u - 0.5      # 归一化到「格」，三轴可比，格中心在整数值上
        key = (gi[:, 0] * ns[1] + gi[:, 1]) * ns[2] + gi[:, 2]
        cf_parts.append((key * 6 + binid).astype(np.int64))
        pi_parts.append(pal_q(C).astype(np.int64))
        p_parts.append(cen)

    CF = np.concatenate(cf_parts)
    PI = np.concatenate(pi_parts)
    CEN = np.concatenate(p_parts)
    print('samples', len(CF))

    def decode_cf(cf):
        d_ = cf % ns[2]
        b_ = (cf // ns[2]) % ns[1]
        a_ = cf // (ns[1] * ns[2])
        return a_, b_, d_

    # --- 1b. 面积加权投票：每颗三角面把自己的**面积**投给「重心所在的 (格, 方向仓, 颜色)」。
    #
    # 为什么不能用「离格中心最近的那个采样点」：那个规则对平坦大面很锐利，但细特征（财神闭着的
    # 笑眼就是一条 1.8 层高的黑弧线）只覆盖格子的一部分，格中心经常落在肤色上 → 整条眼睛消失，
    # 脸上只剩肤色和眉毛。实测 96 层时眼睛区域 0 个黑格，但该区域黑色面积其实占 15.7%。
    #
    # 面积加权 = 每格显示「覆盖面积最大的那种材料」，这是体素化唯一正确的答案；
    # 而且按面积而不是按样本数，不会被倾斜面上密集的三角带带偏（这正是当初改用最近点的原因）。
    acc = {}
    for s0 in range(0, len(tris), BATCH):
        tr = tris[s0:s0 + BATCH]
        P3 = pos[tr]
        ar = 0.5 * np.linalg.norm(np.cross(P3[:, 1] - P3[:, 0], P3[:, 2] - P3[:, 0]), axis=1)
        CEN3 = P3.mean(1)
        U3 = uvv[tr].mean(1)
        C3 = sample_uv(U3[:, 0], U3[:, 1])
        N3 = nrm[tr].mean(1)
        ax = np.abs(N3).argmax(1)
        sgn = np.sign(N3[np.arange(len(N3)), ax])
        bin3 = ax * 2 + (sgn < 0)
        gi = np.empty((len(P3), 3), np.int64)
        for k in range(3):
            u = stud if k != up else h
            gi[:, k] = np.clip(((CEN3[:, k] - lo[k]) / u).astype(int), 0, ns[k] - 1)
        cell3 = (gi[:, 0] * ns[1] + gi[:, 1]) * ns[2] + gi[:, 2]
        key = (cell3 * 6 + bin3) * 64 + pal_q(C3)
        uq, inv = np.unique(key, return_inverse=True)
        aq = np.bincount(inv, weights=ar.astype(np.float32))
        for k, a in zip(uq.tolist(), aq.tolist()):
            acc[k] = acc.get(k, 0.0) + a

    # 票箱 → 覆盖面积最大的颜色
    keys = np.fromiter(acc.keys(), np.int64, len(acc))
    areas = np.fromiter(acc.values(), np.float32, len(acc))
    cf_k = keys // 64
    pi_k = (keys % 64).astype(np.int16)
    o1 = np.lexsort((areas, cf_k))
    cf_s, pi_s = cf_k[o1], pi_k[o1]
    first = np.empty(len(cf_s), bool)
    first[0] = True
    first[1:] = cf_s[1:] != cf_s[:-1]

    ncf = int(cf_k.max()) + 1
    face_pal = np.zeros(ncf, np.int16)
    face_pal[cf_s[first]] = pi_s[first]                                 # 每格每面：面积最大的颜色
    face_votes = np.bincount(cf_k).astype(np.int32)                      # 该面有多少颜色种类（排序用）
    face_area = np.bincount(cf_k, weights=areas, minlength=ncf).astype(np.float32)

    # --- 1c. 细节保底：细而深的线（闭着的眼睛、嘴缝、刻线）在格子里的面积占比往往只有 30~50%，
    # 按"面积最大"会被肤色吃掉，整张脸就只剩眉毛和胡子。
    # 规则：某格里如果存在一种「比主色暗得多」的颜色且覆盖 >= darkdetail，就用那种暗色。
    # 只对浅色格生效（主色本身已经很暗的阴影区不参与），所以不会把阴影糊成黑块。
    dd = args.darkdetail
    if dd > 0:
        LUM = np.array([0.30 * c[0] + 0.59 * c[1] + 0.11 * c[2] for _, c in PAL], np.float32)
        tot_a = np.bincount(cf_k, weights=areas, minlength=ncf).astype(np.float32)
        dl = LUM[pi_k]
        dom_lum = LUM[face_pal]
        BLACKI = [i for i, (n, _) in enumerate(PAL) if n == 'Black'][0]
        # 只认"墨线"：格子里黑（Black）的覆盖面积达到阈值就取黑。
        # 不能改成"取最暗的那个色"——橙底上的暗红阴影会被当成细节，红袍上就会冒出黑斑。
        dark = (pi_k == BLACKI) & (dom_lum[cf_k] > 95)
        dark_a = np.bincount(cf_k[dark], weights=areas[dark], minlength=ncf).astype(np.float32)
        # 每组里最暗的那个颜色：按亮度降序排，组内最后一个即最暗
        o4 = np.lexsort((-dl, cf_k))
        rev = o4[::-1]
        uniq_r, idx_r = np.unique(cf_k[rev], return_index=True)
        last_of = np.zeros(ncf, np.int64) - 1
        last_of[uniq_r] = rev[idx_r]
        share = dark_a / np.maximum(tot_a, 1e-9)
        hit = (share >= dd) & (last_of >= 0)
        face_pal[hit] = pi_k[last_of[hit]]
        print('detail rescue:', int(hit.sum()), 'faces')
    del keys, areas, cf_k, pi_k, o1, cf_s, pi_s, first, acc
    print('surface cells', len(np.unique(CF // 6)))

    # --- 2. 三维洪泛判内外（关键：不做按列填实）
    Ssurf = np.zeros((ns[0], ns[1], ns[2]), bool)
    _sa, _sb, _sd = decode_cf(np.unique(CF // 6))
    Ssurf[_sa, _sb, _sd] = True

    # 单层壳的三角面之间有缝，外部空气会顺缝漏满内部（结果只剩一层皮）→ 先加厚两圈封缝
    Sb = dilate(dilate(Ssurf))
    out = np.zeros_like(Ssurf)
    out[0, :, :] = out[-1, :, :] = True
    out[:, 0, :] = out[:, -1, :] = True
    out[:, :, 0] = out[:, :, -1] = True
    for _ in range(400):
        prev = out.sum()
        grow = np.zeros_like(out)
        grow[1:, :, :] |= out[:-1, :, :]
        grow[:-1, :, :] |= out[1:, :, :]
        grow[:, 1:, :] |= out[:, :-1, :]
        grow[:, :-1, :] |= out[:, 1:, :]
        grow[:, :, 1:] |= out[:, :, :-1]
        grow[:, :, :-1] |= out[:, :, 1:]
        out = (out | grow) & ~Sb
        if out.sum() == prev:
            break

    # 洪泛用的加厚壳把「封锁区」多撑了 2 格 → 腐蚀 2 格收回，否则整体胖两圈、表面色全丢
    inside = erode(erode(~out))
    S = Ssurf | inside
    print('solid cells', int(S.sum()), '(surface', int(Ssurf.sum()), '/ open air', int(out.sum()), ')')

    # --- 2b. 反填：被三角面采样到的面，它朝外那一格必须是空的。
    #
    # 为什么需要这一步：眼睛是刻在脸上的一道浅槽（槽口 1~2 格宽）。洪泛前为了让外壳不漏气，
    # 要把壳 dilate 两圈——这一下同时把槽口也封死了，空气进不去，槽就被判成"内部"，
    # 于是槽里被填上实体，整条眼睛被埋掉、再用邻居的肤色盖住。
    # 实测：眼区贴图黑色面积占 15.7%，成品里却有 0 个黑格，涨到 192 层也照样没有。
    #
    # 这条规则是几何上必然成立的：采样到的面就是可见面，可见面前面不可能有实体。
    # 只反填「非表面格」，两片真实表面之间只剩 1 格的缝保持不动（那本来也表达不出来）。
    cf_f = np.where(face_area > 0)[0]
    cl_f, fa_f = cf_f // 6, cf_f % 6
    fa_, fb_, fd_ = decode_cf(cl_f)
    dirs = np.array(NB)
    ta, tb, td = fa_ + dirs[fa_f, 0], fb_ + dirs[fa_f, 1], fd_ + dirs[fa_f, 2]
    okf = (ta >= 0) & (ta < ns[0]) & (tb >= 0) & (tb < ns[1]) & (td >= 0) & (td < ns[2])
    tgt = np.unique(((ta[okf] * ns[1]) + tb[okf]) * ns[2] + td[okf])
    Sf, Ss = S.reshape(-1), Ssurf.reshape(-1)
    for it in range(8):
        block = tgt[Sf[tgt] & ~Ss[tgt]]
        if len(block) == 0:
            break
        Sf[np.unique(block)] = False
        print('carve', it, len(np.unique(block)))
    print('solid after carve', int(S.sum()))

    # --- 3. 表面格取「朝外那一面」的颜色（朝外 = 邻格是空气，不是实体）
    cf_all = np.where(face_votes > 0)[0]
    cell_all = cf_all // 6
    face_all = cf_all % 6
    ca, cb, cd = decode_cf(cell_all)
    DIRS = np.array(NB)
    na, nb_, nd = ca + DIRS[face_all, 0], cb + DIRS[face_all, 1], cd + DIRS[face_all, 2]
    inb = (na >= 0) & (na < ns[0]) & (nb_ >= 0) & (nb_ < ns[1]) & (nd >= 0) & (nd < ns[2])
    nbr = np.zeros(len(cf_all), bool)
    nbr[inb] = S[np.clip(na, 0, ns[0] - 1)[inb], np.clip(nb_, 0, ns[1] - 1)[inb], np.clip(nd, 0, ns[2] - 1)[inb]]
    outside = ~inb | ~nbr
    print('faces with samples', len(cf_all), 'of which outward', int(outside.sum()))

    cand = cf_all[outside]
    if len(cand):
        c_cell = cand // 6
        o2 = np.lexsort((face_area[cand], c_cell))       # 一个格有多个朝外面时，取面积最大的那面
        cand, c_cell = cand[o2], c_cell[o2]
        keep = np.empty(len(cand), bool)
        keep[-1] = True
        keep[:-1] = c_cell[1:] != c_cell[:-1]
        winner = cand[keep]                     # 每格票数最多的那个朝外面
    else:
        winner = np.array([], np.int64)

    w_cell, w_pal = winner // 6, face_pal[winner]
    colmap = {}
    wc, wb, wd = decode_cf(w_cell)
    for i in range(len(w_cell)):
        colmap[(int(wc[i]), int(wb[i]), int(wd[i]))] = PAL[int(w_pal[i])][0]
    print('coloured surface cells', len(colmap))

    # --- 3b. 按区域归并颜色（治贴图噪点）
    # 扫描件的贴图是被抹开的：一件红袍上同时有 Red / Orange / Dark Red 的碎条纹，每格各自取色就成了"脏"。
    # 做法不是"把所有 Orange 全局改成 Red"——那样黑胡子上的一小块褐色阴影会变成红点。
    # 正确做法：先在「同族」格子上做连通分量，再让**每个区域**统一成该区域的主色，
    # 所以红袍变纯红，而胡子里的杂色会被后面的小区域吸收掉。
    if args.merge and args.merge != 'none':
        if args.merge == 'default':
            mm = MERGE_DEFAULT
        else:
            mm = json.load(open(args.merge, encoding='utf-8'))
        fam = {}
        for t, lst in mm.items():
            fam[t] = t
            for src in lst:
                fam[src] = t

        def family(c):
            return fam.get(c, c)

        seen = set()
        merged = 0
        for k0 in list(colmap):
            if k0 in seen:
                continue
            f = family(colmap[k0])
            comp = [k0]
            seen.add(k0)
            q = [k0]
            while q:
                cur = q.pop()
                for dx, dy, dz in NB:
                    nk = (cur[0] + dx, cur[1] + dy, cur[2] + dz)
                    if nk not in seen and nk in colmap and family(colmap[nk]) == f:
                        seen.add(nk)
                        q.append(nk)
                        comp.append(nk)
            tally = {}
            for p in comp:
                tally[colmap[p]] = tally.get(colmap[p], 0) + 1
            # 区域里有目标色就用目标色，否则保留区域自己的主色
            # （财神的暗红披风就是这样保留下来的——那是真实的明暗分区，不是噪点）
            target = f if f in tally else max(tally.items(), key=lambda tt: tt[1])[0]
            if len(tally) > 1 or target != f:
                for p in comp:
                    colmap[p] = target
                merged += len(comp)
        print('merge:', merged, 'cells unified into',
              len({family(c) for c in colmap.values()}), 'colours')

    # --- 3c. 孤立小色块并进邻域主色（只处理 <=absorb 格的分量；保护色不动）
    def absorb_small(cmap, maxsize, protect):
        seen = set()
        changed = 0
        for k0 in list(cmap):
            if k0 in seen:
                continue
            c = cmap[k0]
            comp = [k0]
            seen.add(k0)
            q = [k0]
            while q:
                cur = q.pop()
                for dx, dy, dz in NB:
                    nk = (cur[0] + dx, cur[1] + dy, cur[2] + dz)
                    if nk not in seen and cmap.get(nk) == c:
                        seen.add(nk)
                        q.append(nk)
                        comp.append(nk)
            if len(comp) > maxsize or c in protect:
                continue
            tally = {}
            for (a_, b_, d_) in comp:
                for dx, dy, dz in NB:
                    c2 = cmap.get((a_ + dx, b_ + dy, d_ + dz))
                    if c2 is not None and c2 != c:
                        tally[c2] = tally.get(c2, 0) + 1
            if not tally:
                continue
            top = max(tally.items(), key=lambda tt: tt[1])[0]
            for p in comp:
                cmap[p] = top
            changed += len(comp)
        return changed

    if args.absorb:
        protect = {s.strip() for s in args.protect.split(',') if s.strip()}
        print('absorb:', absorb_small(colmap, args.absorb, protect), 'cells')

    # --- 4. 内部格 BFS 从外表面向内传播最近颜色
    filled = dict(colmap)
    q = deque(colmap.keys())
    solid = {(int(a), int(b), int(d)) for a, b, d in zip(*np.where(S))}
    while q:
        a_, b_, d_ = q.popleft()
        c = filled[(a_, b_, d_)]
        for dx, dy, dz in NB:
            nk = (a_ + dx, b_ + dy, d_ + dz)
            if nk in solid and nk not in filled:
                filled[nk] = c
                q.append(nk)
    print('filled cells', len(filled), 'of', len(solid))

    # --- 5. 3×3×3 去噪：同色邻格不足 3 的孤立点并进邻域主色
    # 注意：这个滤镜会吃掉 1 格宽的细长特征（笑眯眯的眼睛就是这种）。--denoise 0 可关掉对照。
    for _ in range(args.denoise):
        snap = dict(filled)
        changed = 0
        for k, own in snap.items():
            a_, b_, d_ = k
            tally, same = {}, 0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        c = snap.get((a_ + dx, b_ + dy, d_ + dz))
                        if c is None:
                            continue
                        tally[c] = tally.get(c, 0) + 1
                        if c == own:
                            same += 1
            if same >= 3:
                continue
            top = max(tally.items(), key=lambda tt: tt[1])[0]
            if top != own:
                filled[k] = top
                changed += 1
        print('denoise', changed)
        if not changed:
            break

    cells = [[a_, b_, d_, c] for (a_, b_, d_), c in filled.items()]
    json.dump({'nx': ns[0], 'ny': ns[1], 'nz': ns[2], 'up': int(up), 'front': int(front),
               'cam': args.cam, 'cells': cells}, open(args.out, 'w'))
    print('wrote', args.out, len(cells), 'cells')


def make_ball(args, PAL):
    """24 层、每层一色的验证球：证明分层/坐标/叠砖链路正确。"""
    n = 24
    r = n / 2 - 0.5
    cells = []
    for b in range(n):
        for a in range(n):
            for d in range(n):
                cx, cy, cz = a - r, b - r, d - r
                if cx * cx + cy * cy + cz * cz <= r * r:
                    cells.append([a, b, d, PAL[(b * 3) % len(PAL)][0]])
    json.dump({'nx': n, 'ny': n, 'nz': n, 'up': 1, 'front': 2, 'cam': 1, 'cells': cells},
              open(args.out, 'w'))
    print('wrote ball', args.out, len(cells), 'cells,', n, 'courses')


if __name__ == '__main__':
    main()
