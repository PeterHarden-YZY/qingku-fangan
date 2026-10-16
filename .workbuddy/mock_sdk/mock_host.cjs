// mock 宿主: 完整模拟插件运行所需的 bitable.base 行为 (与线上真实数据形状一致)
// 语义对齐 @lark-base-open/js-sdk v1.0.2:
//   Table.getFieldList()       → Field 实例数组 (只有 getName() 方法, 无 .name)
//   Table.getFieldMetaList()   → {id, name, type}[]
//   Table.addField(cfg)        → {id}
//   Table.setRecords(list)     → 按字段 id 写回
const fs = require('fs');
const path = require('path');

const C = path.resolve(__dirname, '..', '..');
const os = require('os');
const { execFileSync } = require('child_process');
const BASE_TOKEN = 'HPsJbdMCva1IersNsFQco976n7B';
const MAIN_TABLE = 'tblvEAMIFUSzjYmW';
const STOCK_TABLE = 'tblNnE4WXy0esyGt';
const PARAMS_TABLE = 'tblaGnwkT7yEa8cW';
const PROFIT_TABLE = 'tblHVAkfBvpBHPh4'; // 2026-10-10: 利润测算表(成本权威源), 插件运行时实读

function larkCli() {
  const cands = [path.join(os.homedir(), '.workbuddy/binaries/node/cli-connector-packages/lark-cli.cmd')];
  for (const c of cands) if (fs.existsSync(c)) return c;
  return 'lark-cli';
}

function runCli(args) {
  const cli = larkCli();
  const opts = { timeout: 120000, encoding: 'utf8' };
  if (cli.endsWith('.cmd')) opts.shell = true; // .cmd 必须经 shell 执行
  return require('child_process').execFileSync(cli, args, opts);
}

// 2026-09-18 取消缓存: 每次启动实时拉取线上表到内存, 分页临时文件即用即删
function fetchTableLive(tableId, label) {
  const all = [];
  let offset = 0;
  while (true) {
    const tmp = path.join(os.tmpdir(), 'mock_host_' + tableId + '_' + offset + '.ndjson');
    try {
      runCli(['base', '+record-list', '--base-token', BASE_TOKEN, '--table-id', tableId,
        '--limit', '2000', '--offset', String(offset), '--format', 'ndjson', '--output', tmp, '--overwrite', '--as', 'user']);
      const lines = fs.readFileSync(tmp, 'utf8').split('\n').filter(l => l.trim());
      for (const l of lines) all.push(JSON.parse(l));
      if (lines.length < 2000) break;
      offset += lines.length;
    } catch (e) {
      throw new Error('[mock_host] 实时拉取 ' + label + '(' + tableId + ') 失败 @offset=' + offset + ': ' + e.message);
    } finally {
      try { fs.unlinkSync(tmp); } catch (_) {}
    }
  }
  console.log('[mock_host] 实时拉取 ' + label + ': ' + all.length + ' 行 (无缓存)');
  return all;
}

const rows = fetchTableLive(MAIN_TABLE, '清库方案成本核算');
const stockRows = fetchTableLive(STOCK_TABLE, '库存表');
const paramsRows = fetchTableLive(PARAMS_TABLE, '清库参数配置');
const profitRows = fetchTableLive(PROFIT_TABLE, '利润测算表');

// 主表当前真实字段清单 (2026-09-18 更新: 在途/工厂输入字段迁移到主表;
// 傍晚更新: +手动补充item(item兜底) +最优窗口方案(W*下方案说明))
const MAIN_FIELDS = [
  "model", "item", "手动补充item", "售价", "采购价+头程", "固定成本合计", "过去30天销量",
  "独立站渠道库存", "海外仓库存总量", "海外仓库存（非独立站）", "DTC渠道库存(渠道齐套无需调拨）",
  "组件库龄", "部件重量 lbs", "清库标记", "处理方案", "父记录", "固定成本合计（明细）",
  "实际齐套数", "最佳方案", "预计回收资金", "报废数量", "报废成本", "最优窗口期(天)", "最优窗口方案",
  "采购价", "海外仓库存总量_US", "长版剩余", "子model重量（lbs）", "备注",
  "在途库存", "在途到货时间(天)", "工厂未发货数量"
];

class MockField {
  constructor(id, name, type) { this._n = name; this._t = type; this.id = id; }
  getName() { return Promise.resolve(this._n); }
  getType() { return Promise.resolve(this._t); }
}

const calls = { addField: [], setRecords: [], addRecord: [], errors: [] };

