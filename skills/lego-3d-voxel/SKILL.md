---
name: lego-3d-voxel
description: Turn a real 3D model (GLB/GLTF) into a buildable LEGO set by slicing it into horizontal brick courses, one brick per voxel cell, with colours sampled from the model's own texture. Use when the user supplies a 3D model file (or an exported scan / sculpt) and wants it built in LEGO "layer by layer" — not when working from a flat photo.
---

# 3D 模型 → 逐层切片 → 积木

把用户给的 3D 模型（GLB）**真的按层切开**，每层按切片铺砖、逐层叠起来，颜色取自模型自带贴图。
这是"形体优先"的做法：件数不设上限，外形、空腔、朝向必须忠实于模型。

几何与颜色**只能有一个来源：模型文件本身**。不要用照片取色，也不要"看着渲染图想象形状"——
那两条路都试过，结果是形状失真 + 颜色全错，用户明确否决。

## 自包含声明

本 skill **不依赖任何外部文件**：色板（15 色）、颜色归并表（材质分组）、解析器、洪泛、去噪全部内置在
`scripts/vox.py` 里，只要求 `numpy` + `pillow`。不传 `--merge` 就用内置表，不传 `--palette` 就用内置色板。

- 产出只有一个文件：`volume.json`（`{nx,ny,nz,up,front,cam,cells:[[列,层,深,色名],...]}`）。
- `scripts/build.mjs` 是装配模板（把 cells 逐格变成 `m.box`）；`scripts/preview.py` 是自带的校验渲染器，
  都不依赖外部工程。若宿主工程有 `lego.mjs` 之类的打包/检查工具链，那是**可选的下一步**，不是本 skill 的依赖。
- 换句话说：把这个目录整个拷到任何机器上，只要有 python + numpy + pillow，就能从 GLB 跑到 volume.json。

## 什么时候用

- 用户给了 `.glb` / `.gltf`（扫描件、雕刻、游戏资产），要求拼成积木、要求"一层一层切"。
- 照片参考 → 走 `lego-forge` 的手工搭建流程，不要用本 skill。

## 铁律（违反必然返工）

1. **不要从图片取色。** 颜色只来自模型贴图。照片取色在光照/贴图缺失下会把整件东西做成脏色。
2. **不要按列 min..max 填实。** 那是"垂直柱子"的根因，会把手下面、胳膊与身体之间的空腔填死。
   必须用三维洪泛判内外（见 `references/pipeline.md` 第 6 节）。
3. **不要让 build.mjs 做任何"智能修补"**（不补柱、不改色、不剪格子）。修补会把空心结构焊死、把细节糊平。
   几何/颜色的所有决策都在 `vox.py` 里做完，`build.mjs` 只负责 `m.box(x, y*3, z, 1, 1, 3, color)`。
4. **先用球验证链路。** 做任何人物/复杂形体之前，先跑一个 24 层、每层一色的球。
   球是凸的，看不出空腔问题，但能证明分层/坐标/叠砖/打包链路是对的。球错了就停下修管线。
5. **分辨率按原作尺寸给。** 0.9 m 高的坐像下 96 层才够；**五官要分开就得上 128 层**。
6. **层高与格宽的比例必须是真实砖的比例**：层高 = 总高 / 层数，格宽 = 层高 / 1.2（8 mm : 9.6 mm）。
   比例错了，整件东西会显得又矮又胖。
7. **改一个假设、验一次。** 几何错和颜色错要分开修，否则分不清是哪一处起的作用
   （眼睛那条就是"几何被埋 + 颜色被吃"两个根因同时存在）。修完必须凑近看像素，
   不能因为"两个参数都开了"就宣布修好——这一点我犯过。

## 流程

```bash
# 0. 依赖（只有这两个）
python -c "import numpy, PIL" || pip install numpy pillow

# 1. 切片（唯一的数据生产步骤；归并表已内置，不需要外部 json）
python <skill>/scripts/vox.py \
  --glb <model.glb> --out <work>/volume.json --courses 128

# 2. 先看三视图 + 分层图，确认朝向、比例、空腔、五官都对（这一步省不得）
python <skill>/scripts/preview.py --vol <work>/volume.json --outdir <work>

# 3. 拷 scripts/build.mjs 到工程里改路径，逐格发砖
#    再走宿主工程自己的 build / shots 检查（可选）
```

参数怎么定：

| 参数 | 默认 | 取值建议 | 说明 |
|---|---|---|---|
| `--courses` | 96 | 人像 128；小物件 64~96 | 96 层五官会糊成一块；128 层是目前的实用上限（再高只剩更贵） |
| `--merge` | `default` | `default` / `none` / 自定义 json | 内置材质归并表；`none` 看原始贴图 |
| `--absorb` | 3 | 4~6 | ≤N 格的孤立色块并进邻域主色；太大会吃掉小细节 |
| `--denoise` | 2 | 1~2 | 最后一道 3×3×3 平滑；会削 1 格宽的细线 |
| `--darkdetail` | 0.32 | 0.28~0.40 | 细黑线（闭眼/嘴缝/刻线）保底比例，见 `pipeline.md` 5b |
| `--ratio` | 1.2 | 不要改 | 格宽 = 层高 / 1.2 |

