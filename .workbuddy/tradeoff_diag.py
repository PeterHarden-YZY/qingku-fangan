# -*- coding: utf-8 -*-
"""诊断: 稀缺共享组件的分配取舍, 现行营收排序 vs 边际毛值视角"""
import json, math
from pathlib import Path

C = Path(__file__).resolve().parent.parent / ".workbuddy"
rows = json.loads((C / "inventory_cache.json").read_text(encoding="utf-8"))
comps = json.loads((C / "components.json").read_text(encoding="utf-8"))
alloc = json.loads((C / "allocation_result.json").read_text(encoding="utf-8"))
scen = json.loads((C / "scenario_result.json").read_text(encoding="utf-8"))
P = scen["params"]
scrap_items = {s["model"]: s for s in json.loads((C / "scrap_result.json").read_text(encoding="utf-8"))["items"]}
excluded_nc = {e["model"] for e in json.loads((C / "scrap_result.json").read_text(encoding="utf-8"))["excluded_no_clear"]}

def to_num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

sub_of = {}
for r in rows:
    for x in (r.get("父记录") or []):
        sub_of.setdefault(x["id"], []).append(r)
mains = {r["record_id"]: r for r in rows if not r.get("父记录")}
rec_by_id = {r["record_id"]: r for r in rows}

# 主model BOM
bom = {}
for m in mains.values():
    subs = sub_of.get(m["record_id"], [])
    bom[m["record_id"]] = sorted({s["model"] for s in subs if s.get("model")}) or ([m["model"]] if m.get("model") else [])

# 组件 -> 使用它的主model
need = {}
for mid, names in bom.items():
    for n in names:
        need.setdefault(n, set()).add(mid)

# 单件报废成本(不清库=无报废成本)
def scrap_unit(n):
    if n in excluded_nc:
        return 0.0
    s = scrap_items.get(n)
    if s and s.get("unit_scrap_cost") is not None:
        return s["unit_scrap_cost"]
    # 不在长版清单 = 已被分完, 无剩余报废, 避免报废信用照算(用成本字段兜底)
    for r in rows:
        if r["model"] == n:
            pp = to_num(r.get("采购价+头程")) or 0.0
            w = to_num(r.get("部件重量 lbs"))
            fee = 0.0 if w is None else (2.0 if w < 50 else 3.0)
            return pp + fee
    return 0.0

scen_by_model = {r["model"]: r for r in scen["results"]}

print("=" * 110)
print("稀缺共享组件争夺战: 现行规则(营收排序瀑布) 分给了谁 / 各候选主model的单套经济性")
print("=" * 110)

for name in sorted(need):
    users = need[name]
    if len(users) < 2:
        continue
    stock = sum(b["qty"] for b in comps.get(name, {}).get("batches", []))
    if stock <= 0:
        continue
    # 分配结果: 各使用者拿到几套
    got = {}
    for a in alloc["allocations"]:
        if a["record_id"] in users:
            got[a["model"]] = a["K"]
    total_got = sum(got.values())
    if total_got < stock:
        continue  # 不稀缺
    print()
    print(f"### 稀缺组件 {name}  库存={stock:.0f}  使用者数={len(users)}")
    # 每个使用者的单套经济性
    print(f"  {'model':26} {'营收排名':>4} {'K分到':>5} {'售价':>8} {'30d销量':>7} {'u_a':>8} {'u_b':>8} {'Q_a(W=60)':>9} {'BOM组件数':>5} {'单套避免报废$':>9}")
    for a in alloc["allocations"]:
        if a["record_id"] not in users:
            continue
        m = mains[a["record_id"]]
        sr = scen_by_model.get(a["model"], {})
        price = to_num(m.get("售价")) or 0
        s30 = to_num(m.get("过去30天销量")) or 0
        u_a = sr.get("u_a"); u_b = sr.get("u_b")
        q_a = sr.get("q_a")
        avoid = sum(scrap_unit(n) for n in bom[a["record_id"]])
        print(f"  {a['model']:26} {a['rank']:>4} {a['K']:>5.0f} {price:>8.1f} {s30:>7.0f} "
              f"{(u_a if u_a is not None else float('nan')):>8.2f} {(u_b if u_b is not None else float('nan')):>8.2f} "
              f"{(q_a if q_a is not None else float('nan')):>9.0f} {len(bom[a['record_id']]):>5} {avoid:>9.2f}")
    # 谁是真赢家: 单套毛值 = 边际净回收 + 避免报废
    print(f"  --- 单套毛值视角 (A段: u_a + 避免报废; B段: u_b + 避免报废) ---")
    for a in alloc["allocations"]:
        if a["record_id"] not in users:
            continue
        sr = scen_by_model.get(a["model"], {})
        if not sr:
            continue
        avoid = sum(scrap_unit(n) for n in bom[a["record_id"]])
        g_a = (sr.get("u_a") or 0) + avoid
        g_b = (sr.get("u_b") or 0) + avoid
        winner = " <== 现行赢家" if a["K"] > 0 else ""
        print(f"  {a['model']:26} A段毛值={g_a:>8.2f}/套 (可卖{sr.get('q_a',0):.0f}套) B段毛值={g_b:>8.2f}/套{winner}")

print()
print("=" * 110)
print("B路径单套净回收(u_b)为负的 model (卖15折仍亏采购+头程): 报废 vs 出清的边界")
print("=" * 110)
for sr in sorted(scen["results"], key=lambda x: x.get("u_b") or 0):
    ub = sr.get("u_b")
    if ub is not None and ub < 0:
        m = rec_by_id.get(sr["record_id"], {})
        avoid = None
        names = bom.get(sr["record_id"], [])
        # 整套报废成本
        print(f"  {sr['model']:26} u_b={ub:>8.2f}  K={sr['K']:>4.0f}  kB={sr['k_b']:>4.0f}  net={sr['net']:>10,.2f}")
