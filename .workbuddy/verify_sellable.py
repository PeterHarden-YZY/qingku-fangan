# -*- coding: utf-8 -*-
"""验证: 新算法分配的套数, 有多少有真实可卖出依据?
分层: A段(S30真实销量推算) / C段(8折价S30推算) / B段(线下1.5折, 无容量约束假设)
并对比旧算法在同口径下的依据强度
"""
import json, math
from pathlib import Path

C = Path(__file__).resolve().parent.parent / ".workbuddy"
rows = json.loads((C / "inventory_cache.json").read_text(encoding="utf-8"))
P = json.loads((C / "params_cache.json").read_text(encoding="utf-8"))
new = json.loads((C / "scenario_result.json").read_text(encoding="utf-8"))       # 贪心(新)
old = json.loads((C / "scenario_revenue_run.json").read_text(encoding="utf-8"))  # 营收瀑布(旧)

def to_num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

rec_by_id = {r["record_id"]: r for r in rows}

def report(tag, scen):
    print(f"\n{'='*100}")
    print(f"{tag}: 套数可卖出依据分层")
    print(f"{'='*100}")
    tot_a = tot_c = tot_b = 0
    b_pos = b_neg = 0
    b_neg_units = 0
    print(f"{'model':26} {'S30':>5} {'q_a':>5} {'k_a':>4} {'q_c':>5} {'k_c':>4} {'k_b':>5} {'u_b':>8}  B段依据")
    for r in sorted(scen["results"], key=lambda x: -x["K"]):
        if r["K"] <= 0: continue
        m = rec_by_id[r["record_id"]]
        s30 = to_num(m.get("过去30天销量")) or 0
        price = to_num(m.get("售价")) or 0
        pp = to_num(m.get("采购价+头程")) or 0
        u_b = price * P["discount_b"] - pp
        # A段依据: k_a <= q_a (S30 推算容量)
        # C段依据: k_c <= q_c (S30 推算容量)
        # B段: 无容量约束 (假设线下1.5折无限收货)
        tot_a += r["k_a"]; tot_c += r["k_c"]; tot_b += r["k_b"]
        if r["k_b"] > 0:
            if u_b >= 0: b_pos += r["k_b"]
            else: b_neg += r["k_b"]; b_neg_units += 1
        flag = ""
        if r["k_b"] > 0 and u_b < 0:
            flag = "负毛利兜底"
        print(f"{r['model']:26} {s30:>5.0f} {r['q_a']:>5.0f} {r['k_a']:>4.0f} {r['q_c']:>5.0f} {r['k_c']:>4.0f} {r['k_b']:>5.0f} {u_b:>8.2f}  {flag}")
    print(f"\n合计: K={tot_a+tot_c+tot_b:.0f}")
    print(f"  A段 {tot_a:.0f} 套 — 依据: 过去30天真实销量×{P['window_days']:.0f}天窗口×折扣放大 (k_a≤q_a 逐model满足)")
    print(f"  C段 {tot_c:.0f} 套 — 依据: 同上, 8折价预计可销量 (k_c≤q_c)")
    print(f"  B段 {tot_b:.0f} 套 — 无容量约束, 依赖「线下1.5折无限收货」假设")
    print(f"      其中正毛利(u_b≥0): {b_pos:.0f} 套 | 负毛利(u_b<0): {b_neg:.0f} 套 (来自 {b_neg_units} 个model)")
    return tot_a, tot_c, tot_b, b_pos, b_neg

na, nc, nb, npos, nneg = report("新算法·边际价值贪心", new)
oa, oc, ob, opos, oneg = report("旧算法·营收排序瀑布", old)

print(f"\n{'='*100}")
print("对照结论")
print(f"{'='*100}")
print(f"有真实销量依据的套数 (A+C):  旧 {oa+oc:.0f} 套 -> 新 {na+nc:.0f} 套")
print(f"依赖线下无限收货假设的套数 (B): 旧 {ob:.0f} 套 -> 新 {nb:.0f} 套 (其中负毛利: 旧 {oneg:.0f} -> 新 {nneg:.0f})")
