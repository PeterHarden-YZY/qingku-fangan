// 第四轮合成数据 e2e: 注入在途到货时间 + 工厂未发货数量, 验证决策链真正执行
// 用法: node .workbuddy/run_plugin_test_r4.cjs           (从 src 构建, 注入合成数据)
//       NO_INJECT=1 node .workbuddy/run_plugin_test_r4.cjs  (基线对照组, 不注入)
//       BUNDLE_FILE=<path> node ...                       (直接测指定部署产物)
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const C = path.resolve(__dirname, '..');
const srcPath = path.join(C, 'plugin_app', 'src_app.js');
const tmpBundle = path.join(C, '.workbuddy', 'test_bundle_r4.js');

if (process.env.BUNDLE_FILE) {
  // 直接复用指定部署产物 (alias 已在构建时内联, 无需重新构建)
  fs.copyFileSync(process.env.BUNDLE_FILE, tmpBundle);
  console.log(`[harness] 使用指定部署产物: ${process.env.BUNDLE_FILE}`);
} else {
  execSync(`npx esbuild "${srcPath}" --bundle --platform=browser --format=iife --outfile="${tmpBundle}" ` +
           `--alias:@lark-base-open/js-sdk="${path.join(C, '.workbuddy/mock_sdk/mock_js_sdk.mjs')}"`,
           { cwd: path.join(C, 'plugin_app'), stdio: 'pipe' });
}

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
    if (id === 'logBox') return Object.assign(makeEl(), { innerHTML: '', logs: [] });
    if (id === 'runBtn') return Object.assign(makeEl(), { disabled: false });
    if (id === 'borrowMode') return Object.assign(makeEl(), { value: 'borrow' });
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

const { tables, calls } = require('./mock_sdk/mock_host.cjs');
const bundleCode = fs.readFileSync(tmpBundle, 'utf-8');
const sdkModule = require('./mock_sdk/mock_js_sdk.esm.cjs');

// ---- 合成注入 (2026-09-18 迁移后): 在途/工厂输入字段挂在《清库方案成本核算》主表行 ----
// BOM 款填子记录行, 单件款填主记录行; 库存表不再承载人工输入
// 环境变量 NO_INJECT=1 跑基线对照组
const NO_INJECT = process.env.NO_INJECT === '1';
const stockTable = tables.stock;
const mainTable = tables.main;
const origGetRecords = stockTable.getRecords;
const origMainGetRecords = mainTable.getRecords;

tables.main = Object.assign({}, mainTable, {
  getRecords: async () => {
    const res = await origMainGetRecords();
    const metas = await mainTable.getFieldMetaList();
    const nameToId = {};
    for (const m of metas) nameToId[m.name] = m.id;
    const injected = res.records.map(r => ({ recordId: r.recordId, fields: { ...r.fields } }));
    const isSubOf = (f) => {
      const pl = f[nameToId['父记录']];
      if (!pl) return false;
      if (Array.isArray(pl)) return pl.length > 0;
      return !!(pl.recordIds && pl.recordIds.length);
    };
    const modelOf = (f) => String(f[nameToId['model']] || '');

    // 1) 在途注入 (2026-09-18 迁移后): 输入字段挂在主表行 (单件款填主记录行)
    //    HNOFC39DN-LH-US: dtc=10, s30=36 高动销 -> 在途500件30天到货, K 应显著上升
    //    AMESD01NW-V-US: 无货无销量 -> 在途200件90天到货, 超窗排除, K 保持 0
    const TRANSIT_TARGETS = [
      { model: 'HNOFC39DN-LH-US', qty: 500, eta: 30 },
      { model: 'AMESD01NW-V-US', qty: 200, eta: 90 },
    ];
    if (!NO_INJECT) {
      for (const tgt of TRANSIT_TARGETS) {
        for (const r of injected) {
          const f = r.fields;
          if (!isSubOf(f) && modelOf(f) === tgt.model) {
            f[nameToId['在途库存']] = tgt.qty;
            f[nameToId['在途到货时间(天)']] = tgt.eta;
            console.log(`[注入·在途] ${tgt.model}: 在途${tgt.qty}件, ${tgt.eta}天到货 -> ${tgt.eta <= 60 ? '入池' : '超窗排除'}`);
          }
        }
      }
    }

    // 2) 工厂: 给有决策资格的 model 注入工厂未发货数量 (主记录行, K>0 才走决策)
    //    AMBFC05NR-US / HNOFC38DN-LH-US 均为单件款 -> 填主记录行
    const FAC_MODELS = ['AMBFC05NR-US', 'HNOFC38DN-LH-US'];
    if (!NO_INJECT) {
      let facSeen = 0;
      for (const r of injected) {
        const f = r.fields;
        if (!isSubOf(f) && FAC_MODELS.includes(modelOf(f))) {
          f[nameToId['工厂未发货数量']] = 200;
          facSeen++;
          console.log(`[注入·工厂] ${modelOf(f)}: 200 件未发货`);
        }
      }
    }
    return { records: injected, hasMore: false, pageToken: undefined };
  },
});
tables.stock = stockTable; // 库存表不再注入 (纯数据源)

