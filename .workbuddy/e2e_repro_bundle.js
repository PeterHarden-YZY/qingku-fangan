// 端到端复现: 用昨天真实缓存数据 + mock bitable 环境跑线上 bundle 的 startExecution
// 目的: 确定报错顺序与错误链 (windowStats / bitable.FieldType / globalBestWindow TDZ)
const fs = require('fs');
const path = require('path');

const C = path.resolve(__dirname, '..');
const rows = JSON.parse(fs.readFileSync(path.join(C, '.workbuddy/inventory_cache.json'), 'utf-8'));
const stockRows = JSON.parse(fs.readFileSync(path.join(C, '.workbuddy/stock_rows_cache.json'), 'utf-8'));
const paramsRows = [];
try {
  for (const line of fs.readFileSync(path.join(C, '.workbuddy/params_raw.ndjson'), 'utf-8').split('\n')) {
    if (line.trim()) paramsRows.push(JSON.parse(line));
  }
} catch (e) { console.log('(params_raw 缓存不可用, 用空表模拟)'); }

// ---- mock bitable: 模拟真实飞书宿主行为 ----
// 关键: 按真实 SDK 语义模拟 —— Table.getFieldList() 返回 Field 实例(只有 getName() 方法, 无 .name 属性)
class MockField {
  constructor(id, name, type) { this._id = id; this._name = name; this._type = type; }
  get id() { return this._id; }                       // Field 实例有 id getter
  getName() { return Promise.resolve(this._name); }   // name 只能通过 getName() 方法获取!
  getType() { return Promise.resolve(this._type); }
}

let createdFields = [];   // 记录 addField 调用
let setRecordsCalls = []; // 记录写回调用
let addRecordCalls = [];

const MAIN_FIELDS = [];   // 先给空 → 复现「字段全部缺失」场景(线上运行时的真实状态推断)
// 主表真实 27 字段(按今天 field-list 结果)
const mainFieldDefs = [
  "model", "item", "售价", "采购价+头程", "固定成本合计", "过去30天销量",
  "独立站渠道库存", "海外仓库存总量", "海外仓库存（非独立站）", "DTC渠道库存(渠道齐套无需调拨）",
  "组件库龄", "部件重量 lbs", "清库标记", "处理方案", "父记录", "固定成本合计（明细）",
  "实际齐套数", "最佳方案", "预计回收资金", "报废数量", "报废成本", "最优窗口期(天)",
  "采购价", "海外仓库存总量_US", "长版剩余", "子model重量（lbs）", "备注"
];

function makeTable(name, fields, records) {
  const fieldsById = {};
  fields.forEach((n, i) => fieldsById['fld_' + n] = n);
  return {
    getName: () => Promise.resolve(name),
    // 真实 SDK 语义: 返回 Field 实例 (f.name === undefined!)
    getFieldList: () => Promise.resolve(fields.map((n, i) => new MockField('fld_' + n, n, 2))),
    // 正确 API: 返回 {name, id} 元信息数组
    getFieldMetaList: () => Promise.resolve(fields.map((n, i) => ({ id: 'fld_' + n, name: n, type: 2 }))),
    addField: (cfg) => {
      createdFields.push(cfg);
      const id = 'fld_new_' + cfg.name;
      fieldsById[id] = cfg.name;
      fields.push(cfg.name);
      return Promise.resolve({ id });   // IFieldRes {id}
    },
    getRecords: ({ pageSize, pageToken }) => {
      return Promise.resolve({ records: records.map(r => ({ recordId: r.record_id, fields: toOpenFields(r) })), hasMore: false, pageToken: undefined });
    },
    setRecords: (payload) => { setRecordsCalls.push(payload); return Promise.resolve(); },
    addRecord: ({ fields }) => { addRecordCalls.push(fields); return Promise.resolve(); },
    getRecordIdList: () => Promise.resolve(records.map(r => r.record_id)),
  };
}

// 行数据转飞书 open fields 形状
function toOpenFields(r) {
  const f = {};
  for (const [k, v] of Object.entries(r)) {
    if (['record_id', '父记录'].includes(k)) continue;
    if (v === null || v === undefined) continue;
    f[k] = v;
  }
  if (r['父记录']) f['父记录'] = r['父记录'];
  return f;
}

