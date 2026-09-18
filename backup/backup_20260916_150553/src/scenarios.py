# -*- coding: utf-8 -*-
"""
阶段四：齐套成品的混合清库分配与财务核算
输入: allocation_result.json + scrap_result.json + inventory_cache.json
输出: scenario_result.json

规则 (rules/agent.md 阶段四, 参数已与用户确认 2026-09):
- A 降价卖: 降价售价 = 原价 × (1 - 降价幅度, 默认20%)
    u_A = 降价售价 - 固定成本合计 - 降价售价 × 30%(推广费)
- B 线下清库: 1.5折
    u_B = 原价 × 0.15 - (采购价+头程)
- C 激励方案: 促销售价 8折; 本渠道收入记 0, 净回收 = -运营团队奖金
    奖金按「呆滞采购金额」分档: ≤20万: 1% | 20-50万: 1.5% | >50万: 2% (对营业收入)
    营业收入 = 套数 × 原价 × 0.8
- 4.2 可销量: Q = floor(S30 × M), M = clamp(1 + λ×d, 1.0, 3.0), λ=1.0
- 4.3 需求分层瀑布: 按实际售价从高到低, k1=min(K,Q1), k2=min(K-k1,Q2-Q1), k3=min(K-k1-k2,Q3-Q2)
    ΔK = K - (k1+k2+k3) -> 进入计提
- 4.4 计提: 按消耗批次库龄分档 121-180天:采购价×30% | 181-270天:采购价×80% | >270天:采购价×150%
    (计提基数 = 采购价, 需从「采购价+头程」拆分; 逐组件按 ΔK 与该主model消耗批次的库龄结构计提)
- 4.5 本渠道净额 = k_A×u_A + k_B×u_B - 奖金_C - 计提费用
"""
import json
import math
from pathlib import Path

from fetch_params import load_params

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def make_bonus(P):
    """奖金函数工厂: 分档边界与比例来自参数表 (档位金额单位: 万USD -> 乘1万)"""
    t1 = P["bonus_t1_cap"] * 10_000
    t2 = P["bonus_t2_cap"] * 10_000
    r1, r2, r3 = P["bonus_r1"], P["bonus_r2"], P["bonus_r3"]

    def bonus(revenue):
        if revenue <= t1:
            return revenue * r1
        if revenue <= t2:
            return (revenue - t1) * r2 + t1 * r1
        return (revenue - t2) * r3 + (t2 - t1) * r2 + t1 * r1

    return bonus


def make_provision_rate(P):
    """计提函数工厂: 库龄分界与比例来自参数表"""
    a1, a2 = P["prov_age1"], P["prov_age2"]
    r1, r2, r3 = P["prov_rate1"], P["prov_rate2"], P["prov_rate3"]

    def provision_rate(age):
        if age <= a1:
            return 0.0
        if age <= a2:
            return r1
        if age <= 270:
            return r2
        return r3

    return provision_rate