globalThis.__MOCK_BITABLE_BASE__ = {
  getTableList: () => Promise.resolve([tables.main, tables.stock, tables.params, tables.profit, tables.log]),
};
sdkModule.bitable.base = globalThis.__MOCK_BITABLE_BASE__;

(async () => {
  console.log(`\n========== 第四轮合成数据 e2e ==========`);
  const fn = new Function('window', 'document', 'bitable', 'FieldType', bundleCode + '\nreturn window.startExecution;');
  const startExec = fn(global, global.document, sdkModule.bitable, sdkModule.FieldType);

  try {
    await startExec();
    console.log('\n>>> 结果: 全程无异常 ✔');
  } catch (e) {
    console.log('\n>>> 结果: 抛错 ✘');
    console.log('    Error:', e.message, e.stack && e.stack.split('\n')[1]);
    process.exit(1);
  }

  // ---- 校验写回值 ----
  // mock id 形状: 原生= fld_<i>_<name>, addField= fld_new_<name> — 统一取最后一个 _ 后的名字
  const fnameOf = (fid) => {
    const s = String(fid);
    return s.startsWith('fld_new_') ? s.slice(8) : (s.includes('_') ? s.split('_').slice(2).join('_') : s);
  };
  let facWritten = [];
  for (const batch of calls.setRecords) {
    for (const u of batch) {
      const names = {};
      for (const [fid, v] of Object.entries(u.fields)) names[fnameOf(fid)] = v;
      if (names['工厂发货决策'] && String(names['工厂发货决策']).length > 0) {
        facWritten.push(names);
      }
    }
  }
  console.log(`\n[校验] 有工厂决策写回的记录: ${facWritten.length} 条`);
  for (const w of facWritten) {
    console.log(`  决策="${w['工厂发货决策']}" 发货套数=${w['工厂发货套数']} 不发货亏损=${w['工厂不发货亏损']}`);
  }
  if (facWritten.length === 0 && !NO_INJECT) {
    console.log('  ✘ 未找到任何工厂决策写回!');
    process.exit(1);
  }
  const shipQtys = facWritten.map(w => w['工厂发货套数'] || 0);
  if (!shipQtys.some(q => q === 200 || q === 100)) {
    console.log('  ⚠️ 发货套数非 0/100/200, 请人工核对: ', shipQtys);
  }
  // ---- 校验总K与在途注入效果 (从写回调用提取) ----
  const mainRes = await tables.main.getRecords();
  const recById = new Map(mainRes.records.map(r => [r.recordId, r.fields]));
  const isSubRecord = (fn) => {
    const pl = fn['父记录'];
    if (!pl) return false;
    if (Array.isArray(pl)) return pl.length > 0;
    return !!(pl.recordIds && pl.recordIds.length);
  };
  const kByModel = {};
  for (const batch of calls.setRecords) {
    for (const u of batch) {
      const names = {};
      for (const [fid, v] of Object.entries(u.fields)) names[fnameOf(fid)] = v;
      const f = recById.get(u.recordId);
      if (!f) continue;
      const fName = {};
      for (const [fid, v] of Object.entries(f)) fName[fnameOf(fid)] = v;
      const model = String(fName['model'] || '');
      if (!isSubRecord(fName) && model && names['实际齐套数'] !== undefined) {
        kByModel[model] = names['实际齐套数'];
      }
    }
  }
  const totalK = Object.values(kByModel).reduce((a, b) => a + (b || 0), 0);
  console.log(`\n[校验·K] 写回主记录数=${Object.keys(kByModel).length}, 总K=${totalK}`);
  console.log(`[校验·K] HNOFC39DN-LH-US K=${kByModel['HNOFC39DN-LH-US']} (基线约10, 注入在途500后应显著上升)`);
  console.log(`[校验·K] AMESD01NW-V-US K=${kByModel['AMESD01NW-V-US']} (在途超窗排除, 应保持0)`);

  // ---- 2026-10-10 覆盖率校验: 全部主记录是否都被写回 (K=0 行也要清值, 不能漏) ----
  const writtenIds = new Set();
  for (const batch of calls.setRecords) for (const u of batch) writtenIds.add(u.recordId);
  const mainRes2 = await tables.main.getRecords();
  const allMains = mainRes2.records.filter(r => !isSubRecord(r.fields));
  const missed = allMains.filter(r => !writtenIds.has(r.recordId));
  console.log(`\n[校验·覆盖] 主记录共 ${allMains.length} 行 | 已写回 ${allMains.length - missed.length} 行 | 未写回 ${missed.length} 行`);
  for (const r of missed.slice(0, 15)) {
    const fName = {};
    for (const [fid, v] of Object.entries(r.fields)) fName[fnameOf(fid)] = v;
    console.log(`  ✘ 未写回: ${fName['model'] || '(无model)'} [${r.recordId}]`);
  }
  if (missed.length === 0) console.log('  ✓ 全部主记录均被写回');
  globalThis.__RESULT_TOTAL_K__ = totalK;
  globalThis.__RESULT_K_BY_MODEL__ = kByModel;
})();
