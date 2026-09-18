# -*- coding: utf-8 -*-
"""
阶段四：齐套成品的混合清库分配与财务核算
输入: allocation_result.json + scrap_result.json + inventory_snapshot.json
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

from cost_helper import (
    calc_incremental_storage_fee,
    get_cost_provider,
    get_current_b_days,
)
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
    rows = json.loads((CACHE_DIR / "inventory_snapshot.json").read_text(encoding="utf-8"))
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

    cost_prov = get_cost_provider()
    b_days = get_current_b_days()

    # BOM tree lookup
    bom_tree = json.loads((CACHE_DIR / "bom_tree.json").read_text(encoding="utf-8"))
    subs_by_main = {b["model"]: b.get("subs", []) for b in bom_tree}

    # 工厂未发货库存 (item -> qty), 第四轮
    fac_path = CACHE_DIR / "factory_stock.json"
    factory = json.loads(fac_path.read_text(encoding="utf-8")) if fac_path.exists() else {}

    results = []
    for a in alloc["allocations"]:
        if a["K"] <= 0:
            continue
        m = rec_by_id[a["record_id"]]
        price = to_num(m.get("售价")) or 0.0
        s30 = to_num(m.get("过去30天销量")) or 0.0
        subs = subs_by_main.get(m.get("model"), [])
        cost_info = cost_prov.get_model_cost_and_cbm(m, subs)
        buy = cost_info["buy"]
        head = cost_info["head"]
        tariff = cost_info["tariff"]
        last = cost_info["last"]
        cbm = cost_info["cbm"]

        # 计算实际消耗批次的单件精确仓储费 (根据分配的批次库龄加权)
        # 若有消耗批次记录，按实际消耗各子组件批次年龄累进计算
        def calc_consumed_storage_fee(delta_days):
            if delta_days <= 0:
                return 0.0
            total_fee = 0.0
            for comp_name, cons in a["consumed"].items():
                # 获取该组件单件 CBM
                comp_rec = next((s for s in subs if s.get("model") == comp_name), None)
                it = comp_rec.get("item") if comp_rec else m.get("item")
                comp_cbm = cost_prov.get_item_cost_and_dim(it)["cbm"]
                if comp_cbm <= 0:
                    continue
                tot_qty = sum(c["qty"] for c in cons)
                if tot_qty <= 0:
                    continue
                fee_comp = sum(c["qty"] * calc_incremental_storage_fee(c["age"], delta_days, comp_cbm) for c in cons)
                total_fee += fee_comp / tot_qty
            if total_fee <= 0 and cbm > 0:
                # 兜底按主Model默认库龄100天计算
                total_fee = calc_incremental_storage_fee(100, delta_days, cbm)
            return total_fee

        storage_fee_a_default = calc_consumed_storage_fee(P["window_days"])
        storage_fee_b = calc_consumed_storage_fee(b_days)
        storage_fee_c = calc_consumed_storage_fee(b_days)

        # 4.0 单件细化综合成本
        # A方案综合成本 = 采购价 + 头程 + 关税 + 尾程 + 仓储费
        cost_a_total = buy + head + tariff + last + storage_fee_a_default
        # B方案综合成本 = 采购价 + 头程 + 关税 + 仓储费 (线下清库无需尾程)
        cost_b_total = buy + head + tariff + storage_fee_b

        price_a = price * (1 - P["discount_a"])
        u_a = price_a - cost_a_total - price_a * P["promo_rate"]
        u_b = price * P["discount_b"] - cost_b_total

        # C: 本渠道净回收 = -奖金/套 - 仓储费
        revenue_c_per = price * P["discount_c"]
        bonus_total_c = bonus(a["K"] * revenue_c_per) if a["K"] > 0 else 0.0
        u_c = (-bonus_total_c / a["K"] - storage_fee_c) if a["K"] > 0 else 0.0

        # ---- 4.1 全量K并列 (仅参考) ----
        full_k = {
            "A": a["K"] * u_a,
            "B": a["K"] * u_b,
            "C": -bonus_total_c - a["K"] * storage_fee_c,
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

        # 持有期计提增量 (第五轮): 窗口 W 内批次老化 -> 计提档位上移的边际成本
        # Δprov(W) = Σ组件 Σ批次 [qty × buy_i × (rate(库龄+W/2) − rate(库龄))] / K  (按窗口中点库龄计档)
        # 语义: A 路径等 W 天卖出的货, 老化半程要多charge的计提; B/C 立即出清不老化, 不受影响
        def calc_hold_provision_delta(W):
            if W <= 0 or a["K"] <= 0:
                return 0.0
            total = 0.0
            for comp_name, cons in a["consumed"].items():
                buy_c = to_num(buy_by_name.get(comp_name, {}).get("采购价")) or 0.0
                if buy_c <= 0:
                    continue
                for c in cons:
                    mid_age = c["age"] + W / 2.0
                    total += c["qty"] * buy_c * (provision_rate(mid_age) - provision_rate(c["age"]))
            return total / a["K"]

        # ---- 4.3 动态清库损失最小化组合分配 (A + C/B 全局多路径比选) ----
        # 可销量与选项评估封装为窗口 W 的函数 (供自定义窗口测算 + 最优窗口搜索复用)
        def eval_options(W):
            # 动态计算窗口 W 下的 A 方案仓储费与 u_a
            cur_storage_a = calc_consumed_storage_fee(W)
            hold_prov_delta = calc_hold_provision_delta(W)  # 持有期计提增量(第五轮)
            cur_u_a = price_a - (buy + head + tariff + last + cur_storage_a + hold_prov_delta) - price_a * P["promo_rate"]

            # 4.2 可销量: Q = 未来 W 天该售价预计可销量
            m_a = min(max(1 + P["lambda"] * P["discount_a"], P["m_min"]), P["m_max"])
            q_a = math.floor(s30 * (W / 30.0) * m_a)
            d_c = 1 - P["discount_c"]
            m_c = min(max(1 + P["lambda"] * d_c, P["m_min"]), P["m_max"])
            q_c_cap = math.floor(s30 * (W / 30.0) * m_c)

            # 单方案收益 (全量 B / 全量 C 受容量约束+超额走B)
            net_b_only = a["K"] * u_b
            k_c_full_cand = min(a["K"], q_c_cap)
            rem_full_c = a["K"] - k_c_full_cand
            bonus_c_full = bonus(k_c_full_cand * revenue_c_per) if k_c_full_cand > 0 else 0.0
            net_c_only = -bonus_c_full - k_c_full_cand * storage_fee_c + rem_full_c * u_b

            # 组合策略 (优先 A 方案卖可销量)
            k_a_cand = min(a["K"], q_a) if cur_u_a > 0 else 0
            rem = a["K"] - k_a_cand
            net_rem_b = rem * u_b
            k_c_cand = min(rem, q_c_cap)
            bonus_c_cand = bonus(k_c_cand * revenue_c_per) if k_c_cand > 0 else 0.0
            rem_after_c = rem - k_c_cand
            net_rem_c = -bonus_c_cand - k_c_cand * storage_fee_c + rem_after_c * u_b

            options = [
                ("A促销+剩余走B出清", k_a_cand * cur_u_a + net_rem_b, k_a_cand, 0, rem, 0),
                ("A促销+剩余走C激励", k_a_cand * cur_u_a + net_rem_c, k_a_cand, k_c_cand, rem_after_c, 0),
                ("全量B线下出清", net_b_only, 0, 0, a["K"], 0),
                ("全量C激励出清", net_c_only, 0, k_c_full_cand, rem_full_c, 0)
            ]
            options.sort(key=lambda x: -x[1])
            return options[0], q_a, q_c_cap, cur_storage_a

        # 自定义窗口测算 (决策口径不变, 仍按参数表 window_days)
        best, q_a, q_c_cap, final_storage_a = eval_options(P["window_days"])
        best_name, best_net, k_a, k_c, k_b, dk = best

        # 计算所选方案实际发生的分摊单件仓储费 (用于写回与报表)
        if k_a > 0 and k_b == 0 and k_c == 0:
            effective_storage_fee = final_storage_a
        elif k_b > 0 and k_a == 0 and k_c == 0:
            effective_storage_fee = storage_fee_b
        elif k_c > 0 and k_a == 0 and k_b == 0:
            effective_storage_fee = storage_fee_c
        elif a["K"] > 0:
            effective_storage_fee = (k_a * final_storage_a + k_b * storage_fee_b + k_c * storage_fee_c) / a["K"]
        else:
            effective_storage_fee = storage_fee_a_default

        # ---- 4.7 最优窗口期: 逐日扫描 W∈[1,730], 各主model净额最大(并列取最小W) ----
        # 语义: 该 model 单独决策时, 达到最高净额所需的最短销售窗口天数;
        #       净额对窗口不敏感(A路径无效/无销量)时为 None
        window_nets = {}
        for W in range(1, 731):
            window_nets[W] = eval_options(W)[0][1]
        w_max = max(window_nets.values())
        w_min = min(window_nets.values())
        if w_max - w_min < 0.005:
            best_window = None
        else:
            best_window = min(W for W, n in window_nets.items() if n >= w_max - 0.005)

        # 最优窗口 W* 下的方案说明: 同一 model 在自定义窗口与最优窗口下方案构成可能不同
        # (窗口越长 q_a 越大, A 促销覆盖越多; 仓储费+持有计提随 W 增长侵蚀 u_a)。
        # 若 W* == 参数窗口 window_days, 方案必然一致, 标注 "(即当前窗口)"
        w_opt = w_net_opt = w_ka = w_kc = w_kb = None
        w_plan_desc = ""

        # 生成精准直观的业务决策动作描述 (零数量分段自动省略, 避免"A促销(0套)"噪音)
        if k_a == a["K"]:
            best_plan_desc = f"A线上降价出清(100%覆盖)"
        else:
            segs = []
            if k_a > 0:
                segs.append(f"A促销({k_a:.0f}套)")
            if k_c > 0:
                segs.append(f"C激励({k_c:.0f}套)")
            if k_b > 0:
                segs.append(f"B出清({k_b:.0f}套)")
            if segs:
                best_plan_desc = "+".join(segs)
            else:
                best_plan_desc = f"无动作(ΔK={dk:.0f})"

        if best_window is not None:
            w_opt, w_net_opt, w_ka, w_kc, w_kb, _ = eval_options(best_window)[0]
            if w_ka == a["K"]:
                w_plan_desc = "A线上降价出清(100%覆盖)"
            else:
                segs = []
                if w_ka > 0:
                    segs.append(f"A促销({w_ka:.0f}套)")
                if w_kc > 0:
                    segs.append(f"C激励({w_kc:.0f}套)")
                if w_kb > 0:
                    segs.append(f"B出清({w_kb:.0f}套)")
                w_plan_desc = "+".join(segs) if segs else f"无动作(ΔK=0)"
            if w_plan_desc == best_plan_desc:
                w_plan_desc += "(即当前窗口)"

        bonus_c_actual = bonus(k_c * revenue_c_per) if k_c > 0 else 0.0
        provision = 0.0
        prov_detail = []
        net = best_net

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

        # 4.6 方案对比（汇报参考视图）与最终决策输出
        compare = {
            "A_降价卖": {"net": net_a_only, "sell": min(a["K"], q_a_full), "note": f"{P['window_days']:.0f}天窗口内预计可卖"},
            "B_线下清库": {"net": net_b_only, "sell": a["K"], "note": "全量出清给线下渠道"},
            "C_激励方案": {"net": net_c_only, "sell": a["K"], "note": "本渠道收入0,付奖金消化库存"},
            "最优组合": {"net": best_net, "sell": k_a + k_b + k_c + dk, "note": best_plan_desc},
        }

        # 最终最佳方案 = 具体方案+数量动作描述, 与前端插件「最佳方案」字段写回口径严格一致
        # (如 "A促销(64套)+B出清(871套,0计提)"), 不再输出笼统的「最优动态组合」标签

        # ---- 4.8 工厂未发货库存发货决策 (第四轮) ----
        # 候选发货套数 X ∈ {0, ⌊F_min/2⌋, F_min} (F_min = BOM各子件未发货量的最小值; 单件款 = 自身)
        # 口径: 只能整批发或最多除以2 (2026-09-17 用户拍板)
        # V(X) = X × u_best − Σ组件 (F_i − X) × buy_i  (发出去的赚钱, 不发的亏采购价)
        #   u_best 用该主model本轮最优单套净回收 (A/C/B 中的生效值; 无最优时取 u_b)
        # 发货成本 = 采购价+头程+关税 (货到海外仓, 无尾程/仓储已含在 u 中按批次0算)
        # 注意: 工厂发货的套数是"额外新增", 与在库 K 独立; 不与在库库存池互斥
        # 2026-09-18 迁移: factory_stock.json 键从 item 改为 model 名 (输入字段挂在主表)
        fac_result = None
        scope_names = [s.get("model") for s in subs] if subs else ([m.get("model")] if m.get("model") else [])
        item_of = {}
        for s in subs:
            item_of[s.get("model")] = s.get("item") or ""
        if not subs:
            item_of[m.get("model")] = m.get("item") or ""

        fac_qty_by_comp = {}
        for comp_name in scope_names:
            if comp_name and factory.get(comp_name, 0) > 0:
                fac_qty_by_comp[comp_name] = factory[comp_name]

        if fac_qty_by_comp:
            f_min = min(fac_qty_by_comp.values())
            # BOM 各子件必须同数量齐套发; 未填写的子件视为 0 (无法齐套) -> 整个 BOM 决策跳过
            if subs and len(fac_qty_by_comp) < len([s for s in subs if s.get("model")]):
                missing = [s.get("model") for s in subs
                           if s.get("model") and s.get("model") not in fac_qty_by_comp]
                fac_result = {
                    "decided": False, "reason": f"子件未填工厂未发货量: {','.join(missing)}",
                    "fac_qty_by_comp": fac_qty_by_comp, "f_min": f_min,
                    "ship_qty": None, "decision": "", "loss_not_ship": None,
                }
            else:
                # 单套发货成本 (BOM = 各子件之和; 单件 = 自身)
                ship_buy = sum(cost_prov.get_item_cost_and_dim(item_of.get(n) or "")["buy"]
                               for n in fac_qty_by_comp)
                ship_head = sum(cost_prov.get_item_cost_and_dim(item_of.get(n) or "")["head"]
                                for n in fac_qty_by_comp)
                ship_tariff = sum(cost_prov.get_item_cost_and_dim(item_of.get(n) or "")["tariff"]
                                  for n in fac_qty_by_comp)
                # 生效最优单套净回收 (与上面 best 选项一致)
                u_per_kit_map = {"A促销+剩余走B出清": u_a, "A促销+剩余走C激励": u_a,
                                 "全量B线下出清": u_b, "全量C激励出清": u_b}
                u_effective = u_per_kit_map.get(best_name, u_b)
                # 工厂到货批次库龄 0: B/C 仓储费按 age=0 起算
                storage_fac_b = calc_incremental_storage_fee(0, b_days, cbm)
                # 单套发货净回收 = 售价路径回收 − 发货落地成本(采+头+关) − age0 仓储费
                u_ship = u_effective - storage_fac_b  # u_effective 已含在库批次的仓储费, 用 age0 近似不重复扣
                # 更精确: 直接用路径单价重算 (发货件不占在库批次)
                price_path = price_a if "A促销" in best_name else price * P["discount_b"]
                promo = price_a * P["promo_rate"] if "A促销" in best_name else 0.0
                u_ship = price_path - promo - (ship_buy + ship_head + ship_tariff) - storage_fac_b
                if "全量C激励" in best_name:
                    rev_c = price * P["discount_c"]
                    bonus_one = bonus(rev_c) / max(1, rev_c) if rev_c > 0 else 0.0
                    u_ship = rev_c - (ship_buy + ship_head + ship_tariff) - storage_fac_b - rev_c * bonus_one

                # 不发货亏损 = Σ (F_i − X) × buy_i ; 全发货则无亏损
                def loss_not_ship(X):
                    return sum((fq - X) * cost_prov.get_item_cost_and_dim(item_of.get(n) or "")["buy"]
                               for n, fq in fac_qty_by_comp.items())

                # 候选: 0 / 半发 / 全发 (取整)
                cands = sorted({0, int(f_min // 2), int(f_min)})
                best_x, best_v = None, None
                for X in cands:
                    V = X * u_ship - loss_not_ship(X)
                    if best_v is None or V > best_v + 1e-9:
                        best_v, best_x = V, X
                # 与不发货(X=0)比: 若最优就是 0, 决策=不发货(亏采购价)
                baseline_v = -loss_not_ship(0)
                if best_x == 0:
                    decision = f"不发货(亏采购价${loss_not_ship(0):,.0f})"
                elif best_x == int(f_min):
                    decision = f"整单全发({int(f_min)}套)"
                else:
                    decision = f"半发({best_x}套)"
                fac_result = {
                    "decided": True,
                    "fac_qty_by_comp": fac_qty_by_comp, "f_min": f_min,
                    "u_ship": u_ship, "ship_qty": best_x, "decision": decision,
                    "loss_not_ship": loss_not_ship(best_x) if best_x > 0 else loss_not_ship(0),
                    "v_best": best_v, "v_baseline": baseline_v,
                    "cands": [{"X": x, "V": x * u_ship - loss_not_ship(x)} for x in cands],
                }

        results.append({
            "record_id": a["record_id"], "model": a["model"], "rank": a["rank"],
            "K": a["K"], "price": price, "s30": s30,
            "u_a": u_a, "u_b": u_b, "u_c": u_c,
            "q_a": q_a, "q_b": q_b, "q_c": q_c,
            "k_a": k_a, "k_b": k_b, "k_c": k_c, "dk": dk,
            "full_k_ref": full_k,            # 4.1 仅参考
            "bonus_c": bonus_c_actual,
            "provision": provision, "prov_detail": prov_detail,
            "net": best_net, "best_layer": best_plan_desc,
            "compare": compare,               # 4.6 方案对比
            "best_plan": best_plan_desc,      # 写回 Base 的具体动作描述(特定方案+数量)
            "best_option": best_name,         # 4 选项名, 供运行日志分组统计
            "best_net": best_net,
            "best_window": best_window,       # 4.7 最优窗口期(天), None=窗口不敏感
            "best_window_plan": w_plan_desc,  # 最优窗口下的方案说明 (与最优窗口期字段配套写回)
            "best_window_net": w_net_opt,     # 最优窗口下净额 (4.7)
            "storage_fee": effective_storage_fee,  # 单件实际核算仓储费
            "storage_fee_a": final_storage_a,
            "storage_fee_b": storage_fee_b,
            "cost_buy": buy,
            "cost_head": head,
            "cost_tariff": tariff,
            "cost_last": last,
            "factory": fac_result,          # 4.8 工厂未发货决策 (无填写的 model 为 None)
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

    # 全局最优窗口: 各主model最优窗口的众数 (窗口不敏感的 model 不参与统计)
    windows = [r["best_window"] for r in results if r.get("best_window")]
    if windows:
        from collections import Counter
        global_window, _ = Counter(windows).most_common(1)[0]
    else:
        global_window = None

    # 工厂未发货决策汇总 (decided=False 的 ⚠️ 条目无 ship_qty 等键, 排除后再统计)
    fac_all = [(r["model"], r["factory"]) for r in results if r.get("factory")]
    fac_decided = [(m, f) for m, f in fac_all if f.get("decided") and f.get("ship_qty") is not None]
    fac_ship_total = sum(f["ship_qty"] for _, f in fac_decided)
    fac_loss_total = sum(f["loss_not_ship"] or 0 for _, f in fac_decided)
    fac_v_gain = sum(f["v_best"] - f["v_baseline"] for _, f in fac_decided)

    print(f"参与测算主model数: {len(results)}, 总齐套 K={tot_k:.0f}")
    print(f"分层: A降价 {tot_ka:.0f} 套 | C激励 {tot_kc:.0f} 套 | B线下 {tot_kb:.0f} 套 | 未清库 ΔK={tot_dk:.0f}")
    print(f"计提合计: ${tot_prov:,.2f} | 报废成本: ${scrap['total_scrap_cost']:,.2f}")
    print(f"本渠道净额合计: ${tot_net:,.2f}")
    print(f"最优窗口期: 全局={global_window}天, 逐model有效值 {len(windows)}/{len(results)} 个")
    if fac_decided:
        print(f"[工厂] 有决策的 model {len(fac_decided)} 个: 发货合计 {fac_ship_total:.0f} 套 | "
              f"不发货亏损合计 ${fac_loss_total:,.2f} | 决策增量价值 ${fac_v_gain:,.2f}")
        for m, f in fac_decided:
            print(f"  {m}: {f['decision']} | u_ship=${f['u_ship']:,.2f} | V=${f['v_best']:,.0f} (不发货V=${f['v_baseline']:,.0f})")
    print()
    print(f"{'model':28} {'K':>5} {'kA':>4} {'kC':>4} {'kB':>4} {'ΔK':>4} {'net$':>11} {'W*':>4} best")
    for r in sorted(results, key=lambda x: -x["net"]):
        print(f"{r['model']:28} {r['K']:5.0f} {r['k_a']:4.0f} {r['k_c']:4.0f} {r['k_b']:4.0f} {r['dk']:4.0f} {r['net']:11,.0f} {str(r.get('best_window') or '-'):>4} {r['best_layer']}")
    diff_w = [r for r in results if r.get("best_window") and r.get("best_window_plan") and "(即当前窗口)" not in r["best_window_plan"]]
    if diff_w:
        print()
        print(f"[最优窗口方案差异] {len(diff_w)} 个 model 在最优窗口下的方案构成与当前窗口不同:")
        for r in diff_w:
            print(f"  {r['model']}: 当前({P['window_days']:.0f}天)={r['best_plan']} | 最优({r['best_window']:.0f}天)={r['best_window_plan']} (净额${r['best_window_net']:,.0f} vs ${r['net']:,.0f})")

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
            "global_best_window": global_window,
            "factory_ship_total": fac_ship_total,
            "factory_loss_total": fac_loss_total,
            "factory_v_gain": fac_v_gain,
            "factory_decided_models": len(fac_decided),
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[done] -> {CACHE_DIR / 'scenario_result.json'}")


if __name__ == "__main__":
    main()
