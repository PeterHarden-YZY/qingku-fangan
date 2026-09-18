    // 6. 阶段一: 营收排序 (仅作展示排名, 不再决定分配先后)
    mains.sort((a, b) => (b.s30 * b.price) - (a.s30 * a.price) || (a.model || "").localeCompare(b.model || ""));

    // 7. 阶段二: 边际价值贪心分配 (共享组件取舍规则 2026-09-16 拍板)
    // 每一套齐套名额给「单套边际价值」最高的主model:
    //   单套边际价值 = 最优可行路径净回收(A段容量内取uA, 否则uB兜底) + 整套BOM避免的报废成本
    //   (不清库组件的避免报废计 0)
    // 边际价值 <= 0 不凑套: 组件保留在池中走报废 (更优)
    const pool = {};
    for (const [k, v] of Object.entries(components)) {
        pool[k] = v.batches.map(b => Object.assign({}, b));
    }

    // 组件单件报废成本 (不清库 = 0)
    const scrapUnitByComp = {};
    for (const compName of Object.keys(components)) {
        if (noClearModels.has(compName)) {
            scrapUnitByComp[compName] = 0;
            continue;
        }
        const rec = costByName[compName] || {};
        const fee = rec.weight < P.weight_threshold ? P.scrap_fee_light : P.scrap_fee_heavy;
        scrapUnitByComp[compName] = (rec.pp_first || 0) + fee;
    }

    // 主model BOM scope 与经济参数
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

    const poolTotal = (comp) => (pool[comp] || []).reduce((acc, b) => acc + b.qty, 0);
    const kitAvoidScrap = (scope) => scope.reduce((acc, n) => acc + (scrapUnitByComp[n] || 0), 0);

    const ksByRid = {};
    const consumedByRid = {};
    for (const m of mains) {
        ksByRid[m.record_id] = 0;
        consumedByRid[m.record_id] = {};
    }

    let guard = 0;
    while (true) {
        guard++;
        if (guard > 2000000) throw new Error("贪心循环超限");
        let bestRid = null, bestV = -Infinity;
        for (const m of mains) {
            const { scope } = scopeByRid[m.record_id];
            if (scope.length === 0) continue;
            if (scope.some(n => poolTotal(n) <= 0)) continue;
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
            for (const b of pool[comp]) {
                if (need <= 0) break;
                if (b.qty <= 0) continue;
                const take = Math.min(b.qty, need);
                b.qty -= take;
                need -= take;
                consumedByRid[bestRid][comp].push({ qty: take, age: b.age });
            }
        }
        ksByRid[bestRid] += 1;
    }

    // 输出 allocations (rank 按营收展示排序)
    const allocations = [];
    let rank = 1;
    for (const m of mains) {
        const { scope, isBom } = scopeByRid[m.record_id];
        allocations.push({
            record_id: m.record_id,
            model: m.model,
            rank: rank++,
            type: isBom ? "bom" : "single",
            K: ksByRid[m.record_id],
            scope: scope,
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
        const stock = components[compName]
            ? components[compName].batches.reduce((acc, b) => acc + b.qty, 0)
            : 0;
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
        console.log(`⚖️ 稀缺组件取舍 ${scarcityDecisions.length} 个:`);
        for (const d of scarcityDecisions) {
            const w = Object.entries(d.winners).map(([mn, k]) => `${mn}(${k}套)`).join(", ");
            console.log(`   ${d.component}: ${w} | 让位: ${d.starved.join(", ")}`);
        }
    }

    // 收集长版死库存
    const longboards = [];
    for (const [comp, bList] of Object.entries(pool)) {
        const rem = bList.reduce((acc, b) => acc + (b.qty > 0 ? b.qty : 0), 0);
        if (rem > 0) {
            longboards.push({
                model: comp,
                remaining: rem,
                batches: bList.filter(b => b.qty > 0)
            });
        }
    }
