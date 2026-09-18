// 验证: 三链路口径一致性 —— 用真实缓存数据在 Node 中运行 base_script/src_app 的贪心核心, 与 Python 对比
const fs = require("fs");
const path = require("path");

const C = path.join(__dirname, "..", ".workbuddy");
const rows = JSON.parse(fs.readFileSync(path.join(C, "inventory_cache.json"), "utf-8"));
const comps = JSON.parse(fs.readFileSync(path.join(C, "components.json"), "utf-8"));
const pyAlloc = JSON.parse(fs.readFileSync(path.join(C, "allocation_result.json"), "utf-8"));
const paramsCache = JSON.parse(fs.readFileSync(path.join(C, "params_cache.json"), "utf-8"));

// --- 复刻 base_script.js 贪心段 (与源码同步的口径) ---
const P = paramsCache;
function toNum(v) { const n = parseFloat(v); return isNaN(n) ? null : n; }
function toText(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) return v.map(i => typeof i === "string" ? i : (i.text || i.name || "")).join(",").trim();
  if (typeof v === "object") return v.text || v.name || "";
  return String(v).trim();
}

const recById = {}, mains = [], subOf = {}, costByName = {}, noClearModels = new Set();
for (const r of rows) {
  const model = toText(r.model);
  const parentLinks = r["父记录"] || [];
  const isSub = Array.isArray(parentLinks) && parentLinks.length > 0;
  const clearFlag = toText(r["清库标记"]);
  if (model && clearFlag.includes("不清库")) noClearModels.add(model);
  const rowObj = {
    record_id: r.record_id, model,
    price: toNum(r["售价"]) || 0, fixed: toNum(r["固定成本合计"]) || 0,
    pp_first: toNum(r["采购价+头程"]) || 0, weight: toNum(r["部件重量 lbs"]) || 0,
    s30: toNum(r["过去30天销量"]) || 0,
  };
  recById[r.record_id] = rowObj;
  if (model && (!costByName[model] || rowObj.pp_first > 0)) costByName[model] = rowObj;
  if (isSub) { const pId = parentLinks[0].id; (subOf[pId] = subOf[pId] || []).push(rowObj); }
  else mains.push(rowObj);
}

// 组件池: components.json 批次 (Python 链路口径)
const pool = {};
for (const [name, c] of Object.entries(comps)) {
  pool[name] = c.batches.filter(b => b.qty > 0).map(b => ({ ...b }));
}

const scrapUnitByComp = {};
for (const compName of Object.keys(comps)) {
  if (noClearModels.has(compName)) { scrapUnitByComp[compName] = 0; continue; }
  const rec = costByName[compName] || {};
  const fee = rec.weight < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
  scrapUnitByComp[compName] = (rec.pp_first || 0) + fee;
}

const scopeByRid = {}, econByRid = {};
for (const m of mains) {
  const subs = subOf[m.record_id] || [];
  let scope = [];
  if (subs.length > 0) scope = Array.from(new Set(subs.map(s => s.model))).filter(Boolean);
  else if (m.model) scope = [m.model];
  const priceA = m.price * (1 - P.discount_a);
  const uA = priceA - m.fixed - priceA * P.promo_rate;
  const uB = (m.price * P.discount_b) - m.pp_first;
  const multA = Math.min(Math.max(1 + P.lambda * P.discount_a, P.m_min), P.m_max);
  const qA = Math.floor(m.s30 * (P.window_days / 30.0) * multA);
  scopeByRid[m.record_id] = scope;
  econByRid[m.record_id] = { uA, uB, qA };
}

const poolTotal = comp => (pool[comp] || []).reduce((acc, b) => acc + b.qty, 0);
const kitAvoidScrap = scope => scope.reduce((acc, n) => acc + (scrapUnitByComp[n] || 0), 0);
const ksByRid = {};
for (const m of mains) ksByRid[m.record_id] = 0;

let guard = 0;
while (true) {
  guard++;
  if (guard > 2000000) throw new Error("超限");
  let bestRid = null, bestV = -Infinity, bestRev = -Infinity, bestName = "";
  for (const m of mains) {
    const scope = scopeByRid[m.record_id];
    if (scope.length === 0) continue;
    if (scope.some(n => poolTotal(n) <= 0)) continue;
    const e = econByRid[m.record_id];
    const k = ksByRid[m.record_id];
    const pathV = (k < e.qA && e.uA > 0) ? e.uA : e.uB;
    const v = pathV + kitAvoidScrap(scope);
    const rev = m.s30 * m.price;
    if (v > bestV + 1e-9 || (Math.abs(v - bestV) <= 1e-9 && rev > bestRev + 1e-9)) {
      bestV = v; bestRid = m.record_id; bestRev = rev; bestName = m.model;
    }
  }
  if (bestRid === null || bestV <= 0) break;
  for (const comp of scopeByRid[bestRid]) {
    let need = 1;
    for (const b of pool[comp]) {
      if (need <= 0) break;
      if (b.qty <= 0) continue;
      const take = Math.min(b.qty, need);
      b.qty -= take; need -= take;
    }
  }
  ksByRid[bestRid] += 1;
}

// --- 对比 ---
const pyK = {};
for (const a of pyAlloc.allocations) pyK[a.model] = a.K;
let mismatch = 0, totalK = 0;
const jsK = {};
for (const m of mains) { jsK[m.model] = ksByRid[m.record_id]; totalK += ksByRid[m.record_id]; }
for (const [model, k] of Object.entries(jsK)) {
  if ((pyK[model] || 0) !== k) {
    mismatch++;
    console.log(`不一致: ${model} Python=${pyK[model] || 0} JS=${k}`);
  }
}
for (const [model, k] of Object.entries(pyK)) {
  if ((jsK[model] || 0) !== k && !(jsK[model] === undefined && k === 0)) {
    mismatch++;
    console.log(`不一致(py侧): ${model} Python=${k} JS=${jsK[model] || 0}`);
  }
}
console.log(`\nPython 总K = ${pyAlloc.allocations.reduce((a, x) => a + x.K, 0)}`);
console.log(`JS 总K = ${totalK}`);
console.log(mismatch === 0 ? "✅ 三链路口径一致 (Python vs JS 贪心分配逐model相同)" : `❌ ${mismatch} 个不一致`);
