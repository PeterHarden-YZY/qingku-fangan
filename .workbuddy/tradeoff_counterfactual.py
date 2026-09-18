# -*- coding: utf-8 -*-
"""反事实: 稀缺组件给错人了吗?
对每个稀缺组件 X:
  - 现行赢家 = 营收排序瀑布下拿到 X 的 model
  - 饿死者 = 因 X 短缺而 K 受限的 model (排除其他组件瓶颈: 该model的BOM中,除X外其他组件的'原始总库存-已被前面model消耗'足够)
计算每人拿到1件X能带来的边际价值:
  v(1套) = min(u_a,q_a>0时) 或 u_b 的可行最优 + 整套BOM避免的报废成本
"""
import json, math
from pathlib import Path

C = Path(__file__).resolve().parent.parent / ".workbuddy"
rows = json.loads((C / "inventory_cache.json").read_text(encoding="utf-8"))
comps = json.loads((C / "components.json").read_text(encoding="utf-8"))
alloc = json.loads((C / "allocation_result.json").read_text(encoding="utf-8"))
scen = json.loads((C / "scenario_result.json").read_text(encoding="utf-8"))
scrap_res = json.loads((C / "scrap_result.json").read_text(encoding="utf-8"))

def to_num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

sub_of = {}
for r in rows:
    for x in (r.get("父记录") or []):
        sub_of.setdefault(x["id"], []).append(r)
mains = {r["record_id"]: r for r in rows if not r.get("父记录")}

bom = {}
for m in mains.values():
    subs = sub_of.get(m["record_id"], [])
    bom[m["record_id"]] = sorted({s["model"] for s in subs if s.get("model")}) or ([m["model"]] if m.get("model") else [])

need = {}
for mid, names in bom.items():
    for n in names:
        need.setdefault(n, set()).add(mid)

# 组件总库存
stock_total = {n: sum(b["qty"] for b in c["batches"]) for n, c in comps.items()}

# model -> 单件报废成本(整套)
cost_by_name = {}
for r in rows:
    n = r["model"]
    if n not in cost_by_name or to_num(cost_by_name[n].get("采购价+头程")) is None:
        cost_by_name[n] = r
excluded_nc = {e["model"] for e in scrap_res["excluded_no_clear"]}
def scrap_unit(n):
    if n in excluded_nc: return 0.0
    for r in rows:
        if r["model"] == n:
            pp = to_num(r.get("采购价+头程")) or 0.0
            w = to_num(r.get("部件重量 lbs"))
            return pp + (0.0 if w is None else (2.0 if w < 50 else 3.0))
    return 0.0
scrap_kit = {mid: sum(scrap_unit(n) for n in names) for mid, names in bom.items()}

scen_by_model = {r["model"]: r for r in scen["results"]}

print("=" * 115)
print("稀缺组件给错人了吗? —— 现行赢家 vs 被饿死者的单套边际价值对比")
print("=" * 115)

any_wrong = False
for name in sorted(need):
    users = need[name]
    if len(users) < 2: continue
    stock = stock_total.get(name, 0)
    if stock <= 0: continue
    got = {a["record_id"]: a["K"] for a in alloc["allocations"] if a["record_id"] in users}
    if sum(got.values()) < stock: continue  # 不稀缺
    # 赢家(拿到X的) 与 全体饿死者
    winners = [rid for rid, k in got.items() if k > 0]
    starvers = [rid for rid in users if got.get(rid, 0) == 0]
    if not starvers: continue

    def marginal(rid):
        """1件X给这个model的边际价值 = 单套最优可行净回收 + 整套避免报废"""
        m = mains[rid]
        price = to_num(m.get("售价")) or 0
        s30 = to_num(m.get("过去30天销量")) or 0
        fixed = to_num(m.get("固定成本合计")) or 0
        pp_first = to_num(m.get("采购价+头程")) or 0
        # u_a: 8折售价 - 固定成本 - 推广30%
        pa = price * 0.8
        u_a = pa - fixed - pa * 0.3
        u_b = price * 0.15 - pp_first
        best_u = max(u_a, u_b) if s30 > 0 else u_b  # 无销量时A路径卖不动首件
        return best_u + scrap_kit[rid], u_a, u_b

    # 饿死者是否被X卡住? 除X外其他组件的瓶颈量
    def bottleneck_ex_x(rid):
        ks = [stock_total.get(n, 0) for n in bom[rid] if n != name]
        return min(ks) if ks else 10**9

    lines = []
    winner_v = max(marginal(w)[0] for w in winners) if winners else 0
    best_starver, best_starver_v = None, -1
    for rid in starvers:
        pot = bottleneck_ex_x(rid)
        if pot <= 0: continue  # 其他组件也没货, 不是X卡住的, 没有真实争夺
        v, u_a, u_b = marginal(rid)
        if v > best_starver_v:
            best_starver, best_starver_v = rid, v
        lines.append((mains[rid]["model"], pot, v, u_a, u_b))
    if not lines: continue

    print()
    print(f"### {name} (库存={stock:.0f})")
    print(f"    现行赢家: {', '.join(mains[w]['model'] + f'(K={got[w]:.0f})' for w in winners)}  单套边际值=${winner_v:.2f}")
    print(f"    {'被饿死model':28} {'除X外瓶颈':>6} {'单套边际值$':>10} {'u_a':>8} {'u_b':>8}")
    for mn, pot, v, u_a, u_b in sorted(lines, key=lambda x: -x[2]):
        mark = " <== 若给X更值钱" if v > winner_v else ""
        print(f"    {mn:28} {pot:>6.0f} {v:>10.2f} {u_a:>8.2f} {u_b:>8.2f}{mark}")
        if v > winner_v + 0.01:
            any_wrong = True

print()
print("=" * 115)
print(f"结论: {'存在给错钱的稀缺组件(见上标记)' if any_wrong else '现行营收排序在本数据集上恰好没把边际价值分错(或差距可忽略)'}")
