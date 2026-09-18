import { bitable } from '@lark-base-open/js-sdk';

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
      const pRecords = await paramsTable.getRecords({ pageSize: 50 });
      for (const r of pRecords.records) {
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
    let stockRows = [];
    let hasMoreStock = true, stockPageToken = undefined;
    while (hasMoreStock) {
      const resp = await stockTable.getRecords({ pageSize: 500, pageToken: stockPageToken });
      stockRows.push(...resp.records);
      hasMoreStock = resp.hasMore;
      stockPageToken = resp.pageToken;
    }
    log(`成功拉取 ${stockRows.length} 条库存记录`, "success");

    // 统一物理批次池: 海外仓每行 = 一份物理批次, is_dtc 标记归属团队
    // (修复: 旧版 dtc/owm 双池是同一批物理库存的两份独立拷贝, 借调时影子副本被再扣一次
    //  -> 共享组件消耗总量超过物理库存; 现改为单一物理池, 数学上不可能超卖)
    const owmBatchesByItem = {};

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

    // 3. 读取主表全量并建立 BOM
    log("正在读取《清库方案成本核算》主表...", "info");
    let mainRows = [];
    let hasMoreMain = true, mainPageToken = undefined;
    while (hasMoreMain) {
      const resp = await mainTable.getRecords({ pageSize: 500, pageToken: mainPageToken });
      mainRows.push(...resp.records);
      hasMoreMain = resp.hasMore;
      mainPageToken = resp.pageToken;
    }

    const recById = {};
    const mains = [];
    const subOf = {};
    const compModelToItems = {};
    const costByName = {};
    const noClearModels = new Set(); // 清库标记=不清库 的组件: 参与齐套, 但长版不进报废核算

    for (const r of mainRows) {
      const rid = r.recordId;
      const f = r.fields;
      const model = toText(f["model"]);
      const itemCode = toText(f["item"]);
      const parentLinks = f["父记录"] || [];
      const isSub = Array.isArray(parentLinks) && parentLinks.length > 0;

      // 单选字段「清库标记」(唯一选项: 不清库): 选中 → 报废核算排除 (留空 = 正常清库)
      const clearFlag = toText(f["清库标记"]);
      if (model && clearFlag.includes("不清库")) noClearModels.add(model);

      const rowObj = {
        record_id: rid,
        model,
        item: itemCode,
        is_sub: isSub,
        parent_id: isSub ? (parentLinks[0].record_id || parentLinks[0].id) : null,
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
    // 修复: 旧版 dtc/owm 双池是同一物理批次的两个独立拷贝, 独立站批次在 dtc 池被消耗后,
    // owm 池里的影子副本仍可被"借调" -> 共享组件消耗总量 > 物理库存 (超分配)
    const compPool = {};

    for (const [modelName, itemSet] of Object.entries(compModelToItems)) {
      let batches = [];
      for (const it of itemSet) {
        if (owmBatchesByItem[it]) batches.push(...owmBatchesByItem[it].map(b => ({ ...b })));
      }
      batches.sort((a, b) => b.age - a.age);
      compPool[modelName] = batches;
    }

    // 4. 阶段一: 营收排序 (仅作展示排名; 分配先后由边际价值贪心决定 2026-09-16)
    mains.sort((a, b) => (b.s30 * b.price) - (a.s30 * a.price) || (a.model || "").localeCompare(b.model || ""));

    // 5. 阶段二: 边际价值贪心分配 (单一物理池, 杜绝超分配; 共享组件取舍规则)
    // 每一套给「单套边际价值」最高的主model:
    //   边际价值 = 最优可行路径净回收(A段容量内取uA, 否则uB兜底) + 整套BOM避免的报废成本(不清库计0)
    //   borrow 模式: 还需至少一个组件有独立站批次 (借调口径与旧瀑布一致)
    // 边际价值 <= 0 不凑套: 组件保留在池中走报废
    const pool = {};
    for (const [k, bList] of Object.entries(compPool)) pool[k] = bList.map(b => ({ ...b }));

    // 组件单件报废成本 (不清库 = 0)
    const scrapUnitByComp = {};
    for (const compName of Object.keys(compPool)) {
      if (noClearModels.has(compName)) { scrapUnitByComp[compName] = 0; continue; }
      const rec = costByName[compName] || {};
      const fee = rec.weight < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
      scrapUnitByComp[compName] = (rec.pp_first || 0) + fee;
    }

    // 主model scope 与经济参数
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
      const priceA = m.price * (1 - P.discount_a);
      const uA = priceA - m.fixed - priceA * P.promo_rate;
      const uB = (m.price * P.discount_b) - m.pp_first;
      const multA = Math.min(Math.max(1 + P.lambda * P.discount_a, P.m_min), P.m_max);
      const qA = Math.floor(m.s30 * (P.window_days / 30.0) * multA);
      scopeByRid[m.record_id] = { scope, isBom };
      econByRid[m.record_id] = { uA, uB, qA };
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
        // 可行性: 全部组件物理有量; borrow 模式额外要求至少一个组件有独立站批次
        if (scope.some(n => poolTotal(n) <= 0)) continue;
        if (allowBorrow && scope.every(n => poolDtc(n) <= 0)) continue;
        const e = econByRid[m.record_id];
        const k = ksByRid[m.record_id];
        const pathV = (k < e.qA && e.uA > 0) ? e.uA : e.uB;
        const v = pathV + kitAvoidScrap(scope);
        if (v > bestV) { bestV = v; bestRid = m.record_id; }
      }
      if (bestRid === null || bestV <= 0) break;
      const { scope } = scopeByRid[bestRid];
      // 扣减: 独立站批次优先 (FIFO 最老在前), 不足借非独立站批次 (仅 borrow 模式) —— 同一物理池
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

    // 稀缺组件取舍明细 (共享组件被多个主model需要且耗尽)
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

    // 收集独立站长版死库存 (只有独立站自己的剩余才计入独立站报废)
    const longboards = [];
    for (const [comp, bList] of Object.entries(pool)) {
      const rem = bList.reduce((acc, b) => acc + (b.qty > 0 && b.is_dtc ? b.qty : 0), 0);
      if (rem > 0) {
        longboards.push({ model: comp, remaining: rem });
      }
    }
    // 阶段三: 长版强锁报废核算 (清库标记=不清库 的组件排除)
    // 报废值只挂载一行: 首选该model首条子记录行, 无子行用单一主记录行 (单行挂载, 防列汇总虚高)
    let totalScrapCost = 0;
    const scrapMapByModel = {};
    const excludedNoClear = [];
    const anchorRowId = {};   // model -> 唯一挂载行 recordId
    for (const lb of longboards) {
      if (noClearModels.has(lb.model)) {
        excludedNoClear.push({ model: lb.model, qty: lb.remaining });
        continue; // 不清库: 剩余不进报废核算
      }
      const rec = costByName[lb.model] || {};
      const ppFirst = rec.pp_first || 0;
      const w = rec.weight || 0;
      const fee = w < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
      const unitCost = ppFirst + fee;
      const cost = unitCost * lb.remaining;
      totalScrapCost += cost;
      scrapMapByModel[lb.model] = { qty: lb.remaining, cost };
      // 锚定行: 子记录行优先, 否则单一主记录行
      const subRow = mainRows.find(r => {
        const pl = r.fields["父记录"];
        return Array.isArray(pl) && pl.length > 0 && toText(r.fields["model"]) === lb.model;
      });
      const anyRow = mainRows.find(r => toText(r.fields["model"]) === lb.model);
      anchorRowId[lb.model] = subRow ? subRow.recordId : (anyRow ? anyRow.recordId : null);
    }
    if (excludedNoClear.length > 0) {
      log(`「清库标记=不清库」排除报废 ${excludedNoClear.length} 个组件: ` +
          excludedNoClear.map(e => `${e.model}(${e.qty}件)`).join("、"), "warn");
    }

    // 6. 阶段四: 4 选项闭环动态损失最小化测算
    const calcBonus = makeBonus(P);
    let totalK = 0;
    let totalBestNet = 0;
    const updates = [];
    const windowStats = [];   // 各主model最优窗口收集 (供全局最优窗口统计)

    for (const a of allocations) {
      const m = recById[a.record_id];
      if (a.K <= 0) {
        updates.push({
          recordId: m.record_id,
          fields: {
            "实际齐套数": 0,
            "最佳方案": "",
            "预计回收资金": 0
          }
        });
        continue;
      }

      totalK += a.K;

      const price = m.price;
      const fixed = m.fixed;
      const ppFirst = m.pp_first;
      const s30 = m.s30;

      const u_a = price * (1 - P.discount_a) - fixed - price * (1 - P.discount_a) * P.promo_rate;
      const u_b = price * P.discount_b - ppFirst;
      const rev_c_unit = price * P.discount_c;

      // 4 选项评估封装为窗口 W 的函数 (自定义窗口测算 + 最优窗口搜索复用)
      const evalOptions = (W) => {
        const mult_a = Math.max(P.m_min, Math.min(P.m_max, 1.0 + P.lambda * P.discount_a));
        const q_a = Math.floor(s30 * (W / 30.0) * mult_a);
        const mult_c = Math.max(P.m_min, Math.min(P.m_max, 1.0 + P.lambda * (1.0 - P.discount_c)));
        const q_c_cap = Math.floor(s30 * (W / 30.0) * mult_c);

        // 单方案全量
        const net_b_only = a.K * u_b;
        const k_c_full_cand = Math.min(a.K, q_c_cap);
        const rem_full_c = a.K - k_c_full_cand;
        const bonus_c_full = k_c_full_cand > 0 ? calcBonus(k_c_full_cand * rev_c_unit) : 0;
        const net_c_only = -bonus_c_full + rem_full_c * u_b;

        // 组合策略
        const k_a_cand = u_a > 0 ? Math.min(a.K, q_a) : 0;
        const rem = a.K - k_a_cand;
        const net_rem_b = rem * u_b;
        const k_c_cand = Math.min(rem, q_c_cap);
        const bonus_c_cand = k_c_cand > 0 ? calcBonus(k_c_cand * rev_c_unit) : 0;
        const rem_after_c = rem - k_c_cand;
        const net_rem_c = -bonus_c_cand + rem_after_c * u_b;

        const options = [
          { name: "A促销+剩余走B出清", net: k_a_cand * u_a + net_rem_b, ka: k_a_cand, kc: 0, kb: rem },
          { name: "A促销+剩余走C激励", net: k_a_cand * u_a + net_rem_c, ka: k_a_cand, kc: k_c_cand, kb: rem_after_c },
          { name: "全量B线下出清", net: net_b_only, ka: 0, kc: 0, kb: a.K },
          { name: "全量C激励出清", net: net_c_only, ka: 0, kc: k_c_full_cand, kb: rem_full_c }
        ];
        options.sort((x, y) => y.net - x.net);
        return options[0];
      };

      const best = evalOptions(P.window_days);   // 决策口径: 参数表自定义窗口

      // 最优窗口期: 逐日扫描 W∈[1,730], 净额最大(并列取最小W); 净额对窗口不敏感时为 null
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

      const k_a = best.ka;
      const k_c = best.kc;
      const k_b = best.kb;
      const final_net = best.net;

      totalBestNet += final_net;

      let best_plan_desc = "";
      if (k_a === a.K) {
        best_plan_desc = "A线上降价出清(100%覆盖)";
      } else {
        const segs = [];
        if (k_a > 0) segs.push(`A促销(${k_a}套)`);
        if (k_c > 0) segs.push(`C激励(${k_c}套)`);
        if (k_b > 0) segs.push(`B出清(${k_b}套)`);
        best_plan_desc = segs.length > 0 ? segs.join("+") : `无动作(ΔK=0)`;
      }

      const scrapInfo = anchorRowId[m.model] === m.record_id
        ? scrapMapByModel[m.model]
        : null;   // 非锚定行不写报废值 (单行挂载)
      updates.push({
        recordId: m.record_id,
        fields: {
          "实际齐套数": a.K,
          "最佳方案": best_plan_desc,
          "预计回收资金": Math.round(final_net * 100) / 100,
          "最优窗口期(天)": bestWindow,
          "报废数量": scrapInfo ? scrapInfo.qty : null,
          "报废成本": scrapInfo ? Math.round(scrapInfo.cost * 100) / 100 : null
        }
      });
    }

    // 7. 报废写回: 单行挂载 —— 锚定行写值, 同model其余行全部清空 (防列汇总虚高/残留)
    for (const r of mainRows) {
      const f = r.fields;
      const model = toText(f["model"]);
      const isSub = Array.isArray(f["父记录"]) && f["父记录"].length > 0;
      const scrapInfo = anchorRowId[model] === r.recordId ? scrapMapByModel[model] : null;
      const fields = {
        "报废数量": scrapInfo ? scrapInfo.qty : null,
        "报废成本": scrapInfo ? Math.round(scrapInfo.cost * 100) / 100 : null
      };
      if (isSub) {
        Object.assign(fields, { "实际齐套数": null, "最佳方案": "", "预计回收资金": null });
      }
      updates.push({ recordId: r.recordId, fields });
    }

    // 8. 批量写回多维表格
    log(`正在批量写回 ${updates.length} 条记录到《清库方案成本核算》表...`, "info");
    // 全局最优窗口 = 各主model最优窗口的众数 (须在写日志前算好, 旧版声明在使用之后 → TDZ 报错)
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
    // 字段元信息用 getFieldMetaList() (deprecated getFieldList() 返回 Field 实例, 无 .name 属性)
    const mFieldMetas = await mainTable.getFieldMetaList();
    const fieldMap = {};
    for (const f of mFieldMetas) fieldMap[f.name] = f.id;

    const neededFields = ["实际齐套数", "最佳方案", "预计回收资金", "报废数量", "报废成本", "最优窗口期(天)"];
    for (const fName of neededFields) {
      if (!fieldMap[fName]) {
        const fType = fName === "最佳方案" ? bitable.FieldType.Text : bitable.FieldType.Number;
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
        const logFields = {};
        if (lNameToId["运行时间戳"]) logFields[lNameToId["运行时间戳"]] = new Date().toLocaleString();
        if (lNameToId["测算场景"]) logFields[lNameToId["测算场景"]] = `${P.scenario} (${allowBorrow ? "允许借调" : "纯独立站"})`;
        if (lNameToId["关键参数快照"]) logFields[lNameToId["关键参数快照"]] = `窗口:${P.window_days}天；借调:${allowBorrow ? "是" : "否"}；齐套K:${totalK}；回收:$${Math.round(totalBestNet)}；长版报废:$${Math.round(totalScrapCost)}；全局最优窗口:${globalBestWindow ? globalBestWindow + "天" : "-"}`;
        await logTable.addRecord({ fields: logFields });
      } catch (e) {
        console.warn("写入日志表失败:", e);
      }
    }

    log("🎉 全部测算与写回完成！", "success");

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
