// 3D 切片模型 → 积木（build.mjs 模板）
//
// 用法：把本文件拷到 volume.json 所在目录，改一下 title 即可。
//   volume.json 由 lego-3d-voxel/scripts/vox.py 生成，读的是同目录的它。
// 这里只做一件事：一格一颗 1×1×3 的砖（3 块板高 = 1 个砖层），按层从下往上堆。
//
// 不要在这里加任何"智能修补"（补立柱 / 改颜色 / 剪格子）：
//   - 补立柱会把悬垂空腔（手下面、胳膊与身体之间）焊成实心柱；
//   - 改颜色会把贴图分区糊掉；
//   - 剪格子会切出没有支撑的孤立件。
// 几何与颜色的所有决策都在 vox.py 里完成。
import fs from 'node:fs';

const V = JSON.parse(fs.readFileSync(new URL('./volume.json', import.meta.url), 'utf8'));

export default function build({ Model }) {
  const m = new Model({
    title: 'SET TITLE',
    subtitle: `sliced from a 3D model — ${V.ny} courses, one brick per cell`,
    budget: [100, 200000], // 体素塑像件数没上限：外形优先
  });

  // [列, 层, 深, 颜色] → x = 列, y = 层 × 3 块板, z = 深
  for (const [a, b, d, c] of V.cells) m.box(a, b * 3, d, 1, 1, 3, c);

  return m;
}
