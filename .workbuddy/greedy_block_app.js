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