想覆盖归并表就写一份 json 传路径（键 = 目标色，数组 = 并入它的颜色）：

```json
{"Red": ["Dark Red", "Orange", "Dark Orange"],
 "Green": ["Bright Green"],
 "Bright Light Orange": ["Yellow", "Tan", "Light Nougat", "Dark Tan"]}
```

合并要按**材料**分组，不要按色相分组：肤色和红袍都很"暖"，但绝不是同一种材料。
归并的语义是"**每个区域**统一成该区域的主色"——成片的暗红阴影会保留（那是真实的明暗分区），
只有碎条纹被并掉。所以红袍变干净，但层次不丢。

第 2 步的验收标准（看 `view_front.png` / `view_side.png` / `layers.png`）：

- 正视图轮廓与模型一致，**不是**一根根竖直的柱子；
- 抬手臂/裙摆下方是空的，能看到背景色；
- 颜色分区合理（脸是肤色、眉眼是深色、衣袍成片），没有大面积斑驳噪点；
- **脸要凑近单独看**：眉毛 / 眼睛 / 胡子是分开的三层，眼睛不能消失。

## 排错：先查这张表

| 症状 | 病因 | 修法 |
|---|---|---|
| 几乎全白 / 只有零星颜色 | 取色退路把 RGB 和平方放大，全被量化成 White | `tbl[:, :3].sum(0) / tot`，不要再乘样本数 |
| 每格颜色像是"对面那一侧"的 | 方向数组顺序和 `binid` 编码反了 | `NB = [(1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)]`，`binid = ax*2 + (sgn<0)` |
| 成品是一根根垂直柱子、空腔被填死 | 按列 min..max 填实 | 三维洪泛：外壳 dilate(2) 封缝 → 从边界洪泛外部空气 → `S = Ssurf \| erode(erode(~out))` |
| 只剩一层皮，内部全空 | 单层壳的三角面之间有缝，外部空气漏满内部 | 洪泛前先把壳 dilate 两圈封缝，洪泛后 erode 两圈收回 |
| 颜色整体偏 / 整片错位 | glTF 的 UV 原点在左上角，V 被额外翻转 | `ty = v * (th-1)`，**不要** `1-v` |
| 眉眼糊成一片 | 票箱取众数 / 平均 | 改成**面积加权**（`pipeline.md` 5a），再不行是层数不够 |
| **眼睛/嘴缝/刻线整个消失** | ① 凹槽被 dilate 封死→判成内部→被回填；② 细线被"面积最大"取色吃掉 | 两条都要加：反填 `carve` + `--darkdetail`（见 `pipeline.md` 5b/6b）。**注意：财神这份扫描件两处都开了仍没出来，属未解决，别照抄结论** |
| 脸上/袍上一块块杂色、"脏" | 扫描贴图是抹开的，同一材料上混着 3~4 个近似色 | `--merge default` 按区域归并 + `--absorb 6` |
| 大量零件报 not connected | 同色才并砖；异色同层不连接、下方又没砖 | 见 `pitfalls.md` 第 7 条（已知未解决） |

## 已知未解决（交付前必须自己复核，不要照抄旧结论）

1. **眼睛/细五官**：carve 与 `--darkdetail` 已实装、参数已调，但在财神 GLB 上 128 层渲染仍看不到成对的眼睛；
   192 层、关去噪均无效。查法见 `pitfalls.md` 6b 与 `scripts/probe.py --profile`。
2. **连接**：`lego build` 报 503 处 `not connected`（128 层），底部外沿换色断连。
3. **成片脏区**：帽子/袍面仍会残留少量异色成片块（如帽子上出现绿色），归并表只能治"同材料近似色"，
   治不了贴图本身串色。

这三条都属于"方法骨架对、最后一公里没跑完"。写进文档是为了下次从这里接着查，不是已完成的证据。

细节算法、参数取值、验证脚本全部在 `references/`：
- `references/pipeline.md` — 十一个步骤的完整算法与数值约定（必读）
- `references/pitfalls.md` — 每个坑的完整复盘：现象、根因、为什么当时的直觉是错的
- `references/validation.md` — 四关验收清单

脚本（全部自包含，只用 numpy + pillow）：
- `scripts/vox.py` — 切片主程序（`--ball` 出验证球）
- `scripts/probe.py` — 体检：`--stats` 颜色分布 / `--layers A:B --cols C:D` 正面 ASCII 色图 / `--profile 列,层` 深度剖面
- `scripts/preview.py` — 三视图 + 分层图
- `scripts/build.mjs` — 装配模板（拷到 volume.json 同目录，只改 title）

排查顺序建议：`probe.py --stats` → `--layers/--cols` 看特征在不在 → `--profile` 看是不是被挡住 →
改一个参数重跑 → 再看一次。**每一步都用数据确认，不靠"参数开了就该好"。**
