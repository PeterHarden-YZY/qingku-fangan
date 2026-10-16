# -*- coding: utf-8 -*-
"""
阶段一 + 阶段二：边际价值贪心分配 (共享组件取舍规则 2026-09-16 拍板)

输入: .workbuddy/inventory_snapshot.json + components.json (fetch_inventory.py 产出)
输出: .workbuddy/allocation_result.json

分配规则 (取代旧「营收排序瀑布」):
- 逐套贪心: 每一套齐套名额给「单套边际价值」最高的主model
  单套边际价值 = 最优可行路径净回收 + 整套BOM避免的报废成本
    - 路径净回收: 已卖 < Q_a 且 u_a>0 时取 u_a (A段容量内), 否则 u_b (B段兜底)
    - 避免报废 = Σ各组件(采购价+头程 + 报废处理费); 「不清库」组件计 0
- 边际价值 <= 0 的 model 不凑套: 组件保留在池中走报废 (更优)
- 营收 (S30×售价) 仅作同分决胜与排名展示, 不再决定分配先后
- FIFO 最老批次优先扣减; 记录消耗批次构成供 4.4 计提
- 独立站批次优先, 不足借非独立站批次 (borrow 模式口径, 与前端插件一致)

输出新增:
- scarcity_decisions: 稀缺组件取舍明细 (哪个主model让位、让了多少、单套价值差)
"""
import json
from math import floor
from pathlib import Path

