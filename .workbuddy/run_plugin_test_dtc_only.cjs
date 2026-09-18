// 测试驱动: 用 mock 宿主 + 真实缓存数据完整跑 startExecution
// 用法: node .workbuddy/run_plugin_test.cjs [src_app_fixed.js]   (默认测当前 plugin_app/src_app.js)
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const C = path.resolve(__dirname, '..');
const srcName = process.argv[2] || 'src_app.js';
const srcPath = path.join(C, 'plugin_app', srcName);
const tmpBundle = path.join(C, '.workbuddy', 'test_bundle_' + srcName.replace(/\W/g, '_') + '.js');

// 1) esbuild 打包, alias @lark-base-open/js-sdk → mock SDK (保留真实 SDK 语义差异)
execSync(`npx esbuild "${srcPath}" --bundle --platform=browser --format=iife --outfile="${tmpBundle}" ` +
         `--alias:@lark-base-open/js-sdk="${path.join(C, '.workbuddy/mock_sdk/mock_js_sdk.mjs')}"`,
         { cwd: path.join(C, 'plugin_app'), stdio: 'pipe' });

// 2) DOM/window mock
function makeEl() {
  return {
    style: {}, dataset: {}, children: [], attributes: {}, innerHTML: '',
    classList: { add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) { this.attributes[k] = v; },
    appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    focus() {}, blur() {},
  };
}
global.document = {
  createElement: () => makeEl(),
  createTextNode: () => makeEl(),
  getElementById: (id) => {
    if (id === 'logBox') return makeEl();
    if (id === 'runBtn') return Object.assign(makeEl(), { disabled: false });
    if (id === 'borrowMode') return Object.assign(makeEl(), { value: 'dtc_only' }); // dtc_only_REPLAY
    if (id === 'resultCard') return Object.assign(makeEl(), { style: {} });
    for (const stat of ['statK', 'statScrap', 'statBestNet', 'statModels']) {
      if (id === stat) return Object.assign(makeEl(), { innerText: '' });
    }
    return null;
  },
  addEventListener() {}, removeEventListener() {},
  querySelector: () => null, querySelectorAll: () => [],
  body: makeEl(), documentElement: makeEl(), head: makeEl(),
};
global.window = global;
global.addEventListener = () => {};
global.removeEventListener = () => {};

// 3) 注入 mock bitable: 通过全局变量桥接 (esbuild 打包后 bundle 内持有自己的 SDK 副本)
const { tables, calls } = require('./mock_sdk/mock_host.cjs');
const bundleCode = fs.readFileSync(tmpBundle, 'utf-8');
const sdkModule = require('./mock_sdk/mock_js_sdk.esm.cjs');

// 把 mock 宿主挂到全局 (mock SDK 的 getter 会读到)
globalThis.__MOCK_BITABLE_BASE__ = {
  getTableList: () => Promise.resolve([tables.main, tables.stock, tables.params, tables.log]),
};
// 同时导出一份(测试诊断用)
sdkModule.bitable.base = globalThis.__MOCK_BITABLE_BASE__;

(async () => {
  const label = srcName === 'src_app.js' ? '【修复前·线上版】' : '【修复后】';
  console.log(`\n========== ${label} ${srcName} ==========`);
  // 4) 执行 bundle (IIFE, 挂 window.startExecution)
  const fn = new Function('window', 'document', 'bitable', 'FieldType', bundleCode + '\nreturn window.startExecution;');
  // 注: src 通过 import 使用 FieldType 顶层导出 → 打包后 FieldType 在 bundle 作用域内, 无需注入
  const startExec = fn(global, global.document, sdkModule.bitable, sdkModule.FieldType);
  if (typeof startExec !== 'function') { console.log('startExecution 未定义!'); return; }

  try {
    await startExec();
    console.log('\n>>> 结果: 全程无异常 ✔');
  } catch (e) {
    console.log('\n>>> 结果: 抛错 ✘');
    console.log('    Error:', e.message);
  }
  console.log('    addField 调用:', JSON.stringify(calls.addField));
  console.log('    setRecords 批次数:', calls.setRecords.length, '| 总记录数:', calls.setRecords.reduce((a, b) => a + b.length, 0));
  console.log('    addRecord (日志表):', JSON.stringify(calls.addRecord));
  console.log('    MSESD52N 检查: ' + JSON.stringify(calls.setRecords.flat().filter(u => (u.fields||{}).hasOwnProperty && Object.values(u.fields).some(v => v === 0)).length) + ' 条零值记录');
  if (process.env.DUMP_FILE) {
    require('fs').writeFileSync(process.env.DUMP_FILE, JSON.stringify(calls.setRecords));
    console.log('    setRecords 明细已 dump');
  }
  console.log('    语义错误:', calls.errors.length ? calls.errors : '无');
})();
