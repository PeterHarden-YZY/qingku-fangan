# -*- coding: utf-8 -*-
"""
阶段三：长版识别与强锁报废
输入: .workbuddy/allocation_result.json (allocation.py 产出) + inventory_snapshot.json
输出: .workbuddy/scrap_result.json

规则 (rules/agent.md 阶段三):
- 虚拟库存池分配完毕后剩余数量 > 0 的组件 -> 长版死库存
- 强制施加方案「组件报废」, 不得进入降价或线下清库路径
- Scrap_Cost = SUM( Remaining_i × Unit_Scrap_Cost_i )
- Unit_Scrap_Cost_i = (采购价+头程)_i + 报废处理费_i
- 报废处理费: 单品重量 < 50 lbs -> $2.00/件; >= 50 lbs -> $3.00/件
- 报废数量与报废成本挂载在子model或单一主记录维度
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
    from fetch_params import load_params
    P = load_params()
    alloc = json.loads((CACHE_DIR / "allocation_result.json").read_text(encoding="utf-8"))
    rows = json.loads((CACHE_DIR / "inventory_snapshot.json").read_text(encoding="utf-8"))

    # model名 -> 单件成本字段 (同名子model多记录取第一条有效值; 已验证同名记录数值一致)
    cost_by_name = {}
    for r in rows:
        n = r["model"]
        if n not in cost_by_name or to_num(cost_by_name[n].get("采购价+头程")) is None:
            cost_by_name[n] = r

    # 「清库标记」= 不清库 的组件: 仍参与主model齐套分配, 但长版剩余不进报废核算
    # (单选项「不清库」选中即排除; 留空 = 正常清库, 兼容旧缓存无该字段)
    def is_no_clear(model):
        for r in rows:
            if r["model"] == model:
                v = r.get("清库标记")
                if isinstance(v, list):
                    v = v[0] if v else None
                if v and "不清库" in str(v):
                    return True
        return False

    scrap_items = []
    excluded = []   # 不清库被排除的组件 (供 writeback 清理旧报废值)
    total_cost = 0.0
    for lb in alloc["longboards"]:
        if is_no_clear(lb["model"]):
            excluded.append({"model": lb["model"], "qty": lb["remaining"]})
            continue
        rec = cost_by_name.get(lb["model"], {})
        pp_first = to_num(rec.get("采购价+头程")) or 0.0
        w = to_num(rec.get("部件重量 lbs"))
        fee = None if w is None else (P["scrap_fee_light"] if w < P["weight_threshold"] else P["scrap_fee_heavy"])
        unit = None if fee is None else pp_first + fee
        cost = None if unit is None else unit * lb["remaining"]
        if cost is not None:
            total_cost += cost
        scrap_items.append({
            "model": lb["model"],
            "record_id": rec.get("record_id"),   # 写回挂载点
            "is_single_main": lb["is_single_main"],
            "scrap_qty": lb["remaining"],
            "purchase_first_leg": pp_first,
            "weight_lbs": w,
            "disposal_fee": fee,
            "unit_scrap_cost": unit,
            "scrap_cost": cost,                  # None = 数据缺失, 无法核算
            "batches": lb["batches"],            # 批次年龄结构(参考)
        })

    computable = [s for s in scrap_items if s["scrap_cost"] is not None]
    print(f"[长版报废] 组件数={len(scrap_items)}, 总件数={sum(s['scrap_qty'] for s in scrap_items):.0f}")
    if excluded:
        print(f"[不清库排除] {len(excluded)} 个组件: " + ", ".join(f"{e['model']}({e['qty']:.0f}件)" for e in excluded))
    print(f"[可核算] {len(computable)}/{len(scrap_items)}; 总报废成本 = ${total_cost:,.2f}")
    for s in scrap_items:
        flag = "" if s["scrap_cost"] is not None else "  <-- 数据缺失(重量/采购价)"
        print(f"  {s['model']}: {s['scrap_qty']:.0f} 件 × (采+头 ${s['purchase_first_leg']:.2f} + 处理费 ${s['disposal_fee']}) = ${s['scrap_cost'] if s['scrap_cost'] is not None else float('nan'):,.2f}{flag}")

    (CACHE_DIR / "scrap_result.json").write_text(json.dumps({
        "params": {"scenario": P["scenario"], "scrap_fee_light": P["scrap_fee_light"],
                   "scrap_fee_heavy": P["scrap_fee_heavy"], "weight_threshold": P["weight_threshold"]},
        "items": scrap_items, "total_scrap_cost": total_cost,
        "excluded_no_clear": excluded,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] -> {CACHE_DIR / 'scrap_result.json'}")


if __name__ == "__main__":
    main()