const mainTable = makeTable('清库方案成本核算', [...mainFieldDefs], rows);
const stockTable = makeTable('库存表', ['item', '所属团队', '仓库类型', '可用量', '库龄(程序拉取)'], stockRows);
const paramsTable = makeTable('清库参数配置', ['场景名', '是否启用', '销量预测窗口(天)', 'A降价幅度', 'B线下折扣', 'C促销售价折扣', 'A推广费率', '销量放大系数λ', '放大系数上限', '放大系数下限', '奖金档1上限(万USD)', '奖金档1比例', '奖金档2上限(万USD)', '奖金档2比例', '奖金档3比例', '报废费-轻(USD/件)', '报废费-重(USD/件)', '重量分界(lbs)', '计提库龄分界1(天)', '计提比例1(120-180天)', '计提库龄分界2(天)', '计提比例2(181-270天)', '计提比例3(>270天)'], paramsRows);
const logTable = makeTable('测算运行日志', ['运行时间戳', '测算场景', '关键参数快照', '全局最优窗口(天)'], []);

const bitable = {
  base: { getTableList: () => Promise.resolve([mainTable, stockTable, paramsTable, logTable]) },
  bridge: {}, ui: {}, dashboard: {},
  // 注意: 真实 SDK v1.0.2 中 bitable.FieldType 是 undefined —— 精确模拟!
  FieldType: undefined,
};

// ---- DOM mock (完整版: 兼容 bundle 内置 React UIBuilder 初始化) ----
function makeEl() {
  return {
    style: {}, dataset: {}, children: [], attributes: {},
    classList: { add() {}, remove() {}, contains: () => false },
    setAttribute(k, v) { this.attributes[k] = v; },
    getAttribute(k) { return this.attributes[k]; },
    removeAttribute(k) { delete this.attributes[k]; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
    insertBefore(c) { this.children.push(c); return c; },
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    focus() {}, blur() {}, click() {},
    getBoundingClientRect: () => ({ x: 0, y: 0, width: 100, height: 100, top: 0, left: 0 }),
  };
}
global.document = {
  createElement: () => makeEl(),
  createTextNode: () => makeEl(),
  getElementById: (id) => {
    if (id === 'logBox') return Object.assign(makeEl(), { innerHTML: '' });
    if (id === 'runBtn') return Object.assign(makeEl(), { disabled: false, innerHTML: '' });
    if (id === 'borrowMode') return Object.assign(makeEl(), { value: 'borrow' });
    return null;
  },
  addEventListener() {}, removeEventListener() {},
  querySelector: () => null, querySelectorAll: () => [],
  body: makeEl(), documentElement: makeEl(), head: makeEl(),
};
global.window = global;
global.addEventListener = () => {};
global.removeEventListener = () => {};
global.location = { href: 'https://mock.local/plugin', origin: 'https://mock.local' };
global.navigator = { userAgent: 'node-mock', language: 'zh-CN' };

// ---- 加载 bundle (线上 v=20260916e 内容) ----
const bundleCode = fs.readFileSync(path.join(C, '.workbuddy/deployed_bundle_v7.js'), 'utf-8');

(async () => {
  console.log('=== 复现: 线上 bundle v20260916e + 真实缓存数据 ===\n');
  try {
    // bundle 是浏览器脚本, 用 Function 注入 mock 全局后执行
    const fn = new Function('bitable', 'document', 'window', bundleCode);
    fn(bitable, global.document, global);
    // 等 window.startExecution 被赋值
    await new Promise(r => setTimeout(r, 100));
    if (typeof global.startExecution !== 'function') {
      console.log('startExecution 未定义! bundle 加载失败');
      return;
    }
    await global.startExecution();
    console.log('\n>>> 意外: 全程无报错');
  } catch (e) {
    console.log('\n=== 第一个抛出的错误 ===');
    console.log('Error:', e.message);
    console.log('stack 头部:', String(e.stack).split('\n').slice(0, 3).join(' | '));
  }
  console.log('\ncreatedFields:', JSON.stringify(createdFields));
  console.log('setRecordsCalls:', setRecordsCalls.length, '| addRecordCalls:', addRecordCalls.length);
})();
