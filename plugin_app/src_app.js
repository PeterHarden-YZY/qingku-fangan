import { bitable, FieldType } from '@lark-base-open/js-sdk';
import { tierRate, calcIncrementalStorageFee, getCurrentBDays } from './cost_calc.js';
import { getProfitItem, getItemCostAndDim, getModelCostAndCbm } from './cost_provider.js';

// 默认配置
const DEFAULTS = {
  scenario: "默认场景",
  window_days: 30,
  discount_a: 0.20,
  discount_b: 0.15,
  discount_c: 0.80,
  promo_rate: 0.30,
  lambda: 1.0,
  m_min: 1.0,
  m_max: 3.0,
  bonus_t1_cap: 20.0,
  bonus_r1: 0.010,
  bonus_t2_cap: 50.0,
  bonus_r2: 0.015,
  bonus_r3: 0.020,
  scrap_fee_light: 2.0,
  scrap_fee_heavy: 3.0,
  weight_threshold: 50.0,
  prov_age1: 120,
  prov_rate1: 0.30,
  prov_age2: 180,
  prov_rate2: 0.80,
  prov_rate3: 1.50
};

function log(msg, type = "info") {
  const box = document.getElementById("logBox");
  if (!box) return;
  const div = document.createElement("div");
  div.className = "log-entry log-" + type;
  const time = new Date().toLocaleTimeString();
  div.innerText = `[${time}] ${msg}`;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

window.clearLogs = function() {
  const box = document.getElementById("logBox");
  if (box) box.innerHTML = "";
};

function toNum(v) {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return v;
  if (Array.isArray(v)) {
    if (v.length === 0) return null;
    if (typeof v[0] === "number") return v[0];
    if (v[0]?.text) return parseFloat(v[0].text) || null;
  }
  const n = parseFloat(v);
  return isNaN(n) ? null : n;
}

function toText(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.trim();
  if (typeof v === "number") return String(v);
  if (Array.isArray(v)) {
    return v.map(item => typeof item === "string" ? item : (item.text || item.name || "")).join(",").trim();
  }
  if (typeof v === "object") return v.text || v.name || "";
  return String(v).trim();
}

// 字段规范化: SDK getRecords 返回 fields 以 fieldId 为键 (官方 IRecord.fields: {[fieldId]: IOpenCellValue}),
// 而业务代码统一用「字段名」读取。此处用 getFieldMetaList() 建 id->name 映射, 把每条记录转成 name 键。
// 2026-09-18 线上 K=0 根因修复: 此前直接用 r.fields["item"] 按名读取, 线上全部 undefined -> 库存池空 -> K=0
async function loadAllRecords(table, pageSize = 500) {
  const metas = await table.getFieldMetaList();
  const idToName = {};
  for (const m of metas) idToName[m.id] = m.name;
  const all = [];
  let hasMore = true, pageToken = undefined;
  while (hasMore) {
    const resp = await table.getRecords({ pageSize, pageToken });
    for (const r of resp.records) {
      const fields = {};
      for (const [fid, v] of Object.entries(r.fields || {})) {
        fields[idToName[fid] || fid] = v;
      }
      all.push({ recordId: r.recordId, fields });
    }
    hasMore = !!resp.hasMore;
    pageToken = resp.pageToken;
  }
  return all;
}

// 父记录字段解析: 真实 SDK 返回 IOpenLink { recordIds: string[] }, 兼容旧 raw 数组 [{id|record_id}]
function getParentId(f) {
  const pl = f["父记录"];
  if (!pl) return null;
  if (Array.isArray(pl)) {
    if (pl.length === 0) return null;
    return pl[0].record_id || pl[0].id || null;
  }
  if (pl.recordIds && Array.isArray(pl.recordIds) && pl.recordIds.length > 0) {
    return pl.recordIds[0];
  }
  return null;
}

function makeBonus(P) {
  const t1 = P.bonus_t1_cap * 10000;
  const t2 = P.bonus_t2_cap * 10000;
  const r1 = P.bonus_r1, r2 = P.bonus_r2, r3 = P.bonus_r3;
  return function(revenue) {
    if (revenue <= t1) return revenue * r1;
    if (revenue <= t2) return (revenue - t1) * r2 + t1 * r1;
    return (revenue - t2) * r3 + (t2 - t1) * r2 + t1 * r1;
  };
}

// 计提比例函数 (第五轮: 持有期计提增量): 与 Python make_provision_rate 口径一致
function makeProvisionRate(P) {
  const a1 = P.prov_age1, a2 = P.prov_age2;
  const r1 = P.prov_rate1, r2 = P.prov_rate2, r3 = P.prov_rate3;
  return function(age) {
    if (age <= a1) return 0.0;
    if (age <= a2) return r1;
    if (age <= 270) return r2;
    return r3;
  };
}

window.startExecution = async function() {
  const btn = document.getElementById("runBtn");
  const borrowSelect = document.getElementById("borrowMode");
  const allowBorrow = !borrowSelect || borrowSelect.value === "borrow";

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = "<span>⏳ 正在测算并写回中...</span>";
  }

  try {
    log(`启动测算模式: 【${allowBorrow ? "允许跨渠道借调凑齐套" : "严格仅限独立站自存齐套"}】`, "info");
    if (!bitable || !bitable.base) {
      throw new Error("未能连接 bitable 实例，请确保在多维表格自定义插件侧边栏中运行。");
    }

    const tableList = await bitable.base.getTableList();
    let mainTable = null, stockTable = null, paramsTable = null, logTable = null;

    for (const t of tableList) {
      const name = await t.getName();
      if (name.includes("清库方案") || name.includes("成本核算")) mainTable = t;
      else if (name === "库存表") stockTable = t;
      else if (name.includes("参数配置")) paramsTable = t;
      else if (name.includes("运行日志") || name.includes("测算日志")) logTable = t;
    }

    if (!mainTable) throw new Error("未找到《清库方案成本核算》表");
    if (!stockTable) throw new Error("未找到《库存表》");
    log("数据表映射成功！", "success");

    // 1. 读取参数表
    let P = { ...DEFAULTS };
    if (paramsTable) {
      log("正在读取《清库参数配置》表...", "info");
      const pRecords = await loadAllRecords(paramsTable, 50);
      for (const r of pRecords) {
        const enableField = toText(r.fields["是否启用"]);
        if (enableField.includes("启用") || enableField === "是" || enableField === "true") {
          P.scenario = toText(r.fields["场景名"]) || P.scenario;
          P.window_days = toNum(r.fields["销量预测窗口(天)"]) ?? P.window_days;
          P.discount_a = toNum(r.fields["A降价幅度"]) ?? P.discount_a;
          P.discount_b = toNum(r.fields["B线下折扣"]) ?? P.discount_b;
          P.discount_c = toNum(r.fields["C促销售价折扣"]) ?? P.discount_c;
          P.promo_rate = toNum(r.fields["A推广费率"]) ?? P.promo_rate;
          P.lambda = toNum(r.fields["销量放大系数λ"]) ?? P.lambda;
          P.m_min = toNum(r.fields["放大系数下限"]) ?? P.m_min;
          P.m_max = toNum(r.fields["放大系数上限"]) ?? P.m_max;
          P.bonus_t1_cap = toNum(r.fields["奖金档1上限(万USD)"]) ?? P.bonus_t1_cap;
          P.bonus_r1 = toNum(r.fields["奖金档1比例"]) ?? P.bonus_r1;
          P.bonus_t2_cap = toNum(r.fields["奖金档2上限(万USD)"]) ?? P.bonus_t2_cap;
          P.bonus_r2 = toNum(r.fields["奖金档2比例"]) ?? P.bonus_r2;
          P.bonus_r3 = toNum(r.fields["奖金档3比例"]) ?? P.bonus_r3;
          P.scrap_fee_light = toNum(r.fields["报废费-轻(USD/件)"]) ?? P.scrap_fee_light;
          P.scrap_fee_heavy = toNum(r.fields["报废费-重(USD/件)"]) ?? P.scrap_fee_heavy;
          P.weight_threshold = toNum(r.fields["重量分界(lbs)"]) ?? P.weight_threshold;
          P.prov_age1 = toNum(r.fields["计提库龄分界1(天)"]) ?? P.prov_age1;
          P.prov_rate1 = toNum(r.fields["计提比例1(120-180天)"]) ?? P.prov_rate1;
          P.prov_age2 = toNum(r.fields["计提库龄分界2(天)"]) ?? P.prov_age2;
          P.prov_rate2 = toNum(r.fields["计提比例2(181-270天)"]) ?? P.prov_rate2;
          P.prov_rate3 = toNum(r.fields["计提比例3(>270天)"]) ?? P.prov_rate3;
          log(`已加载激活场景参数: 【${P.scenario}】(预测窗口:${P.window_days}天)`, "success");
          break;
        }
      }
    }

    // 2. 读取库存全量批次
    log("正在拉取《库存表》多批次数据...", "info");
    const stockRows = await loadAllRecords(stockTable, 500);
    log(`成功拉取 ${stockRows.length} 条库存记录`, "success");

    // 统一物理批次池: 海外仓每行 = 一份物理批次, is_dtc 标记归属团队
    // 2026-09-18 架构修正: 在途/工厂输入字段挂在《清库方案成本核算》主表
    //   (BOM 款填子记录行, 单件款填主记录行), 库存表回归纯数据源
    const owmBatchesByItem = {};
    const transitByModel = {};   // model 名 -> [{qty, eta}] (主表行填写)
    const factoryByModel = {};   // model 名 -> qty (主表行填写)
    let transitInPool = 0, transitExcluded = 0;

    for (const r of stockRows) {
      const f = r.fields;
      const team = toText(f["所属团队"]);
      const whType = toText(f["仓库类型"]);
      const itemCode = toText(f["item"]);
      const qty = toNum(f["可用量"]) || 0;
      const age = toNum(f["库龄(程序拉取)"]) || 0;

      if (whType === "海外仓" && qty > 0 && itemCode) {
        if (!owmBatchesByItem[itemCode]) owmBatchesByItem[itemCode] = [];
        owmBatchesByItem[itemCode].push({ qty, age, is_dtc: team === "独立站" });
      }
    }
    if (transitInPool > 0 || transitExcluded > 0) {
      log(`🚚 在途库存: 入池(库龄0) ${transitInPool} 件 | 到货晚于${P.window_days}天窗口排除 ${transitExcluded} 件`, "info");
    }

    // 工厂未发货库存 (2026-09-18 迁移: 从主表行读「工厂未发货数量」, model 维度)
    // (在途输入同样在主表行, 在读取主表后入池 — 见下方)

    // 3. 读取主表全量并建立 BOM
    log("正在读取《清库方案成本核算》主表...", "info");
    const mainRows = await loadAllRecords(mainTable, 500);

    const recById = {};
    const mains = [];
    const subOf = {};
    const compModelToItems = {};
    const costByName = {};
    const noClearModels = new Set();

    for (const r of mainRows) {
      const rid = r.recordId;
      const f = r.fields;
      const model = toText(f["model"]);
      // 手动补充item 兜底: item lookup 数据源覆盖不全时, 用人工填写值补位 (与 Python fetch_inventory.py 一致)
      const itemCode = toText(f["item"]) || toText(f["手动补充item"]).trim();
      const parentId = getParentId(f);
      const isSub = !!parentId;

      // 单选字段「清库标记」(唯一选项: 不清库): 选中 → 报废核算排除 (留空 = 正常清库)
      const clearFlag = toText(f["清库标记"]);
      if (model && clearFlag.includes("不清库")) noClearModels.add(model);

      // 2026-09-18 迁移: 在途/工厂输入挂在主表行 (BOM款=子记录行, 单件款=主记录行)
      if (model) {
        const tq = toNum(f["在途库存"]);
        if (tq !== null && tq > 0) {
          if (!transitByModel[model]) transitByModel[model] = [];
          transitByModel[model].push({ qty: tq, eta: toNum(f["在途到货时间(天)"]) });
        }
        const fq = toNum(f["工厂未发货数量"]) || 0;
        if (fq > 0) factoryByModel[model] = (factoryByModel[model] || 0) + fq;
      }

      const rowObj = {
        record_id: rid,
        model,
        item: itemCode,
        is_sub: isSub,
        parent_id: parentId,
        price: toNum(f["售价"]) || 0,
        fixed: toNum(f["固定成本合计"]) || 0,
        pp_first: toNum(f["采购价+头程"]) || 0,
        buy_price: toNum(f["采购价"]) || 0,
        weight: toNum(f["部件重量 lbs"]) || 0,
        s30: toNum(f["过去30天销量"]) || 0,
        raw_fields: f
      };
      recById[rid] = rowObj;

      if (model) {
        if (!compModelToItems[model]) compModelToItems[model] = new Set();
        if (itemCode) compModelToItems[model].add(itemCode);
        if (!costByName[model] || rowObj.pp_first > 0) costByName[model] = rowObj;
      }

      if (isSub) {
        const pId = rowObj.parent_id;
        if (!subOf[pId]) subOf[pId] = [];
        subOf[pId].push(rowObj);
      } else {
        mains.push(rowObj);
      }
    }

    // 构建组件虚拟库存池: 单一物理池 (海外仓全量批次, 每批次带 is_dtc 归属标记)
    // 在途批次 (2026-09-18 迁移: 输入来自主表行, 库龄0, 到货时间<=窗口才入池)
    const compPool = {};

    for (const [modelName, itemSet] of Object.entries(compModelToItems)) {
      let batches = [];
      for (const it of itemSet) {
        if (owmBatchesByItem[it]) batches.push(...owmBatchesByItem[it].map(b => ({ ...b })));
      }
      for (const t of (transitByModel[modelName] || [])) {
        if (t.eta !== null && t.eta >= 0 && t.eta <= P.window_days) {
          batches.push({ qty: t.qty, age: 0, is_dtc: true });
          transitInPool += t.qty;
        } else {
          transitExcluded += t.qty;
        }
      }
      batches.sort((a, b) => b.age - a.age);
      compPool[modelName] = batches;
    }
    if (transitInPool > 0 || transitExcluded > 0) {
      log(`🚚 在途库存(主表行): 入池(库龄0) ${transitInPool} 件 | 到货晚于${P.window_days}天窗口/未填排除 ${transitExcluded} 件`, "info");
    }
    {
      const fTotal = Object.values(factoryByModel).reduce((a, b) => a + b, 0);
      if (fTotal > 0) {
        log(`🏭 工厂未发货库存(主表行)合计 ${fTotal} 件, 覆盖 ${Object.keys(factoryByModel).length} 个 model`, "info");
      }
    }

    // 4. 阶段一: 营收排序 (仅作展示排名; 分配先后由边际价值贪心决定)
    mains.sort((a, b) => (b.s30 * b.price) - (a.s30 * a.price) || (a.model || "").localeCompare(b.model || ""));

    // 5. 阶段二: 边际价值贪心分配 (细化成本 + 仓储费)
    const bDays = getCurrentBDays();
    const pool = {};
    for (const [k, bList] of Object.entries(compPool)) pool[k] = bList.map(b => ({ ...b }));

    // 组件单件报废成本 (不清库 = 0)
    const scrapUnitByComp = {};
    for (const compName of Object.keys(compPool)) {
      if (noClearModels.has(compName)) { scrapUnitByComp[compName] = 0; continue; }
      const rec = costByName[compName] || {};
      const itemInfo = getItemCostAndDim(rec.item, rec);
      const buyHead = (itemInfo.buy + itemInfo.head) > 0 ? (itemInfo.buy + itemInfo.head) : (rec.pp_first || 0);
      const fee = itemInfo.weight_lbs < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
      scrapUnitByComp[compName] = buyHead + fee;
    }

    // 主model scope 与经济参数 (细化成本计算)
    const scopeByRid = {};
    const econByRid = {};
    for (const m of mains) {
      const subs = subOf[m.record_id] || [];
      let scope = [];
      let isBom = false;
      if (subs.length > 0) {
        isBom = true;
        scope = Array.from(new Set(subs.map(s => s.model))).filter(Boolean);
      } else if (m.model) {
        scope = [m.model];
      }

      const costInfo = getModelCostAndCbm(m, subs, recById);
      const buy = costInfo.buy;
      const head = costInfo.head;
      const tariff = costInfo.tariff;
      const last = costInfo.last;
      const cbm = costInfo.cbm;

      const storageA = calcIncrementalStorageFee(100, P.window_days, cbm);
      const storageB = calcIncrementalStorageFee(100, bDays, cbm);

      const costATotal = buy + head + tariff + last + storageA;
      const costBTotal = buy + head + tariff + storageB;

      const priceA = m.price * (1 - P.discount_a);
      const uA = priceA - costATotal - priceA * P.promo_rate;
      const uB = (m.price * P.discount_b) - costBTotal;

      const multA = Math.min(Math.max(1 + P.lambda * P.discount_a, P.m_min), P.m_max);
      const qA = Math.floor(m.s30 * (P.window_days / 30.0) * multA);
      scopeByRid[m.record_id] = { scope, isBom };
      econByRid[m.record_id] = { uA, uB, qA, buy, head, tariff, last, cbm };
    }

    const poolDtc = (comp) => (pool[comp] || []).reduce((acc, b) => acc + (b.is_dtc ? b.qty : 0), 0);
    const poolTotal = (comp) => (pool[comp] || []).reduce((acc, b) => acc + b.qty, 0);
    const kitAvoidScrap = (scope) => scope.reduce((acc, n) => acc + (scrapUnitByComp[n] || 0), 0);

    const ksByRid = {};
    const consumedByRid = {};
    for (const m of mains) { ksByRid[m.record_id] = 0; consumedByRid[m.record_id] = {}; }

    let guard = 0;
    while (true) {
      guard++;
      if (guard > 2000000) throw new Error("贪心循环超限");
      let bestRid = null, bestV = -Infinity;
      for (const m of mains) {
        const { scope } = scopeByRid[m.record_id];
        if (scope.length === 0) continue;
        if (allowBorrow) {
          // borrow 门槛: 每组件池总量>0 且 整套至少1个组件有独立站批次
          if (scope.some(n => poolTotal(n) <= 0)) continue;
          if (scope.every(n => poolDtc(n) <= 0)) continue;
        } else if (scope.some(n => poolDtc(n) <= 0)) {
          // 严格模式门槛 (2026-09-18 修复): 每个组件都必须有独立站批次
          // 旧门控用池总量判断 → 非独立站库存使门槛通过但扣减取不到货 → 凭空计套
          continue;
        }
        const e = econByRid[m.record_id];
        const k = ksByRid[m.record_id];
        const pathV = (k < e.qA && e.uA > 0) ? e.uA : e.uB;
        const v = pathV + kitAvoidScrap(scope);
        if (v > bestV) { bestV = v; bestRid = m.record_id; }
      }
      if (bestRid === null || bestV <= 0) break;
      const { scope } = scopeByRid[bestRid];
      for (const comp of scope) {
        if (!consumedByRid[bestRid][comp]) consumedByRid[bestRid][comp] = [];
        let need = 1;
        const bList = pool[comp] || [];
        for (const b of bList) {
          if (need <= 0) break;
          if (b.qty <= 0 || !b.is_dtc) continue;
          const take = Math.min(b.qty, need);
          b.qty -= take; need -= take;
          consumedByRid[bestRid][comp].push({ qty: take, age: b.age, is_dtc: true });
        }
        if (allowBorrow && need > 0) {
          for (const b of bList) {
            if (need <= 0) break;
            if (b.qty <= 0 || b.is_dtc) continue;
            const take = Math.min(b.qty, need);
            b.qty -= take; need -= take;
            consumedByRid[bestRid][comp].push({ qty: take, age: b.age, is_dtc: false });
          }
        }
      }
      ksByRid[bestRid] += 1;
    }

    const allocations = [];
    for (const m of mains) {
      const { scope, isBom } = scopeByRid[m.record_id];
      allocations.push({
        record_id: m.record_id,
        model: m.model,
        type: isBom ? "bom" : "single",
        K: ksByRid[m.record_id],
        scope,
        consumed: consumedByRid[m.record_id]
      });
    }

    // 稀缺组件取舍明细
    const needMap = {};
    for (const m of mains) {
      for (const n of scopeByRid[m.record_id].scope) {
        if (!needMap[n]) needMap[n] = [];
        needMap[n].push(m);
      }
    }
    const scarcityDecisions = [];
    for (const [compName, users] of Object.entries(needMap)) {
      if (users.length < 2) continue;
      const stock = compPool[compName] ? compPool[compName].reduce((acc, b) => acc + b.qty, 0) : 0;
      if (stock <= 0) continue;
      const winners = {}, starved = [];
      let got = 0;
      for (const u of users) {
        const k = ksByRid[u.record_id] || 0;
        got += k;
        if (k > 0) winners[u.model] = k;
        else starved.push(u.model);
      }
      if (got < stock || starved.length === 0) continue;
      scarcityDecisions.push({ component: compName, stock, winners, starved });
    }
    if (scarcityDecisions.length > 0) {
      log(`⚖️ 稀缺组件取舍 ${scarcityDecisions.length} 个:`, "info");
      for (const d of scarcityDecisions) {
        const w = Object.entries(d.winners).map(([mn, k]) => `${mn}(${k}套)`).join(", ");
        log(`&nbsp;&nbsp;${d.component}: ${w} | 让位: ${d.starved.join(", ")}`, "info");
      }
    }

    // 收集独立站长版死库存
    const longboards = [];
    for (const [comp, bList] of Object.entries(pool)) {
      const rem = bList.reduce((acc, b) => acc + (b.qty > 0 && b.is_dtc ? b.qty : 0), 0);
      if (rem > 0) {
        longboards.push({ model: comp, remaining: rem });
      }
    }

    // 阶段三: 长版强锁报废核算 (清库标记=不清库 的组件排除)
    let totalScrapCost = 0;
    const scrapMapByModel = {};
    const excludedNoClear = [];
    const anchorRowId = {};
    for (const lb of longboards) {
      if (noClearModels.has(lb.model)) {
        excludedNoClear.push({ model: lb.model, qty: lb.remaining });
        continue;
      }
      const rec = costByName[lb.model] || {};
      const itemInfo = getItemCostAndDim(rec.item, rec);
      const buyHead = (itemInfo.buy + itemInfo.head) > 0 ? (itemInfo.buy + itemInfo.head) : (rec.pp_first || 0);
      const fee = itemInfo.weight_lbs < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
      const unitCost = buyHead + fee;
      const cost = unitCost * lb.remaining;
      totalScrapCost += cost;
      scrapMapByModel[lb.model] = { qty: lb.remaining, cost };

      const subRow = mainRows.find(r => {
        return getParentId(r.fields) && toText(r.fields["model"]) === lb.model;
      });
      const anyRow = mainRows.find(r => toText(r.fields["model"]) === lb.model);
      anchorRowId[lb.model] = subRow ? subRow.recordId : (anyRow ? anyRow.recordId : null);
    }
    if (excludedNoClear.length > 0) {
      log(`「清库标记=不清库」排除报废 ${excludedNoClear.length} 个组件: ` +
          excludedNoClear.map(e => `${e.model}(${e.qty}件)`).join("、"), "warn");
    }

    // 6. 阶段四: 4 选项闭环动态损失最小化测算 (精确批次仓储费)
    const calcBonus = makeBonus(P);
    let totalK = 0;
    let totalBestNet = 0;
    let factoryShipTotal = 0;
    const updates = [];
    const windowStats = [];

    for (const a of allocations) {
      const m = recById[a.record_id];
      if (a.K <= 0) {
        updates.push({
          recordId: m.record_id,
          fields: {
            "实际齐套数": 0,
            "最佳方案": "",
            "预计回收资金": 0,
            "仓储费": 0,
            "最优窗口期(天)": null,
            "最优窗口方案": ""
          }
        });
        continue;
      }

      totalK += a.K;

      const price = m.price;
      const s30 = m.s30;
      const subs = subOf[m.record_id] || [];
      const costInfo = getModelCostAndCbm(m, subs, recById);
      const buy = costInfo.buy;
      const head = costInfo.head;
      const tariff = costInfo.tariff;
      const last = costInfo.last;

      // 根据 a.consumed 计算批次精确单台增量仓储费函数
      const calcConsumedStorageFee = (deltaDays) => {
        if (!a.consumed || a.K <= 0 || deltaDays <= 0) return 0.0;
        let totalFee = 0.0;
        for (const [compName, batches] of Object.entries(a.consumed)) {
          const recC = costByName[compName] || {};
          const infoC = getItemCostAndDim(recC.item, recC);
          const cbmC = infoC.cbm;
          for (const b of batches) {
            const fee = calcIncrementalStorageFee(b.age, deltaDays, cbmC) * b.qty;
            totalFee += fee;
          }
        }
        return totalFee / a.K;
      };

      const storageFeeADefault = calcConsumedStorageFee(P.window_days);
      const storageFeeB = calcConsumedStorageFee(bDays);
      const storageFeeC = calcConsumedStorageFee(bDays);

      // 持有期计提增量 (第五轮): 窗口 W 内批次老化 -> 计提档位上移的边际成本 (与 Python calc_hold_provision_delta 口径一致)
      // Δprov(W) = Σ组件 Σ批次 [qty × buy_i × (rate(库龄+W/2) − rate(库龄))] / K  (按窗口中点库龄计档)
      const provisionRateFn = makeProvisionRate(P);
      const calcHoldProvisionDelta = (W) => {
        if (W <= 0 || a.K <= 0 || !a.consumed) return 0.0;
        let total = 0.0;
        for (const [compName, batches] of Object.entries(a.consumed)) {
          const recC = costByName[compName] || {};
          const buyC = recC.buy_price || 0;
          if (buyC <= 0) continue;
          for (const b of batches) {
            const midAge = b.age + W / 2.0;
            total += b.qty * buyC * (provisionRateFn(midAge) - provisionRateFn(b.age));
          }
        }
        return total / a.K;
      };

      const priceA = price * (1 - P.discount_a);
      const uA = priceA - (buy + head + tariff + last + storageFeeADefault) - priceA * P.promo_rate;
      const uB = price * P.discount_b - (buy + head + tariff + storageFeeB);
      const revCUnit = price * P.discount_c;

      // 4 选项评估封装为窗口 W 的函数 (自定义窗口测算 + 最优窗口搜索复用)
      const evalOptions = (W) => {
        const curStorageA = calcConsumedStorageFee(W);
        const holdProvDelta = calcHoldProvisionDelta(W); // 持有期计提增量(第五轮)
        const curUA = priceA - (buy + head + tariff + last + curStorageA + holdProvDelta) - priceA * P.promo_rate;

        const multA = Math.max(P.m_min, Math.min(P.m_max, 1.0 + P.lambda * P.discount_a));
        const qA = Math.floor(s30 * (W / 30.0) * multA);
        const multC = Math.max(P.m_min, Math.min(P.m_max, 1.0 + P.lambda * (1.0 - P.discount_c)));
        const qCCap = Math.floor(s30 * (W / 30.0) * multC);

        // 单方案全量
        const netBOnly = a.K * uB;
        const kCFullCand = Math.min(a.K, qCCap);
        const remFullC = a.K - kCFullCand;
        const bonusCFull = kCFullCand > 0 ? calcBonus(kCFullCand * revCUnit) : 0;
        const netCOnly = -bonusCFull - (kCFullCand * storageFeeC) + remFullC * uB;

        // 组合策略
        const kACand = curUA > 0 ? Math.min(a.K, qA) : 0;
        const rem = a.K - kACand;
        const netRemB = rem * uB;
        const kCCand = Math.min(rem, qCCap);
        const bonusCCand = kCCand > 0 ? calcBonus(kCCand * revCUnit) : 0;
        const remAfterC = rem - kCCand;
        const netRemC = -bonusCCand - (kCCand * storageFeeC) + remAfterC * uB;

        const options = [
          { name: "A促销+剩余走B出清", net: kACand * curUA + netRemB, ka: kACand, kc: 0, kb: rem, cur_storage_a: curStorageA },
          { name: "A促销+剩余走C激励", net: kACand * curUA + netRemC, ka: kACand, kc: kCCand, kb: remAfterC, cur_storage_a: curStorageA },
          { name: "全量B线下出清", net: netBOnly, ka: 0, kc: 0, kb: a.K, cur_storage_a: curStorageA },
          { name: "全量C激励出清", net: netCOnly, ka: 0, kc: kCFullCand, kb: remFullC, cur_storage_a: curStorageA }
        ];
        options.sort((x, y) => y.net - x.net);
        return options[0];
      };

      const best = evalOptions(P.window_days);

      // 最优窗口期逐日扫描
      let bestWindow = null;
      {
        let wMax = -Infinity;
        const nets = [];
        for (let W = 1; W <= 730; W++) {
          const n = evalOptions(W).net;
          nets.push(n);
          if (n > wMax) wMax = n;
        }
        const wMin = Math.min(...nets);
        if (wMax - wMin > 0.005) {
          bestWindow = nets.findIndex(n => n >= wMax - 0.005) + 1;
        }
        windowStats.push(bestWindow);
      }


      const kA = best.ka;
      const kC = best.kc;
      const kB = best.kb;
      const finalNet = best.net;

      totalBestNet += finalNet;

      let bestPlanDesc = "";
      if (kA === a.K) {
        bestPlanDesc = "A线上降价出清(100%覆盖)";
      } else {
        const segs = [];
        if (kA > 0) segs.push(`A促销(${kA}套)`);
        if (kC > 0) segs.push(`C激励(${kC}套)`);
        if (kB > 0) segs.push(`B出清(${kB}套)`);
        bestPlanDesc = segs.length > 0 ? segs.join("+") : `无动作(ΔK=0)`;
      }

      // 最优窗口 W* 下的方案说明 (与 Python scenarios.py 口径一致):
      // 同一 model 在当前窗口与最优窗口下方案构成可能不同 —— 窗口越长 q_a 越大 (A 覆盖越多),
      // 但仓储费+持有计提同步增长侵蚀 u_a; W* == 当前窗口时方案一致, 标注 "(即当前窗口)"
      let bestWindowPlan = "";
      if (bestWindow !== null) {
        const wOpt = evalOptions(bestWindow);
        if (wOpt.ka === a.K) {
          bestWindowPlan = "A线上降价出清(100%覆盖)";
        } else {
          const segs = [];
          if (wOpt.ka > 0) segs.push(`A促销(${wOpt.ka}套)`);
          if (wOpt.kc > 0) segs.push(`C激励(${wOpt.kc}套)`);
          if (wOpt.kb > 0) segs.push(`B出清(${wOpt.kb}套)`);
          bestWindowPlan = segs.length > 0 ? segs.join("+") : `无动作(ΔK=0)`;
        }
        if (bestWindowPlan === bestPlanDesc) bestWindowPlan += "(即当前窗口)";
      }

      // 计算有效单件仓储费 (加权)
      let effectiveStorageFee = 0.0;
      if (a.K > 0) {
        const curStorageA = best.cur_storage_a || storageFeeADefault;
        effectiveStorageFee = (kA * curStorageA + kB * storageFeeB + kC * storageFeeC) / a.K;
      }

      // ---- 4.8 工厂未发货库存发货决策 (第四轮) ----
      // 候选 X ∈ {0, ⌊F_min/2⌋, F_min}; V(X) = X×u_ship − Σ(F_i−X)×buy_i
      // 发货的按最优路径单套净回收测算; 不发货的亏采购价; 与 Python scenarios.py 口径一致
      let facShipQty = 0, facDecision = "", facLossNotShip = null;
      {
        const scopeNames = (subOf[m.record_id] || []).length > 0
          ? Array.from(new Set((subOf[m.record_id] || []).map(s => s.model))).filter(Boolean)
          : (m.model ? [m.model] : []);
        const itemOf = {};
        for (const s of (subOf[m.record_id] || [])) itemOf[s.model] = s.item || "";
        if ((subOf[m.record_id] || []).length === 0) itemOf[m.model] = m.item || "";

        const facQtyByComp = {};
        for (const compName of scopeNames) {
          if (compName && (factoryByModel[compName] || 0) > 0) facQtyByComp[compName] = factoryByModel[compName];
        }

        if (Object.keys(facQtyByComp).length > 0) {
          const fMin = Math.min(...Object.values(facQtyByComp));
          const isBom = (subOf[m.record_id] || []).length > 0;
          if (isBom && Object.keys(facQtyByComp).length < scopeNames.length) {
            // BOM 有子件未填 -> 无法齐套, 跳过决策但提示
            const missing = scopeNames.filter(n => !(n in facQtyByComp));
            facDecision = `⚠️ 子件未填工厂未发货量: ${missing.join(",")}`;
          } else {
            let shipBuy = 0, shipHead = 0, shipTariff = 0;
            for (const n of Object.keys(facQtyByComp)) {
              const info = getItemCostAndDim(itemOf[n] || "", costByName[n] || {});
              shipBuy += info.buy; shipHead += info.head; shipTariff += info.tariff;
            }
            const cbmMain = getModelCostAndCbm(m, subOf[m.record_id] || [], recById).cbm;
            const storageFacB = calcIncrementalStorageFee(0, bDays, cbmMain);
            // 按生效最优路径确定发货单套净回收 (与 Python 口径一致)
            let uShip;
            if (best.name === "全量C激励出清") {
              const revC = price * P.discount_c;
              const bonusRate = revC > 0 ? calcBonus(revC) / revC : 0;
              uShip = revC - (shipBuy + shipHead + shipTariff) - storageFacB - revC * bonusRate;
            } else {
              const isAPath = best.name.includes("A促销");
              const pricePath = isAPath ? priceA : price * P.discount_b;
              const promo = isAPath ? priceA * P.promo_rate : 0;
              uShip = pricePath - promo - (shipBuy + shipHead + shipTariff) - storageFacB;
            }
            const buyOf = {};
            for (const n of Object.keys(facQtyByComp)) {
              buyOf[n] = getItemCostAndDim(itemOf[n] || "", costByName[n] || {}).buy;
            }
            const lossNotShip = (X) => Object.entries(facQtyByComp).reduce(
              (acc, [n, fq]) => acc + (fq - X) * buyOf[n], 0);
            const cands = [...new Set([0, Math.floor(fMin / 2), Math.floor(fMin)])].sort((x, y) => x - y);
            let bestX = 0, bestV = -Infinity;
            for (const X of cands) {
              const V = X * uShip - lossNotShip(X);
              if (V > bestV + 1e-9) { bestV = V; bestX = X; }
            }
            facShipQty = bestX;
            facLossNotShip = lossNotShip(bestX);
            if (bestX === 0) facDecision = `不发货(亏采购价$${Math.round(lossNotShip(0)).toLocaleString()})`;
            else if (bestX === Math.floor(fMin)) facDecision = `整单全发(${Math.floor(fMin)}套)`;
            else facDecision = `半发(${bestX}套)`;
            factoryShipTotal += bestX;
          }
        }
      }

      const scrapInfo = anchorRowId[m.model] === m.record_id ? scrapMapByModel[m.model] : null;
      updates.push({
        recordId: m.record_id,
        fields: {
          "实际齐套数": a.K,
          "最佳方案": bestPlanDesc,
          "预计回收资金": Math.round(finalNet * 100) / 100,
          "仓储费": Math.round(effectiveStorageFee * 100) / 100,
          "最优窗口期(天)": bestWindow,
          "最优窗口方案": bestWindowPlan,
          "报废数量": scrapInfo ? scrapInfo.qty : null,
          "报废成本": scrapInfo ? Math.round(scrapInfo.cost * 100) / 100 : null,
          "工厂发货套数": facShipQty,
          "工厂发货决策": facDecision,
          "工厂不发货亏损": facLossNotShip !== null ? Math.round(facLossNotShip * 100) / 100 : null
        }
      });
    }

    // 7. 报废写回: 单行挂载
    for (const r of mainRows) {
      const f = r.fields;
      const model = toText(f["model"]);
      const isSub = !!getParentId(f);
      const scrapInfo = anchorRowId[model] === r.recordId ? scrapMapByModel[model] : null;
      const fields = {
        "报废数量": scrapInfo ? scrapInfo.qty : null,
        "报废成本": scrapInfo ? Math.round(scrapInfo.cost * 100) / 100 : null
      };
      if (isSub) {
        Object.assign(fields, { "实际齐套数": null, "最佳方案": "", "预计回收资金": null, "仓储费": null, "最优窗口期(天)": null, "最优窗口方案": "", "工厂发货套数": null, "工厂发货决策": "", "工厂不发货亏损": null });
      }
      updates.push({ recordId: r.recordId, fields });
    }

    // 8. 批量写回多维表格
    log(`正在批量写回 ${updates.length} 条记录到《清库方案成本核算》表...`, "info");
    let globalBestWindow = null;
    {
      const cnt = {};
      for (const w of windowStats) {
        if (w === null || w === undefined) continue;
        cnt[w] = (cnt[w] || 0) + 1;
      }
      const entries = Object.entries(cnt).sort((a, b) => b[1] - a[1]);
      if (entries.length > 0) globalBestWindow = Number(entries[0][0]);
      log(`📈 全局最优窗口期: ${globalBestWindow ? globalBestWindow + " 天 (各主model最优窗口的众数)" : "无 (所有model净额对窗口不敏感)"}`, "info");
    }

    const mFieldMetas = await mainTable.getFieldMetaList();
    const fieldMap = {};
    for (const f of mFieldMetas) fieldMap[f.name] = f.id;

    const neededFields = ["实际齐套数", "最佳方案", "预计回收资金", "仓储费", "报废数量", "报废成本", "最优窗口期(天)", "最优窗口方案", "工厂发货套数", "工厂发货决策", "工厂不发货亏损"];
    for (const fName of neededFields) {
      if (!fieldMap[fName]) {
        const fType = (fName === "最佳方案" || fName === "工厂发货决策" || fName === "最优窗口方案") ? FieldType.Text : FieldType.Number;
        const newF = await mainTable.addField({ name: fName, type: fType });
        fieldMap[fName] = newF.id;
      }
    }

    const payload = updates.map(u => {
      const mappedFields = {};
      for (const [k, v] of Object.entries(u.fields)) {
        if (fieldMap[k]) mappedFields[fieldMap[k]] = v;
      }
      return { recordId: u.recordId, fields: mappedFields };
    });

    for (let i = 0; i < payload.length; i += 500) {
      await mainTable.setRecords(payload.slice(i, i + 500));
    }

    // 9. 写入日志表
    if (logTable) {
      try {
        const logFieldList = await logTable.getFieldMetaList();
        const lNameToId = Object.fromEntries(logFieldList.map(f => [f.name, f.id]));
        // 2026-09-18 修复: 字段名兼容映射 (线上表是 run_log.py 建的「运行时间/场景名/关键参数」,
        // 旧代码写「运行时间戳/测算场景/关键参数快照」→ logFields 全空 → addRecord 建出空行, 排障被致盲)
        const pick = (...names) => { for (const n of names) if (lNameToId[n]) return lNameToId[n]; return null; };
        const logFields = {};
        const fTime = pick("运行时间", "运行时间戳");
        if (fTime) logFields[fTime] = new Date().toLocaleString();
        const fScen = pick("场景名", "测算场景");
        if (fScen) logFields[fScen] = `${P.scenario} (${allowBorrow ? "允许借调" : "纯独立站"})`;
        const fParam = pick("关键参数", "关键参数快照");
        if (fParam) logFields[fParam] = `窗口:${P.window_days}天；借调:${allowBorrow ? "是" : "否"}；齐套K:${totalK}；回收:$${Math.round(totalBestNet)}；长版报废:$${Math.round(totalScrapCost)}；全局最优窗口:${globalBestWindow ? globalBestWindow + "天" : "-"}`;
        const fK = pick("总齐套K");
        if (fK) logFields[fK] = totalK;
        const fWindow = pick("全局最优窗口(天)");
        if (fWindow) logFields[fWindow] = globalBestWindow;
        await logTable.addRecord({ fields: logFields });
      } catch (e) {
        console.warn("写入日志表失败:", e);
      }
    }

    log("🎉 全部测算与写回完成！" + (factoryShipTotal > 0 ? `（工厂建议发货 ${factoryShipTotal} 套）` : ""), "success");

    document.getElementById("statK").innerText = totalK.toLocaleString() + " 套";
    document.getElementById("statScrap").innerText = "$" + Math.round(totalScrapCost).toLocaleString();
    document.getElementById("statBestNet").innerText = "$" + Math.round(totalBestNet).toLocaleString();
    document.getElementById("statModels").innerText = allocations.filter(a => a.K > 0).length + " 款";
    document.getElementById("resultCard").style.display = "block";

  } catch (err) {
    log("执行出错: " + err.message, "error");
    console.error(err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = "<span>🚀 开始一键全自动测算</span>";
    }
  }
};