function makeTable(name, fields, records, fieldTypeMap) {
  const flds = fields.slice();
  const typeMap = Object.assign({ "最佳方案": 1, "处理方案": 4 }, fieldTypeMap || {});
  return {
    getName: () => Promise.resolve(name),
    // SDK 真实语义: Field 实例 (无 .name 属性!)
    getFieldList: () => Promise.resolve(flds.map((n, i) => new MockField('fld_' + i + '_' + n, n, typeMap[n] || 2))),
    getFieldMetaList: () => Promise.resolve(flds.map((n, i) => ({ id: 'fld_' + i + '_' + n, name: n, type: typeMap[n] || 2 }))),
    addField: (cfg) => {
      calls.addField.push(cfg);
      flds.push(cfg.name);
      const id = 'fld_new_' + cfg.name;
      typeMap[cfg.name] = cfg.type;
      return Promise.resolve({ id });
    },
    getRecords: () => Promise.resolve({
      records: records.map(r => ({ recordId: r.record_id, fields: toOpen(r, flds) })),
      hasMore: false, pageToken: undefined
    }),
    setRecords: (list) => {
      calls.setRecords.push(list);
      // 语义校验: 传入的字段 id 必须在表里存在
      for (const u of list) {
        for (const fid of Object.keys(u.fields)) {
          if (!flds.some(n => 'fld_' + flds.indexOf(n) + '_' + n === fid) && !fid.startsWith('fld_new_')) {
            calls.errors.push('setRecords 用了不存在字段id: ' + fid);
          }
        }
      }
      return Promise.resolve();
    },
    addRecord: ({ fields }) => { calls.addRecord.push(fields); return Promise.resolve(); },
    getRecordIdList: () => Promise.resolve(records.map(r => r.record_id)),
  };
}

// 对齐真实 SDK: fields 以 fieldId 为键 (官方 IRecord.fields: {[fieldId]: IOpenCellValue})
// 这是 2026-09-18 线上 K=0 的根因: 旧 mock 用字段名做键, 掩盖了业务代码按名读取的 bug
function toOpen(r, fields) {
  const f = {};
  for (let i = 0; i < fields.length; i++) {
    const name = fields[i];
    if (!(name in r)) continue;
    let v = r[name];
    if (v === null || v === undefined) continue;
    // 父记录 link 字段 -> IOpenLink 格式 (真实 SDK 语义: {recordIds: string[]})
    if (name === '父记录' && Array.isArray(v)) {
      const ids = v.map(x => (x && (x.id || x.record_id)) || null).filter(Boolean);
      v = { text: ids.join(','), type: 'text', recordIds: ids, tableId: MAIN_TABLE };
    }
    f['fld_' + i + '_' + name] = v;
  }
  return f;
}

const tables = {
  main: makeTable('清库模式成本核算'.replace('式', '方'), MAIN_FIELDS, rows),   // 清库方案成本核算
  stock: makeTable('库存表', ['item', '所属团队', '仓库类型', '可用量', '库龄(程序拉取)'], stockRows),
  params: makeTable('清库参数配置', ['场景名', '是否启用', '销量预测窗口(天)', 'A降价幅度', 'B线下折扣', 'C促销售价折扣', 'A推广费率', '销量放大系数λ', '放大系数上限', '放大系数下限', '奖金档1上限(万USD)', '奖金档1比例', '奖金档2上限(万USD)', '奖金档2比例', '奖金档3比例', '报废费-轻(USD/件)', '报废费-重(USD/件)', '重量分界(lbs)', '计提库龄分界1(天)', '计提比例1(120-180天)', '计提库龄分界2(天)', '计提比例2(181-270天)', '计提比例3(>270天)'], paramsRows),
  profit: makeTable('利润测算表-导入多维表格', ['Item', '采购', '采购价$', '头程', '关税', '尾程', '长cm', '宽cm', '高cm', '毛重kg'], profitRows),
  log: makeTable('测算运行日志', ['运行时间戳', '测算场景', '关键参数快照', '全局最优窗口(天)'], []),
};

// 场景开关: 删除指定字段, 模拟「字段缺失触发 addField」的线上报错路径
if (process.env.MOCK_DROP_FIELDS) {
  const dropped = process.env.MOCK_DROP_FIELDS.split(',');
  const kept = MAIN_FIELDS.filter(f => !dropped.includes(f));
  tables.main = makeTable('清库方案成本核算', kept, rows);
  console.log(`[mock] 主表已移除字段: ${dropped.join(', ')} (模拟字段缺失场景)`);
}

module.exports = { tables, calls };
