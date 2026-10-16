import profitData from './profit_data.json';

// 2026-10-10 修复: 运行时从 Base《利润测算表》实时构建的成本字典 (由 src_app.js 注入)。
// 此前只读构建时内嵌的 profit_data.json 快照——Base 更新成本后插件永远用旧快照,
// 导致插件与 Python 口径分裂 (旧成本下 A/B 全亏 -> 误选 C激励, 写回负净额)。
// 现在运行时字典优先, 内嵌快照仅作利润表不可读时的兜底。
let runtimeProfit = null;

export function setRuntimeProfit(dict) {
  runtimeProfit = dict;
}

export function getRuntimeProfit() {
  return runtimeProfit;
}

function lookup(it) {
  if (!it) return null;
  if (runtimeProfit && runtimeProfit[it] != null) return runtimeProfit[it];
  return profitData[it] || null;
}

// 从利润表记录行构建成本字典 (与 Python cost_helper.CostProvider 同口径):
//   Item 逗号分隔多 item 行 -> 每个 item 共享该行成本; 单 item 行优先
//   buy = 采购价$ ?? 采购 | head/tariff/last 直接读 | cbm 由长宽高算
export function buildProfitDictFromRecords(rows) {
  const single = {};
  const multi = {};
  for (const r of rows) {
    const it = toTxt(r.fields ? r.fields["Item"] : r["Item"]);
    if (!it) continue;
    const f = r.fields || r;
    const buy = toN(f["采购价$"]) ?? toN(f["采购"]) ?? 0;
    const head = toN(f["头程"]) ?? 0;
    const tariff = toN(f["关税"]) ?? 0;
    const last = toN(f["尾程"]) ?? 0;
    const l = toN(f["长cm"]), w = toN(f["宽cm"]), h = toN(f["高cm"]);
    const cbm = (l > 0 && w > 0 && h > 0) ? (l * w * h) / 1000000 : 0;
    const wk = toN(f["毛重kg"]);
    const weight_lbs = wk > 0 ? wk * 2.20462 : 0;
    const parts = it.split(",").map(s => s.trim()).filter(Boolean);
    if (parts.length === 1) single[parts[0]] = { buy, head, tariff, last, cbm, weight_lbs };
    else for (const p of parts) if (multi[p] == null) multi[p] = { buy, head, tariff, last, cbm, weight_lbs };
  }
  return { ...multi, ...single }; // 单 item 行优先 (覆盖多 item 共享行)
}

function toN(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return v;
  if (Array.isArray(v)) {
    if (v.length === 0) return null;
    if (typeof v[0] === "number") return v[0];
    if (v[0] && v[0].text) { const n = parseFloat(v[0].text); return isNaN(n) ? null : n; }
    return null;
  }
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

function toTxt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return v.map(x => typeof x === "string" ? x : ((x && (x.text || x.name)) || "")).join(",").trim();
  if (typeof v === "object") return (v && (v.text || v.name)) || "";
  return String(v).trim();
}

export function getProfitItem(it) {
  return lookup(it);
}

export function getItemCostAndDim(it, rowObj) {
  const p = lookup(it);
  let buy = p ? p.buy : 0;
  let head = p ? p.head : 0;
  let tariff = p ? p.tariff : 0;
  let last = p ? p.last : 0;
  let cbm = p ? p.cbm : 0;
  let weight_lbs = p ? p.weight_lbs : (rowObj?.weight || 0);

  // 兜底回退清库主表字段
  if (buy <= 0 && rowObj?.buy_price > 0) buy = rowObj.buy_price;
  if (head <= 0 && rowObj?.pp_first > 0 && buy > 0) head = Math.max(0, rowObj.pp_first - buy);

  return { buy, head, tariff, last, cbm, weight_lbs };
}

export function getModelCostAndCbm(mRec, subs, recById) {
  if (subs && subs.length > 0) {
    let buy = 0, head = 0, tariff = 0, last = 0, cbm = 0;
    for (const s of subs) {
      const sRow = recById ? recById[s.record_id] : s;
      const info = getItemCostAndDim(s.item || sRow?.item, sRow);
      buy += info.buy;
      head += info.head;
      tariff += info.tariff;
      last += info.last;
      cbm += info.cbm;
    }
    return { buy, head, tariff, last, cbm };
  } else {
    const info = getItemCostAndDim(mRec.item, mRec);
    return {
      buy: info.buy,
      head: info.head,
      tariff: info.tariff,
      last: info.last,
      cbm: info.cbm
    };
  }
}