import fetch_params
from cost_helper import (
    calc_incremental_storage_fee,
    get_cost_provider,
    get_current_b_days,
)

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    P = fetch_params.load_params()
    rows = json.loads((CACHE_DIR / "inventory_snapshot.json").read_text(encoding="utf-8"))
    components = json.loads((CACHE_DIR / "components.json").read_text(encoding="utf-8"))
    rec_by_id = {r["record_id"]: r for r in rows}

    # ---- BOM 树 ----
    mains = [r for r in rows if not r.get("父记录")]
    sub_of = {}
    for r in rows:
        for x in (r.get("父记录") or []):
            sub_of.setdefault(x["id"], []).append(r)

    # ---- 单件报废成本 & 不清库标记 ----
    cost_by_name = {}
    for r in rows:
        n = r["model"]
        if n not in cost_by_name or to_num(cost_by_name[n].get("采购价+头程")) is None:
            cost_by_name[n] = r

    def is_no_clear(model):
        for r in rows:
            if r["model"] == model:
                v = r.get("清库标记")
                if isinstance(v, list):
                    v = v[0] if v else None
                if v and "不清库" in str(v):
                    return True
        return False

    no_clear = {n for n in components if is_no_clear(n)}

    def scrap_unit(name):
        """单件报废成本; 不清库=0; 数据缺失返回0(计提口径兜底, 不阻塞分配)"""
        if name in no_clear:
            return 0.0
        rec = cost_by_name.get(name, {})
        pp = to_num(rec.get("采购价+头程"))
        w = to_num(rec.get("部件重量 lbs"))
        if pp is None or w is None:
            return 0.0
        return pp + (P["scrap_fee_light"] if w < P["weight_threshold"] else P["scrap_fee_heavy"])

    # ---- 主model BOM 与经济参数 ----
    def scope_of(m):
        subs = sub_of.get(m["record_id"], [])
        if subs:
            return sorted({s["model"] for s in subs if s.get("model")}), "bom"
        return ([m["model"]] if m.get("model") else []), "single"

    cost_prov = get_cost_provider()
    b_days = get_current_b_days()

    econ = {}
    for m in mains:
        price = to_num(m.get("售价")) or 0.0
        subs = sub_of.get(m["record_id"], [])
        cost_info = cost_prov.get_model_cost_and_cbm(m, subs)
        buy = cost_info["buy"]
        head = cost_info["head"]
        tariff = cost_info["tariff"]
        last = cost_info["last"]
        cbm = cost_info["cbm"]

        # 默认基于平均库龄（100天）估算分配阶段的单件仓储费 (用于贪心边际价值排序)
        # 精确批次仓储费在分配消耗后由 scenarios.py 逐批次精确结算
        storage_a = calc_incremental_storage_fee(100, P["window_days"], cbm)
        storage_b = calc_incremental_storage_fee(100, b_days, cbm)

        # 细化成本合计 = 采购价 + 头程 + 关税 + 尾程 + 仓储费
        cost_a_total = buy + head + tariff + last + storage_a
        # B方案线下清库成本 = 采购价 + 头程 + 关税 + 仓储费 (无需尾程配送)
        cost_b_total = buy + head + tariff + storage_b

        s30 = to_num(m.get("过去30天销量")) or 0.0
        price_a = price * (1 - P["discount_a"])
        u_a = price_a - cost_a_total - price_a * P["promo_rate"]
        u_b = price * P["discount_b"] - cost_b_total
        m_mult = min(max(1 + P["lambda"] * P["discount_a"], P["m_min"]), P["m_max"])
        q_a = floor(s30 * (P["window_days"] / 30.0) * m_mult)
        econ[m["record_id"]] = {
            "u_a": u_a, "u_b": u_b, "q_a": q_a, "s30": s30, "price": price,
            "revenue": s30 * price,
            "cost_info": cost_info,
        }

    # ---- 批次池 (FIFO: 最老在前; borrow 模式: 独立站批次优先借调) ----
    # 入池口径: 海外仓全量批次(含非独立站), is_dtc 标记归属; 逐套扣减时独立站批次优先,
    # 不足借非独立站 (同一物理池操作杜绝超分配) —— 2026-09-16 拍板, 与前端插件一致
    allow_borrow = True
    pool = {}
    for name, c in components.items():
        batches = [dict(b) for b in c["batches"] if b["qty"] > 0]
        batches.sort(key=lambda b: (-b["age"], not b.get("is_dtc", True)))
        pool[name] = batches

    def pool_dtc(name):
        return sum(b["qty"] for b in pool.get(name, []) if b.get("is_dtc"))

    def pool_total(name):
        return sum(b["qty"] for b in pool.get(name, []))

    # ---- 边际价值贪心分配 (逐套) ----
    ks = {m["record_id"]: 0 for m in mains}
    consumed_all = {m["record_id"]: {n: [] for n in scope_of(m)[0]} for m in mains}
    # 让位追踪: 组件 -> 因边际价值低而让位的 (model, 让出套数, 其单套价值)
    yield_log = {}   # comp -> list of {model, kits, unit_value}
    guard = 0
    while True:
        guard += 1
        if guard > 2_000_000:
            raise RuntimeError("贪心循环超限")
        best_rid, best_v = None, -1e18
        for m in mains:
            rid = m["record_id"]
            names, _ = scope_of(m)
            if not names:
                continue
            if any(pool_total(n) <= 0 for n in names):
                continue
            # borrow 模式: 整套 BOM 至少一件来自独立站批次 (与前端插件口径一致)
            if allow_borrow and all(pool_dtc(n) <= 0 for n in names):
                continue
            e = econ[rid]
            k = ks[rid]
            path_v = e["u_a"] if (k < e["q_a"] and e["u_a"] > 0) else e["u_b"]
            v = path_v + sum(scrap_unit(n) for n in names)
            if v > best_v:
                best_v, best_rid = v, rid
        if best_rid is None or best_v <= 0:
            break
        best_m = next(m for m in mains if m["record_id"] == best_rid)
        names, _ = scope_of(best_m)
        # FIFO + 独立站优先扣减, 不足借非独立站 (borrow 模式), 记录消耗
        for n in names:
            need = 1
            for b in pool[n]:
                if need <= 0:
                    break
                if not b.get("is_dtc", True):
                    continue
                take = min(b["qty"], need)
                b["qty"] -= take
                need -= take
                if take > 0:
                    consumed_all[best_rid][n].append({"qty": take, "age": b["age"]})
            if allow_borrow and need > 0:
                for b in pool[n]:
                    if need <= 0:
                        break
                    if b.get("is_dtc", True):
                        continue
                    take = min(b["qty"], need)
                    b["qty"] -= take
                    need -= take
                    if take > 0:
                        consumed_all[best_rid][n].append({"qty": take, "age": b["age"]})
        ks[best_rid] += 1

    # ---- 稀缺组件取舍明细 ----
    # 共享组件: 被>=2个主model需要; 稀缺 = 分配后无剩余 (全部消耗)
    need_map = {}
    for m in mains:
        names, _ = scope_of(m)
        for n in names:
            need_map.setdefault(n, []).append(m["record_id"])
    scarcity_decisions = []
    for n, users in need_map.items():
        if len(users) < 2:
            continue
        stock = sum(b["qty"] for b in components.get(n, {}).get("batches", []))
        got = {rec_by_id[u]["model"]: ks.get(u, 0) for u in users}
        if sum(got.values()) < stock:
            continue  # 未耗尽, 无取舍
        winners = {u: ks.get(u, 0) for u in users if ks.get(u, 0) > 0}
        losers = [u for u in users if ks.get(u, 0) == 0]
        if not losers:
            continue
        scarcity_decisions.append({
            "component": n,
            "stock": stock,
            "winners": {rec_by_id[u]["model"]: k for u, k in winners.items()},
            "starved": [rec_by_id[u]["model"] for u in losers],
        })

    # ---- 输出 (兼容 scenarios.py 消费结构) ----
    # rank 仍按营收降序(展示用)
    ranked = sorted(mains, key=lambda r: (-econ[r["record_id"]]["revenue"], r["model"] or ""))
    results = []
    for rank, m in enumerate(ranked, 1):
        names, mtype = scope_of(m)
        results.append({
            "rank": rank, "record_id": m["record_id"], "model": m["model"],
            "type": mtype, "K": ks[m["record_id"]], "scope": names,
            "consumed": consumed_all[m["record_id"]],
        })

    longboards = []
    single_main_names = {m["model"] for m in mains if not sub_of.get(m["record_id"])}
    for name, batches in pool.items():
        # 长版只算独立站剩余 (borrow 借走的非独立站件不属于本渠道清库范围)
        rem_dtc = sum(b["qty"] for b in batches if b["qty"] > 0 and b.get("is_dtc"))
        if rem_dtc > 0:
            longboards.append({
                "model": name, "remaining": rem_dtc,
                "batches": [b for b in batches if b["qty"] > 0 and b.get("is_dtc")],
                "is_single_main": name in single_main_names,
            })

    total_k = sum(x["K"] for x in results)
    print(f"[阶段一/二] 主model数={len(results)}, 营收优先展示排序")
    print(f"[贪心] 有齐套的主model数={sum(1 for x in results if x['K'] > 0)}, 总齐套数={total_k}")
    print(f"[贪心] 边际价值<=0 未凑套 -> 组件进报废: 见长版清单")
    if scarcity_decisions:
        print(f"[稀缺取舍] {len(scarcity_decisions)} 个共享组件发生取舍:")
        for d in scarcity_decisions:
            w = ", ".join(f"{mn}({k:.0f}套)" for mn, k in d["winners"].items())
            print(f"  {d['component']}: {w} | 让位: {', '.join(d['starved'])}")
    print(f"[长版] 剩余>0 的组件数={len(longboards)}, 总剩余件数={sum(x['remaining'] for x in longboards)}")

    (CACHE_DIR / "allocation_result.json").write_text(json.dumps({
        "allocations": results,
        "longboards": longboards,
        "scarcity_decisions": scarcity_decisions,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] -> {CACHE_DIR / 'allocation_result.json'}")


def to_param_default(P, m, field):
    v = to_num(m.get(field))
    return v if v is not None else 0.0


if __name__ == "__main__":
    main()
