# -*- coding: utf-8 -*-
"""
阶段一 + 阶段二：营收优先级 + 批次级组件池瀑布流分配 (FIFO 最老批次优先)

输入: .workbuddy/inventory_cache.json + components.json (fetch_inventory.py 产出)
输出: .workbuddy/allocation_result.json

口径 (2026-09-16 批次级重构):
- 组件池 = 库存表「独立站团队 + 海外仓」行级批次 (qty, age)，同名子model合并，不重复计数
- min(独立站, 海外仓总量) 在行级自动满足: 独立站行 ⊆ 海外仓行, 独立站量恒为约束
- 分配消耗按 FIFO 最老批次优先; 记录每个主model消耗的批次构成(供4.4计提用)
- 长版 = 池中剩余批次; 报废成本与库龄无关, 但输出剩余批次年龄结构供参考
"""
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    rows = json.loads((CACHE_DIR / "inventory_cache.json").read_text(encoding="utf-8"))
    components = json.loads((CACHE_DIR / "components.json").read_text(encoding="utf-8"))
    rec_by_id = {r["record_id"]: r for r in rows}

    # ---- BOM 树 ----
    mains = [r for r in rows if not r.get("父记录")]
    sub_of = {}
    for r in rows:
        for x in (r.get("父记录") or []):
            sub_of.setdefault(x["id"], []).append(r)

    # ---- 阶段一: 营收优先级 ----
    def revenue(r):
        s = to_num(r.get("过去30天销量")) or 0.0
        p = to_num(r.get("售价")) or 0.0
        return s * p

    ranked = sorted(mains, key=lambda r: -revenue(r))
    print(f"[阶段一] 主model数={len(ranked)}, 有营收的={sum(1 for r in ranked if revenue(r) > 0)}")

    # ---- 批次池: 每个组件名 -> 剩余批次列表 [{qty, age}] (FIFO: 最老在前) ----
    pool = {}
    for name, c in components.items():
        batches = [dict(b) for b in c["batches"] if b["qty"] > 0]
        batches.sort(key=lambda b: -b["age"])  # 最老批次在前 (FIFO)
        pool[name] = batches

    # ---- 阶段二: 瀑布分配 ----
    results = []
    for rank, m in enumerate(ranked, 1):
        subs = sub_of.get(m["record_id"], [])
        if subs:
            # BOM 型: 范围组件 = 池中仍有量的子model名(去重)
            scope_names = sorted({s["model"] for s in subs if sum(b["qty"] for b in pool.get(s["model"], [])) > 0})
            mtype = "bom"
        else:
            # 单一主记录: 自身作为组件
            scope_names = ([m["model"]] if sum(b["qty"] for b in pool.get(m["model"], [])) > 0 else [])
            mtype = "single"

        if not scope_names:
            results.append({
                "rank": rank, "record_id": m["record_id"], "model": m["model"],
                "type": mtype, "K": 0, "scope": [], "consumed": {},
            })
            continue

        # K = min(各范围组件剩余总量)
        k = min(sum(b["qty"] for b in pool[n]) for n in scope_names)
        # FIFO 消耗: 每个组件从最老批次开始扣, 记录消耗构成 (供计提)
        consumed = {}
        for n in scope_names:
            need = k
            cons = []
            for b in pool[n]:
                if need <= 0:
                    break
                take = min(b["qty"], need)
                b["qty"] -= take
                need -= take
                if take > 0:
                    cons.append({"qty": take, "age": b["age"]})
            consumed[n] = cons
        results.append({
            "rank": rank, "record_id": m["record_id"], "model": m["model"],
            "type": mtype, "K": k, "scope": scope_names, "consumed": consumed,
        })

    # ---- 长版: 池中剩余 > 0 的组件 ----
    longboards = []
    single_main_names = {m["model"] for m in mains if not sub_of.get(m["record_id"])}
    for name, batches in pool.items():
        rem = [b for b in batches if b["qty"] > 0]
        total = sum(b["qty"] for b in rem)
        if total > 0:
            longboards.append({
                "model": name,
                "remaining": total,
                "batches": rem,
                "is_single_main": name in single_main_names,
            })

    total_k = sum(x["K"] for x in results)
    print(f"[阶段二] 有齐套的主model数={sum(1 for x in results if x['K'] > 0)}, 总齐套数={total_k}")
    print(f"[长版] 剩余>0 的组件数={len(longboards)}, 总剩余件数={sum(x['remaining'] for x in longboards)}")

    (CACHE_DIR / "allocation_result.json").write_text(json.dumps({
        "allocations": results, "longboards": longboards,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] -> {CACHE_DIR / 'allocation_result.json'}")


if __name__ == "__main__":
    main()
