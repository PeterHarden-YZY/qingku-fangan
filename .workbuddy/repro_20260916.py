# -*- coding: utf-8 -*-
"""复现问题1+2: 用真实缓存数据精确复刻线上插件(borrow模式)的分配与报废算法"""
import json

stock = json.load(open(r'D:\agents_project\清库方案自动计算\.workbuddy\stock_rows_cache.json', encoding='utf-8'))
rows = json.load(open(r'D:\agents_project\清库方案自动计算\.workbuddy\inventory_cache.json', encoding='utf-8'))


def toText(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        return ",".join(str(x) if isinstance(x, str) else (x.get('text') or x.get('name') or "") for x in v).strip()
    if isinstance(v, dict):
        return v.get('text') or v.get('name') or ""
    return str(v).strip()


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---- 库存表: item -> (dtc批次, owm批次) ----
dtcBatchesByItem, owmBatchesByItem = {}, {}
for s in stock:
    team = toText(s.get("所属团队"))
    whType = toText(s.get("仓库类型"))
    itemCode = toText(s.get("item"))
    qty = to_num(s.get("可用量")) or 0
    age = to_num(s.get("库龄(程序拉取)")) or 0
    if whType == "海外仓" and qty > 0 and itemCode:
        owmBatchesByItem.setdefault(itemCode, []).append({"qty": qty, "age": age, "is_dtc": team == "独立站"})
        if team == "独立站":
            dtcBatchesByItem.setdefault(itemCode, []).append({"qty": qty, "age": age, "is_dtc": True})

# ---- 主表: BOM + costByName + noClear ----
recById, mains, subOf, compModelToItems, costByName, noClearModels = {}, [], {}, {}, {}, set()
for r in rows:
    rid = r.get("record_id")
    model = toText(r.get("model"))
    itemCode = toText(r.get("item"))
    parentLinks = r.get("父记录") or []
    isSub = bool(parentLinks)
    clearFlag = toText(r.get("清库标记"))
    if model and "不清库" in clearFlag:
        noClearModels.add(model)
    rowObj = dict(r, record_id=rid, model=model, item=itemCode, is_sub=isSub,
                  parent_id=(parentLinks[0].get("id") or parentLinks[0].get("record_id")) if isSub else None,
                  price=to_num(r.get("售价")) or 0, fixed=to_num(r.get("固定成本合计")) or 0,
                  pp_first=to_num(r.get("采购价+头程")) or 0, weight=to_num(r.get("部件重量 lbs")) or 0,
                  s30=to_num(r.get("过去30天销量")) or 0)
    recById[rid] = rowObj
    if model:
        compModelToItems.setdefault(model, set())
        if itemCode:
            compModelToItems[model].add(itemCode)
        if model not in costByName or rowObj["pp_first"] > 0:
            costByName[model] = rowObj
    if isSub:
        subOf.setdefault(rowObj["parent_id"], []).append(rowObj)
    else:
        mains.append(rowObj)

# ---- 组件池 (复刻插件) ----
compPoolDtc, compPoolOwm = {}, {}
for modelName, itemSet in compModelToItems.items():
    bd, bo = [], []
    for it in itemSet:
        if it in dtcBatchesByItem:
            bd.extend(dict(b) for b in dtcBatchesByItem[it])
        if it in owmBatchesByItem:
            bo.extend(dict(b) for b in owmBatchesByItem[it])
    bd.sort(key=lambda b: -b["age"])
    bo.sort(key=lambda b: -b["age"])
    compPoolDtc[modelName] = bd
    compPoolOwm[modelName] = bo

# ---- 阶段一+二 (复刻插件 borrow 模式) ----
mains.sort(key=lambda m: -(m["s30"] * m["price"]))
poolDtc = {k: [dict(b) for b in v] for k, v in compPoolDtc.items()}
poolOwm = {k: [dict(b) for b in v] for k, v in compPoolOwm.items()}

allocations = []
for m in mains:
    subs = subOf.get(m["record_id"], [])
    scope = sorted({s["model"] for s in subs}) if subs else ([m["model"]] if m["model"] else [])
    K = 0
    if scope:
        dtcAvails = [sum(b["qty"] for b in poolDtc.get(c, [])) for c in scope]
        owmAvails = [sum(b["qty"] for b in poolOwm.get(c, [])) for c in scope]
        maxDtc = max(dtcAvails) if dtcAvails else 0
        if maxDtc > 0:
            K = min(maxDtc, *owmAvails)
    consumed = {}
    if K > 0:
        for comp in scope:
            consumed[comp] = []
            need = K
            for b in poolDtc.get(comp, []):
                if need <= 0:
                    break
                if b["qty"] <= 0:
                    continue
                take = min(b["qty"], need)
                b["qty"] -= take
                need -= take
                consumed[comp].append({"qty": take, "age": b["age"], "is_dtc": True})
            if need > 0:
                for b in poolOwm.get(comp, []):
                    if need <= 0:
                        break
                    if b["qty"] <= 0:
                        continue
                    take = min(b["qty"], need)
                    b["qty"] -= take
                    need -= take
                    consumed[comp].append({"qty": take, "age": b["age"], "is_dtc": False})
    allocations.append({"model": m["model"], "K": K, "scope": scope, "consumed": consumed})

# ---- 长版 (插件口径: 只看 poolDtc 残余) ----
longboards = []
for comp, bList in poolDtc.items():
    rem = sum(b["qty"] for b in bList if b["qty"] > 0)
    if rem > 0:
        longboards.append({"model": comp, "remaining": rem})

# ---- 报废 (插件口径) ----
P = {"scrap_fee_light": 2.0, "scrap_fee_heavy": 3.0, "weight_threshold": 50.0}
scrapItems, total = [], 0
for lb in longboards:
    if lb["model"] in noClearModels:
        continue
    rec = costByName.get(lb["model"], {})
    pp = rec.get("pp_first", 0)
    w = rec.get("weight", 0)
    fee = P["scrap_fee_light"] if w < P["weight_threshold"] else P["scrap_fee_heavy"]
    total += (pp + fee) * lb["remaining"]
    scrapItems.append({"model": lb["model"], "qty": lb["remaining"], "cost": (pp + fee) * lb["remaining"]})

# ============================================================
# 复现检测
# ============================================================
print("=" * 70)
print("【问题1 复现】共享组件消耗 vs 物理库存 (borrow 模式, 线上插件算法)")
print("=" * 70)

all_comps = set()
for a in allocations:
    all_comps.update(a["consumed"].keys())

over = []
shared_info = []
for comp in sorted(all_comps):
    total_consumed = sum(b["qty"] for a in allocations for b in a["consumed"].get(comp, []))
    phys_dtc = sum(b["qty"] for b in compPoolDtc.get(comp, []))
    phys_owm = sum(b["qty"] for b in compPoolOwm.get(comp, []))
    parent_models = [a["model"] for a in allocations
                     if comp in a["consumed"] and sum(x["qty"] for x in a["consumed"][comp]) > 0]
    if len(parent_models) > 1:
        shared_info.append((comp, total_consumed, phys_owm, phys_dtc, parent_models))
    if total_consumed > phys_owm + 0.5:
        over.append((comp, total_consumed, phys_owm, phys_dtc, parent_models))

print(f"被多个主model共同消耗的共享组件: {len(shared_info)} 个")
for comp, tc, po, pd, parents in shared_info:
    mark = " ❌超分配" if tc > po + 0.5 else ""
    print(f"  {comp}: 消耗={tc:.0f} / 物理海外仓={po:.0f} / 其中独立站={pd:.0f} | 主model: {parents}{mark}")

print()
if over:
    print(f"❌ 问题1 实锤: 超物理库存分配的组件 {len(over)} 个")
    for comp, tc, po, pd, parents in over:
        print(f"  {comp}: 消耗总量={tc:.0f} > 物理库存={po:.0f} (凭空多出 {tc-po:.0f} 件)")
else:
    print("⚠️ 当前真实数据下共享组件恰好充裕, 未直接触发超分配")
    print("   -> 用最小可复现案例证明双池口径的数学必然性 (下一步)")

print()
print("=" * 70)
print("【问题2 复现】报废值广播写回 vs 单行挂载 (行求和口径)")
print("=" * 70)
comp_rids_by_model = {}
for r in rows:
    mn = toText(r.get("model"))
    if mn:
        comp_rids_by_model.setdefault(mn, []).append(r.get("record_id"))

print(f"插件长版报废组件数: {len(scrapItems)}, 总成本(单值口径): ${total:,.2f}")
multi_row_models = []
for s in scrapItems:
    rids = comp_rids_by_model.get(s['model'], [])
    if len(rids) > 1:
        multi_row_models.append((s['model'], len(rids), s['qty'], s['cost']))

print(f"\n报废组件中在表里有>1条记录行的: {len(multi_row_models)} 个 (插件把 qty/cost 广播写到每一行)")
sum_single = 0
sum_broadcast = 0
for mn, n, q, c in multi_row_models:
    print(f"  {mn}: {n} 行 × qty={q:.0f}/cost=${c:,.2f} -> 行求和={q*n:.0f}件/${c*n:,.2f} (虚高 {n} 倍)")
    sum_single += c
    sum_broadcast += c * n
for s in scrapItems:
    mn = s['model']
    if len(comp_rids_by_model.get(mn, [])) <= 1:
        sum_single += s['cost']
        sum_broadcast += s['cost']

print(f"\n真实口径(每model一份): ${sum_single:,.2f}")
print(f"按行求和口径(用户在表格里拉列汇总): ${sum_broadcast:,.2f}")
print(f"虚高: ${sum_broadcast-sum_single:,.2f} ({(sum_broadcast/sum_single-1)*100:.0f}%)" if sum_single else "")

# 当前线上表里的实际报废值分布 (验证广播已经发生)
print()
print("=" * 70)
print("【线上表实况】当前「报废数量/报废成本」字段按行分布")
print("=" * 70)
scrap_rows = [(toText(r.get("model")), r.get("报废数量"), r.get("报废成本"))
              for r in rows if r.get("报废数量") or r.get("报废成本")]
by_model = {}
for mn, q, c in scrap_rows:
    by_model.setdefault(mn, []).append((q, c))
for mn, lst in by_model.items():
    if len(lst) > 1:
        qsum = sum((q or 0) for q, _ in lst)
        csum = sum((c or 0) for _, c in lst)
        print(f"  {mn}: {len(lst)} 行, 行求和 qty={qsum:.0f} cost=${csum:,.2f}  ← 广播残留")

# ---- 最小可复现案例: 双池口径数学证明 ----
print()
print("=" * 70)
print("【最小案例】双池借调口径的数学必然性 (独立站 ⊆ 海外仓总量)")
print("=" * 70)
# 场景: 组件X只在独立站海外仓有 100 件 (海外仓总量=100, 独立站=100)
# 主model A (优先) scope=[X,Y], 主model B (次优先) scope=[X,Z], Y/Z 库存都充足
# 插件算法: A: dtcAvails=[100, 大], owmAvails=[100, 大] -> K_A=100
#           消耗: poolDtc[X] 全扣 100 -> 0; poolOwm[X] 也扣 100 -> 0  (此时正确)
# 场景2: 组件X 独立站有 60, 非独立站海外仓有 40 (海外仓总量=100)
#   A: maxDtc=60, owmAvail=100 -> K_A=60; 消耗 poolDtc 60, 无需借
#   B: maxDtc=0 -> K_B=0. 正确.
# 场景3 (bug): 主model A scope=[X,Y]; X独立站=60+海外仓40, Y独立站=100
#   A: dtc=[60,100], owm=[100,100] -> K=min(100,100,100)=100
#   消耗: X: poolDtc 60, 再借 poolOwm 40 -> X 独立站池=0, owm池=0. 正确?
#   -> 检查: poolOwm[X] 的 100 里, 前 60 件就是独立站批次本身!
#      poolDtc 扣的 60 与 poolOwm 扣的前 60 是同一批物理库存, 再借 40 时
#      poolOwm 只剩 40 (因为独立站那 60 已被扣). 实际上 owm 池扣的是 60(dt本体)+40(借) = 100.
#   结论: 若 owm 池独立扣减, dtc 借调部分不会双扣 —— 除非 dtc 池扣减不同步 owm 池.
#   插件代码: poolDtc 和 poolOwm 是独立列表 (同样的批次对象被 map 浅拷贝成两份!)
#   => X 的独立站 60 件在 poolDtc 扣一次, 又在 poolOwm 里被当作可借库存再扣一次 = 双重计数!
print("关键: src_app.js L249-250 对同一批次做两次 map 拷贝,")
print("      poolDtc[comp] 与 poolOwm[comp] 是两份独立副本,")
print("      消耗 poolDtc 时 poolOwm 副本里的同物理批次并未减少,")
print("      借调阶段会把已被独立站口径消耗的批次再借一次 => 超分配。")
print()
print("最小案例数值验证:")
X_dtc, X_owm = 60, 100  # 独立站60件(在海外仓), 海外仓总量100(含独立站60+非独立站40)
# 主model A (rank1): scope=[X], 只有X
dtcAvails = [X_dtc]        # poolDtc[X] = 60
owmAvails = [X_owm]        # poolOwm[X] = 100 (含独立站的60)
maxDtc = max(dtcAvails)    # 60
K_A = min(maxDtc, *owmAvails)  # min(60, 100) = 60
# 消耗: poolDtc[X] 60 -> 0
# 借调: need=0, 不借. K_A=60 正确.
# 主model B (rank2): scope=[X]
dtcAvails_B = [0]
# maxDtc=0 -> K_B=0. 正确.
print(f"  单主model场景: K_A={K_A}, K_B=0 — 正确 (借调只发生在同一次消耗的 need>0 时)")

# 真 bug 场景: 借调发生时 (need>0), poolOwm 里独立站批次仍在
X_dtc2, X_owm2 = 60, 100
Y = 100
# 主model A: scope=[X, Y]; Y 独立站充足
dtcAvails = [X_dtc2, Y]     # [60, 100]
owmAvails = [X_owm2, Y]     # [100, 100]
maxDtc = max(60, 100)       # 100
K_A = min(100, 100, 100)    # 100
# 消耗 X: poolDtc[X] 60 -> 0, need=40 -> 借 poolOwm[X]:
#   poolOwm[X] 的批次列表 = [独立站批次60, 非独立站40] (都完整存在)
#   借走 40: FIFO 最老批次优先. 独立站批次(60件)若更老, 先借它!
#   借走的这 40 件 = poolDtc 里已被扣的同一批物理库存 => X 实际物理消耗 = 60(dtc扣) + 40(借走dtc批次) = 100
#   但物理上 X 只有 100 件, 其中独立站 60. 消耗 100 = 全部. 若独立站批次更老:
#   poolOwm 扣的 40 全来自独立站批次 => 独立站 60 件被计了两次消耗! (dtc池扣60 + owm池借40)
#   实际 K_A=100 套齐套成立吗? X 物理就 100 件, K=100 消耗 X 100 件 — 物理上成立!
#   但! "独立站口径" K=min(maxDtc=100?...) maxDtc=max(60,100)=100 来自 Y 的独立站100,
#   而规则要求"以独立站最大呆滞组件为上限" — 本意是 X 只能配 60+借40.
#   借 40 来自 poolOwm[X]: 其中独立站批次60件是 poolDtc 已扣的影子 — 借走它们等于
#   把同一物理件计入"独立站消耗"又计入"借调". X 真实可借的非独立站库存只有 40,
#   FIFO 老批次(独立站)优先借 => 数学上 dtc消耗60+借调40=100 恰好等于物理100,
#   但其中"借调的40"有 0..40 件实际是独立站库存被二次计数.
print(f"  借调场景: K_A={K_A}, poolDtc[X]扣60, 借调 need=40")
print("  poolOwm[X]=100 含独立站批次60(影子, dtc池已扣) + 非独立站40(真可借)")
print("  FIFO 最老优先: 若独立站批次更老, 借走的40件来自独立站批次 => 同一批物理库存双扣")
print("  => X 的独立站60件被记为: 独立站齐套60 + 借调40 = 100 件消耗, 物理只有60件独立站库存")

# 用真实数据找出实际借调发生的组件, 验证影子借调
print()
print("=" * 70)
print("【真实数据佐证】借调批次构成审计 (borrow 发生时借的是谁的库存)")
print("=" * 70)
borrow_events = []
for a in allocations:
    for comp, batches in a["consumed"].items():
        borrowed = sum(b["qty"] for b in batches if not b.get("is_dtc"))
        if borrowed > 0:
            borrow_events.append((a["model"], comp, borrowed))
if borrow_events:
    for mdl, comp, q in borrow_events:
        print(f"  {mdl} 借调 {comp} {q:.0f} 件 (从 poolOwm)")
else:
    print("  当前数据: 无借调发生 (所有主model独立站池已足够)")
