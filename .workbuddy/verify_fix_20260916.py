# -*- coding: utf-8 -*-
"""验证问题1修复: 用真实数据复刻修复后的 src_app.js 单池算法, 证明无超分配"""
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


# ---- 库存表: item -> 物理批次 (海外仓全量, is_dtc 标记) — 复刻修复后代码 ----
owmBatchesByItem = {}
for s in stock:
    team = toText(s.get("所属团队"))
    whType = toText(s.get("仓库类型"))
    itemCode = toText(s.get("item"))
    qty = to_num(s.get("可用量")) or 0
    age = to_num(s.get("库龄(程序拉取)")) or 0
    if whType == "海外仓" and qty > 0 and itemCode:
        owmBatchesByItem.setdefault(itemCode, []).append({"qty": qty, "age": age, "is_dtc": team == "独立站"})

recById, mains, subOf, compModelToItems, costByName, noClearModels = {}, [], {}, {}, {}, set()
for r in rows:
    rid = r.get("record_id")
    model = toText(r.get("model"))
    itemCode = toText(r.get("item"))
    parentLinks = r.get("父记录") or []
    isSub = bool(parentLinks)
    if model and "不清库" in toText(r.get("清库标记")):
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

# 单一物理池 (修复后)
compPool = {}
for modelName, itemSet in compModelToItems.items():
    batches = []
    for it in itemSet:
        if it in owmBatchesByItem:
            batches.extend(dict(b) for b in owmBatchesByItem[it])
    batches.sort(key=lambda b: -b["age"])
    compPool[modelName] = batches

mains.sort(key=lambda m: -(m["s30"] * m["price"]))
pool = {k: [dict(b) for b in v] for k, v in compPool.items()}

allowBorrow = True
allocations = []
for m in mains:
    subs = subOf.get(m["record_id"], [])
    scope = sorted({s["model"] for s in subs}) if subs else ([m["model"]] if m["model"] else [])
    K = 0
    if scope:
        dtcAvails = [sum(b["qty"] for b in pool.get(c, []) if b["is_dtc"]) for c in scope]
        if allowBorrow:
            maxDtc = max(dtcAvails) if dtcAvails else 0
            if maxDtc > 0:
                owmAvails = [sum(b["qty"] for b in pool.get(c, [])) for c in scope]
                K = min(maxDtc, *owmAvails)
        else:
            K = min(dtcAvails) if dtcAvails else 0
    consumed = {}
    if K > 0:
        for comp in scope:
            consumed[comp] = []
            need = K
            bList = pool.get(comp, [])
            for b in bList:  # pass1 独立站优先
                if need <= 0:
                    break
                if b["qty"] <= 0 or not b["is_dtc"]:
                    continue
                take = min(b["qty"], need)
                b["qty"] -= take
                need -= take
                consumed[comp].append({"qty": take, "age": b["age"], "is_dtc": True})
            if need > 0:  # pass2 借非独立站
                for b in bList:
                    if need <= 0:
                        break
                    if b["qty"] <= 0 or b["is_dtc"]:
                        continue
                    take = min(b["qty"], need)
                    b["qty"] -= take
                    need -= take
                    consumed[comp].append({"qty": take, "age": b["age"], "is_dtc": False})
    allocations.append({"model": m["model"], "K": K, "scope": scope, "consumed": consumed})

# ===== 验证: 共享组件消耗 <= 物理库存 =====
print("=" * 68)
print("【修复后验证】共享组件消耗 vs 物理库存 (borrow 模式, 单一物理池)")
print("=" * 68)
all_comps = set()
for a in allocations:
    all_comps.update(a["consumed"].keys())

over = []
shared = []
for comp in sorted(all_comps):
    total_consumed = sum(b["qty"] for a in allocations for b in a["consumed"].get(comp, []))
    phys = sum(b["qty"] for b in compPool.get(comp, []))
    parents = [a["model"] for a in allocations
               if comp in a["consumed"] and sum(x["qty"] for x in a["consumed"][comp]) > 0]
    if len(parents) > 1:
        shared.append((comp, total_consumed, phys, parents))
    if total_consumed > phys + 0.5:
        over.append((comp, total_consumed, phys, parents))

print(f"共享组件(被>1主model消耗): {len(shared)} 个")
for comp, tc, po, parents in shared:
    mark = " ❌超分配" if tc > po + 0.5 else " ✅"
    print(f"  {comp}: 消耗={tc:.0f} / 物理={po:.0f} | {parents}{mark}")
print()
print(f"超物理库存分配: {len(over)} 个 {'✅ 修复成功 — 全部组件消耗 ≤ 物理库存' if not over else '❌ 仍有超分配'}")

# 之前超分配的两个组件对照
print()
print("修复前后对照 (之前超分配的组件):")
for comp in ("AMBFC04NR-C2-US", "HNESD217W-US", "AMESD02B-F-US"):
    tc = sum(b["qty"] for a in allocations for b in a["consumed"].get(comp, []))
    po = sum(b["qty"] for b in compPool.get(comp, []))
    print(f"  {comp}: 消耗={tc:.0f} / 物理={po:.0f} {'✅' if tc <= po + 0.5 else '❌'}")

# 长版与报废 (修复后: is_dtc 剩余)
longboards = []
for comp, bList in pool.items():
    rem = sum(b["qty"] for b in bList if b["qty"] > 0 and b["is_dtc"])
    if rem > 0:
        longboards.append({"model": comp, "remaining": rem})

P = {"scrap_fee_light": 2.0, "scrap_fee_heavy": 3.0, "weight_threshold": 50.0}
total = 0
scrap_list = []
for lb in longboards:
    if lb["model"] in noClearModels:
        continue
    rec = costByName.get(lb["model"], {})
    pp = rec.get("pp_first", 0)
    w = rec.get("weight", 0)
    fee = P["scrap_fee_light"] if w < P["weight_threshold"] else P["scrap_fee_heavy"]
    total += (pp + fee) * lb["remaining"]
    scrap_list.append(lb["model"])
print()
print(f"修复后插件口径长版报废: {len(scrap_list)} 组件, 成本 ${total:,.2f} (Python链路: $26,152.63)")
print(f"与 Python 链路一致: {'✅' if abs(total - 26152.63) < 0.05 else '❌'}")
