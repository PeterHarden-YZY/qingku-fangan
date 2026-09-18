// 验证部署产物 v11 中三个输入字段的读取位置 (必须全部在主表循环侧)
const fs = require('fs');
const code = fs.readFileSync('D:/agents_project/清库方案自动计算/.workbuddy/deployed_bundle_v11.js', 'utf-8');
function esc(s) {
  let e = '';
  for (const ch of s) e += '\\u' + ch.codePointAt(0).toString(16).toUpperCase().padStart(4, '0');
  return e;
}
function allPos(s) {
  // esbuild 只转义非 ASCII 字符, ASCII (括号等) 保持原样
  let t = '';
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    t += cp > 127 ? '\\u' + cp.toString(16).toUpperCase().padStart(4, '0') : ch;
  }
  const pos = [];
  let i = -1;
  while ((i = code.indexOf(t, i + 1)) !== -1) pos.push(i);
  return pos;
}
const parentPos = allPos('父记录');
const firstParent = parentPos[0];
console.log('父记录 首现位置:', firstParent, '(主表循环起点标志)');
let pass = true;
for (const kw of ['在途库存', '在途到货时间(天)', '工厂未发货数量']) {
  const pos = allPos(kw);
  // 读取语句形如 f["在途库存"] — 位于首个父记录之后的才是主表循环侧
  const readSide = pos.filter(p => p > firstParent);
  console.log(`${kw}: 共${pos.length}处, 主表循环侧 ${readSide.length} 处 [${readSide.join(',')}]`);
  if (readSide.length === 0) { pass = false; console.log('  ✘ 无主表侧读取!'); }
}
// log 文案检查 (主表行迁移标记)
const marker = allPos('在途库存(主表行)');
console.log('迁移标记「在途库存(主表行)」:', marker.length, '处', marker.length > 0 ? '✓' : '✘');
if (marker.length === 0) pass = false;
console.log(pass ? '\n>>> v11 部署产物校验 PASS' : '\n>>> v11 部署产物校验 FAIL');
process.exit(pass ? 0 : 1);
