// 复用 e2e mock 宿主，重放插件并 dump setRecords 明细，与主表现值/Python 结果对账
const fs = require('fs');
const path = require('path');
const C = path.resolve(__dirname, '..');
const mock = require(path.join(C, '.workbuddy/mock_sdk/mock_host.cjs'));
const sdkModule = require(path.join(C, '.workbuddy/mock_sdk/mock_js_sdk.esm.cjs'));
globalThis.__MOCK_BITABLE_BASE__ = {
  getTableList: () => Promise.resolve([mock.tables.main, mock.tables.stock, mock.tables.params, mock.tables.profit, mock.tables.log]),
};
sdkModule.bitable.base = globalThis.__MOCK_BITABLE_BASE__;
global.window = global;
global.addEventListener = () => {};
global.removeEventListener = () => {};

function makeEl() {
  return { style: {}, dataset: {}, children: [], attributes: {}, innerHTML: '',
    classList: { add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) { this.attributes[k] = v; }, appendChild(c) { this.children.push(c); return c; },
    addEventListener() {}, querySelector: () => null, querySelectorAll: () => [], focus() {}, blur() {} };
}
global.document = {
  createElement: () => makeEl(), createTextNode: () => makeEl(),
  getElementById: (id) => {
    if (id === 'logBox') return makeEl();
    if (id === 'runBtn') return Object.assign(makeEl(), { disabled: false });
    if (id === 'borrowMode') return Object.assign(makeEl(), { value: 'borrow' });
    if (id === 'resultCard') return Object.assign(makeEl(), { style: {} });
    for (const s of ['statK', 'statScrap', 'statBestNet', 'statModels'])
      if (id === s) return Object.assign(makeEl(), { innerText: '' });
    return null;
  },
  body: makeEl(), addEventListener() {},
};
global.window = { startExecution: null, clearLogs: () => {} };

(async () => {
  const bundleCode = fs.readFileSync(path.join(C, '.workbuddy/test_bundle_src_app_js.js'), 'utf8');
  const fn = new Function('window', 'document', 'bitable', 'FieldType', bundleCode + '\nreturn window.startExecution;');
  const startExec = fn(global, global.document, sdkModule.bitable, sdkModule.FieldType);
  await startExec();

  const metas = await mock.tables.main.getFieldMetaList();
  const name2id = {}; for (const m of metas) name2id[m.name] = m.id;
  const id2name = {}; for (const m of metas) id2name[m.id] = m.name;
  const allRecs = mock.calls.setRecords.flat();
  console.log('setRecords 总条数:', allRecs.length);
  if (allRecs.length > 0) {
    console.log('样例写回项 keys:', Object.keys(allRecs[0]), '| recordId 样例:', allRecs[0].recordId);
    console.log('样例 fields keys:', Object.keys(allRecs[0].fields).slice(0, 8));
  }
  const mainRowsFile = fs.readFileSync(path.join(C, '.workbuddy/tmp_main_now.ndjson'), 'utf8')
    .split('\n').filter(l => l.trim()).map(l => JSON.parse(l));
  console.log('主表 ndjson 行数:', mainRowsFile.length, '| 样例 record_id:', mainRowsFile[0] && mainRowsFile[0].record_id);

  // record_id -> 写回内容 (同一 record 有两条 update: 分配循环+报废循环, 按字段合并)
  const wrote = {};
  for (const u of allRecs) {
    if (!wrote[u.recordId]) wrote[u.recordId] = {};
    for (const [fid, v] of Object.entries(u.fields)) wrote[u.recordId][id2name[fid] || fid] = v;
  }

  // 与主表现值对比
  const main = mainRowsFile;
  const wroteKeys = Object.keys(wrote);
  const mainIds = main.map(r => r.record_id);
  const inter = mainIds.filter(id => wroteKeys.includes(id));
  console.log('写回 recordId 数:', wroteKeys.length, '| 主表 record_id 数:', mainIds.length, '| 交集:', inter.length);
  if (inter.length === 0) {
    console.log('写回样例:', wroteKeys.slice(0, 5));
    console.log('主表样例:', mainIds.slice(0, 5));
  }
  const scen = JSON.parse(fs.readFileSync(path.join(C, '.workbuddy/scenario_result.json'), 'utf8'));
  const py = {}; for (const r of scen.results) py[r.model] = r;

  let kSum = 0, recSum = 0;
  const diffs = [];
  for (const r of main) {
    const par = r['父记录'];
    const isSub = Array.isArray(par) ? par.length > 0 : !!par;
    if (isSub) continue; // 只看主记录
    const w = wrote[r.record_id] || {};
    const wK = typeof w['实际齐套数'] === 'number' ? w['实际齐套数'] : null;
    const wPlan = String(w['最佳方案'] ?? '');
    const wRec = typeof w['预计回收资金'] === 'number' ? w['预计回收资金'] : null;
    const model = String(r.model || '');
    if (wK !== null) kSum += wK;
    if (wRec !== null) recSum += wRec;
    // 主表现值
    const tK = r['实际齐套数'] !== null && r['实际齐套数'] !== undefined ? Number(r['实际齐套数']) : null;
    const tPlan = String(r['最佳方案'] ?? '');
    const tRec = r['预计回收资金'] !== null && r['预计回收资金'] !== undefined ? Number(r['预计回收资金']) : null;
    const p = py[model];
    const pK = p ? p.K : null;
    const pPlan = p ? p.best_plan : '';
    const sameAsTable = (wK !== null && tK !== null && Math.abs(wK - tK) < 0.5 && wPlan === tPlan);
    if (!sameAsTable) diffs.push({ model, wK, wPlan: wPlan.slice(0, 40), tK, tPlan: tPlan.slice(0, 40), pK, pPlan: pPlan.slice(0, 40) });
  }
  console.log('插件重放 K 合计 =', kSum, '| 回收合计 =', Math.round(recSum));
  const nTable = main.filter(r => !r['父记录'] && wrote[r.record_id]).length;
  console.log('主记录被写回条数 =', nTable, '/ 92');
  console.log('与主表现值不一致(插件重放 vs 主表):', diffs.length, '条');
  for (const d of diffs.slice(0, 20)) console.log('  ', JSON.stringify(d));
})().catch(e => { console.error('ERR', e); process.exit(1); });