def main():
    P = load_params()  # 全部参数从 Base 参数表读取
    bonus = make_bonus(P)
    provision_rate = make_provision_rate(P)
    alloc = json.loads((CACHE_DIR / "allocation_result.json").read_text(encoding="utf-8"))
    rows = json.loads((CACHE_DIR / "inventory_cache.json").read_text(encoding="utf-8"))
    rec_by_id = {r["record_id"]: r for r in rows}
    # model名 -> 采购价(不含头程), 用于计提
    buy_by_name = {}
    for r in rows:
        n = r["model"]
        if n not in buy_by_name or to_num(buy_by_name[n].get("采购价")) is None:
            buy_by_name[n] = r
    # model名 -> 成本记录(采+头/重量), 用于整套报废测算
    cost_by_name = {}
    for r in rows:
        n = r["model"]
        if n not in cost_by_name or to_num(cost_by_name[n].get("采购价+头程")) is None:
            cost_by_name[n] = r

    results = []
    for a in alloc["allocations"]:
        if a["K"] <= 0:
            continue
        m = rec_by_id[a["record_id"]]
        price = to_num(m.get("售价")) or 0.0
        fixed = to_num(m.get("固定成本合计")) or 0.0     # 变动销售费用
        pp_first = to_num(m.get("采购价+头程")) or 0.0
        s30 = to_num(m.get("过去30天销量")) or 0.0

        # ---- 4.0 单件净回收 (参数来自参数表) ----
        price_a = price * (1 - P["discount_a"])
        u_a = price_a - fixed - price_a * P["promo_rate"]
        u_b = price * P["discount_b"] - pp_first
        # C: 本渠道净回收 = -奖金/套 (收入记0)
        revenue_c_per = price * P["discount_c"]
        bonus_total_c = bonus(a["K"] * revenue_c_per) if a["K"] > 0 else 0.0
        u_c = -bonus_total_c / a["K"] if a["K"] > 0 else 0.0

        # ---- 4.1 全量K并列 (仅参考) ----
        full_k = {
            "A": a["K"] * u_a,
            "B": a["K"] * u_b,
            "C": -bonus_total_c,
        }

        # ---- 4.2 可销量: Q = 未来 P["window_days"] 天该售价预计可销量 ----
        # S30 = 过去30天销量 -> 日均 = S30/30; 放大系数 M 按折扣深度调整需求
        def sellable_q(sell_price):
            d = (price - sell_price) / price if price > 0 else 0.0
            mm = min(max(1 + P["lambda"] * d, P["m_min"]), P["m_max"])
            return math.floor(s30 * (P["window_days"] / 30.0) * mm)

        q_a = sellable_q(price_a)
        q_b = sellable_q(price * P["discount_b"])
        q_c = sellable_q(price * P["discount_c"])

        # ---- 4.3 需求分层瀑布 (按实际售价降序) ----
        layers = sorted(
            [("A", price_a, q_a, u_a), ("C", price * P["discount_c"], q_c, u_c), ("B", price * P["discount_b"], q_b, u_b)],
            key=lambda x: -x[1]
        )
        remaining_k = a["K"]
        prev_q = 0
        k_layers = {}
        for name, sp, q, u in layers:
            k_i = max(0, min(remaining_k, q - prev_q))
            k_layers[name] = k_i
            remaining_k -= k_i
            prev_q = max(prev_q, q)
        dk = remaining_k  # 未清库量 ΔK

        # ---- 4.4 计提 (ΔK 按消耗批次结构, 基数=采购价) ----
        # BOM 1:1: 每个范围组件都有 ΔK 件未卖出(不是按比例分摊!)
        # 未清库物理上对应消耗批次的尾部(最年轻批次), FIFO 精确分配
        provision = 0.0
        prov_detail = []
        if dk > 0:
            for comp_name, cons in a["consumed"].items():
                rec = buy_by_name.get(comp_name, {})
                buy = to_num(rec.get("采购价")) or 0.0
                need = dk
                # 从最年轻批次开始分配 ΔK (消耗列表按老→新排列, 尾部最年轻)
                for c in reversed(cons):
                    if need <= 0:
                        break
                    take = min(c["qty"], need)
                    need -= take
                    rate = provision_rate(c["age"])
                    p = take * buy * rate
                    if p != 0:
                        provision += p
                        prov_detail.append({
                            "component": comp_name, "qty": take, "age": c["age"],
                            "rate": rate, "buy_price": buy, "provision": p,
                        })

        # ---- 4.5 本渠道净额 ----
        k_a, k_b, k_c = k_layers["A"], k_layers["B"], k_layers["C"]
        bonus_c_actual = bonus(k_c * revenue_c_per) if k_c > 0 else 0.0
        net = k_a * u_a + k_b * u_b - bonus_c_actual - provision

        # ---- 4.6 方案对比: 各路径单独执行 vs 混合, 求净额最优 ----
        def provision_on(qty):
            """qty 套未清库的计提(复用消耗批次尾部)"""
            p = 0.0
            for comp_name, cons in a["consumed"].items():
                buy = to_num(buy_by_name.get(comp_name, {}).get("采购价")) or 0.0
                need = qty
                for c in reversed(cons):
                    if need <= 0:
                        break
                    take = min(c["qty"], need)
                    need -= take
                    p += take * buy * provision_rate(c["age"])
            return p

        # 整套报废成本(K套全部报废): 每套=各组件(采+头+处理费)之和
        scrap_unit_total = 0.0
        scrap_data_ok = True
        for comp_name in a["consumed"]:
            rec_c = cost_by_name.get(comp_name, {})
            pp = to_num(rec_c.get("采购价+头程"))
            w = to_num(rec_c.get("部件重量 lbs"))
            if pp is None or w is None:
                scrap_data_ok = False
                break
            scrap_unit_total += pp + (P["scrap_fee_light"] if w < P["weight_threshold"] else P["scrap_fee_heavy"])

        # 各单方案净额: 卖得掉的按 u 算, 卖不掉的计提; B/C 假设全量出清
        q_a_full = sellable_q(price_a)
        net_a_only = min(a["K"], q_a_full) * u_a - provision_on(max(0, a["K"] - q_a_full))
        net_b_only = a["K"] * u_b
        net_c_only = -bonus(a["K"] * revenue_c_per)
        net_scrap = -a["K"] * scrap_unit_total if scrap_data_ok else None
        net_mix = net  # 4.3 混合瀑布结果

        compare = {
            "A_降价卖": {"net": net_a_only, "sell": min(a["K"], q_a_full), "note": f"{P['window_days']:.0f}天窗口内预计可卖"},
            "B_线下清库": {"net": net_b_only, "sell": a["K"], "note": "全量出清给线下渠道"},
            "C_激励方案": {"net": net_c_only, "sell": a["K"], "note": "本渠道收入0,付奖金消化库存"},
            "D_整套报废": {"net": net_scrap, "sell": a["K"], "note": "物理处置" if scrap_data_ok else "数据缺失"},
            "混合(A+B)": {"net": net_mix, "sell": k_a + k_b + k_c, "note": "需求分层瀑布"},
        }
        best_plan_name = max(
            (k for k in compare if compare[k]["net"] is not None),
            key=lambda k: compare[k]["net"]
        )
        best_net = compare[best_plan_name]["net"]

        # 最佳方案 = 分层结果里卖量最大的层
        best = max(k_layers, key=lambda x: k_layers[x]) if any(k_layers.values()) else "无"

        results.append({
            "record_id": a["record_id"], "model": a["model"], "rank": a["rank"],
            "K": a["K"], "price": price, "s30": s30,
            "u_a": u_a, "u_b": u_b, "u_c": u_c,
            "q_a": q_a, "q_b": q_b, "q_c": q_c,
            "k_a": k_a, "k_b": k_b, "k_c": k_c, "dk": dk,
            "full_k_ref": full_k,            # 4.1 仅参考
            "bonus_c": bonus_c_actual,
            "provision": provision, "prov_detail": prov_detail,
            "net": net, "best_layer": best,
            "compare": compare,               # 4.6 方案对比
            "best_plan": best_plan_name,      # 净额最优方案
            "best_net": best_net,
        })

    # ---- 汇总 ----
    tot_k = sum(r["K"] for r in results)
    tot_ka = sum(r["k_a"] for r in results)
    tot_kb = sum(r["k_b"] for r in results)
    tot_kc = sum(r["k_c"] for r in results)
    tot_dk = sum(r["dk"] for r in results)
    tot_net = sum(r["net"] for r in results)
    tot_prov = sum(r["provision"] for r in results)
    scrap = json.loads((CACHE_DIR / "scrap_result.json").read_text(encoding="utf-8"))

    print(f"参与测算主model数: {len(results)}, 总齐套 K={tot_k:.0f}")
    print(f"分层: A降价 {tot_ka:.0f} 套 | C激励 {tot_kc:.0f} 套 | B线下 {tot_kb:.0f} 套 | 未清库 ΔK={tot_dk:.0f}")
    print(f"计提合计: ${tot_prov:,.2f} | 报废成本: ${scrap['total_scrap_cost']:,.2f}")
    print(f"本渠道净额合计: ${tot_net:,.2f}")
    print()
    print(f"{'model':28} {'K':>5} {'kA':>4} {'kC':>4} {'kB':>4} {'ΔK':>4} {'net$':>11} best")
    for r in sorted(results, key=lambda x: -x["net"]):
        print(f"{r['model']:28} {r['K']:5.0f} {r['k_a']:4.0f} {r['k_c']:4.0f} {r['k_b']:4.0f} {r['dk']:4.0f} {r['net']:11,.0f} {r['best_layer']}")

    (CACHE_DIR / "scenario_result.json").write_text(json.dumps({
        "params": {"scenario": P["scenario"],
                   "discount_a": P["discount_a"], "discount_b": P["discount_b"], "discount_c": P["discount_c"],
                   "promo_rate": P["promo_rate"], "lambda": P["lambda"], "m_bounds": [P["m_min"], P["m_max"]],
                   "window_days": P["window_days"]},
        "results": results,
        "summary": {
            "total_k": tot_k, "total_k_a": tot_ka, "total_k_b": tot_kb, "total_k_c": tot_kc,
            "total_dk": tot_dk, "total_provision": tot_prov,
            "total_scrap_cost": scrap["total_scrap_cost"],
            "net_channel_total": tot_net,
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] -> {CACHE_DIR / 'scenario_result.json'}")


if __name__ == "__main__":
    main()
