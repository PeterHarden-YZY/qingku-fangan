# -*- coding: utf-8 -*-
"""模拟对比: 现行营收排序瀑布 vs 边际价值贪心 (真实数据, 同一套财务口径)
口径: 总回收 = Σ各主model(A段 k_a×u_a + B段 k_b×u_b) - 长版报废总成本
     (A段容量 Q_a 按 60 天窗口; B 段无容量限制; C 段不影响排序只在净额里体现, 两法同口径)
"""
import json, math
from pathlib import Path

C = Path(__file__).resolve().parent.parent / ".workbuddy"
rows = json.loads((C / "inventory_cache.json").read_text(encoding="utf-8"))
comps = json.loads((C / "components.json").read_text(encoding="utf-8"))

def to_num(v):
    try: return float(v)
    except (TypeError, ValueError): return None

sub_of = {}
for r in rows:
    for x in (r.get("父记录") or []):
        sub_of.setdefault(x["id"], []).append(r)
mains = [r for r in rows if not r.get("父记录")]

# 每个 model 的 BOM
bom = {}
for m in mains:
    subs = sub_of.get(m["record_id"], [])
    bom[m["record_id"]] = sorted({s["model"] for s in subs if s.get("model")}) or ([m["model"]] if m.get("model") else [])

# 参数 (与 scenario_result.json 一致)
PRICE_A_DISC = 0.20   # A 降价幅度
PROMO = 0.30
DISC_B = 0.15
W = 60                # 窗口天数
LAM, M_MIN, M_MAX = 1.0, 1.0, 3.0

def unit_econs(m):
    price = to_num(m.get("售价")) or 0.0
    fixed = to_num(m.get("固定成本合计")) or 0.0
    pp = to_num(m.get("采购价+头程")) or 0.0
    s30 = to_num(m.get("过去30天销量")) or 0.0
    pa = price * (1 - PRICE_A_DISC)
    u_a = pa - fixed - pa * PROMO
    u_b = price * DISC_B - pp
    d = PRICE_A_DISC
    mm = min(max(1 + LAM * d, M_MIN), M_MAX)
    q_a = math.floor(s30 * (W / 30.0) * mm)
    return u_a, u_b, q_a, s30

# 单件报废成本
cost_by_name = {}
for r in rows:
    n = r["model"]
    if n not in cost_by_name or to_num(cost_by_name[n].get("采购价+头程")) is None:
        cost_by_name[n] = r
def scrap_unit(n):
    rec = cost_by_name.get(n, {})
    pp = to_num(rec.get("采购价+头程"))
    w = to_num(rec.get("部件重量 lbs"))
    if pp is None or w is None:
        return None
    return pp + (2.0 if w < 50 else 3.0)

# 不清库组件
nc = set()
for r in rows:
    v = r.get("清库标记")
    if isinstance(v, list): v = v[0] if v else None
    if v and "不清库" in str(v):
        nc.add(r["model"])

scrap_kit = {}
for m in mains:
    tot, ok = 0.0, True
    for n in bom[m["record_id"]]:
        su = scrap_unit(n) if n not in nc else 0.0
        if su is None: su = 0.0  # 数据缺失按0, 两法同口径
        tot += su
    scrap_kit[m["record_id"]] = tot

def fresh_pool():
    pool = {}
    for name, c in comps.items():
        pool[name] = sum(b["qty"] for b in c["batches"])
    return pool

def evaluate(pool, ks):
    """给定分配 ks={rid: K}, 返回总净回收 (卖段 - 报废段)"""
    total = 0.0
    detail = []
    for m in mains:
        rid = m["record_id"]
        k = ks.get(rid, 0)
        u_a, u_b, q_a, s30 = unit_econs(m)
        if k > 0:
            k_a = min(k, q_a) if u_a > 0 else 0
            k_b = k - k_a
            val = k_a * u_a + k_b * u_b
            total += val
            detail.append((m["model"], k, k_a, k_b, val))
    scrap_total = 0.0
    for n, rem in pool.items():
        if rem > 0 and n not in nc:
            su = scrap_unit(n)
            if su is not None:
                scrap_total += su * rem
    total -= scrap_total
    return total, detail, scrap_total

# ---------- 方法A: 现行 营收排序瀑布 ----------
def alloc_revenue():
    pool = fresh_pool()
    ranked = sorted(mains, key=lambda m: -(to_num(m.get("过去30天销量")) or 0) * (to_num(m.get("售价")) or 0))
    ks = {}
    for i, m in enumerate(ranked, 1):
        names = bom[m["record_id"]]
        k = min([pool.get(n, 0) for n in names] or [0])
        k = max(0, k)
        for n in names:
            pool[n] -= k
        ks[m["record_id"]] = k
    return pool, ks

# ---------- 方法B: 边际价值贪心 (逐套, 下一套给当前边际价值最高的主model) ----------
def alloc_value_greedy():
    pool = fresh_pool()
    ks = {m["record_id"]: 0 for m in mains}
    econ = {m["record_id"]: unit_econs(m) for m in mains}
    # 每次给边际价值最高的一套; 边际价值<0 则不配 (直接报废更优)
    while True:
        best_rid, best_v = None, -1e18
        for m in mains:
            rid = m["record_id"]
            names = bom[rid]
            if any(pool.get(n, 0) <= 0 for n in names):
                continue
            u_a, u_b, q_a, s30 = econ[rid]
            k = ks[rid]
            path_v = u_a if (k < q_a and u_a > 0) else u_b
            v = path_v + scrap_kit[rid]
            if v > best_v:
                best_v, best_rid = v, rid
        if best_rid is None or best_v <= 0:
            break
        for n in bom[best_rid]:
            pool[n] -= 1
        ks[best_rid] += 1
    return pool, ks

pool_a, ks_a = alloc_revenue()
pool_b, ks_b = alloc_value_greedy()
tot_a, det_a, scr_a = evaluate(pool_a, ks_a)
tot_b, det_b, scr_b = evaluate(pool_b, ks_b)

print(f"现行·营收排序瀑布 : 总净回收 = ${tot_a:,.2f}  (报废成本 ${scr_a:,.2f})")
print(f"新法·边际价值贪心 : 总净回收 = ${tot_b:,.2f}  (报废成本 ${scr_b:,.2f})")
print(f"差额              : ${tot_b - tot_a:,.2f}")
print()
print("分配变化 (K 不同的 model):")
for m in mains:
    ka, kb = ks_a.get(m["record_id"], 0), ks_b.get(m["record_id"], 0)
    if ka != kb:
        print(f"  {m['model']:28} 营收法 K={ka:>4.0f}  价值法 K={kb:>4.0f}")
