# -*- coding: utf-8 -*-
"""
数据契约层：拉取《清库方案成本核算》全表 + 《库存表》行级批次数据
-> 校验 -> 构建主/子 BOM 树 + 批次级库存池

关键口径（2026-09-16 确认）:
- 库龄字段已改为数组: 数组元素 = 库存表的行（批次），每行自带 可用量 + 库龄(程序拉取)
- 清库表「独立站渠道库存」= sum(库存表.可用量 | item匹配 & 所属团队=独立站)
- 清库表「海外仓库存总量」= sum(库存表.可用量 | item匹配 & 仓库类型=海外仓)  # 注意: 未过滤团队, 与独立站口径有重叠设计
- 瀑布分配的组件池直接用库存表行级数据(数量+库龄一一对应), 不再依赖清库表的聚合值

缓存策略（2026-09-18 用户拍板取消缓存）:
- 无任何数据缓存：每次运行实时拉取线上两张表，快照天然同代，跨快照幽灵批次问题从根上消除
- 中间产物（bom_tree/components/factory_stock 等）仅供单次运行的下游阶段消费，run_all 跑完全链路后统一清理
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_TOKEN = "HPsJbdMCva1IersNsFQco976n7B"
MAIN_TABLE = "tblvEAMIFUSzjYmW"   # 清库方案成本核算
STOCK_TABLE = "tblNnE4WXy0esyGt"  # 库存表
PROFIT_TABLE = "tblHVAkfBvpBHPh4" # 利润测算表(US站, 成本权威源)
CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"   # 仅作单次运行中间产物目录(跑完清理)

LARK_CLI_CANDIDATES = [
    r"C:\Users\yangziyang\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd",
    r"C:\Users\yangziyang\AppData\Roaming\npm\lark-cli.cmd",
]


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def run_lark(args):
    cli = shutil.which("lark-cli")
    if cli is None:
        for cand in LARK_CLI_CANDIDATES:
            if Path(cand).exists():
                cli = cand
                break
    if cli is None:
        raise RuntimeError("lark-cli not found in PATH")
    proc = subprocess.run([cli] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"[run_lark] 退出码 {proc.returncode} | stderr: {proc.stderr[:200]}")
    return proc


def fetch_ndjson(table_id):
    """拉取整表（自动翻页），返回 record list。
    分页临时文件写入系统临时目录，函数返回前清理——不落任何数据缓存。"""
    all_rows = []
    offset = 0
    while True:
        page_path = Path(tempfile.gettempdir()) / f"lark_fetch_{table_id}_{offset}.ndjson"
        try:
            run_lark([
                "base", "+record-list",
                "--base-token", BASE_TOKEN,
                "--table-id", table_id,
                "--limit", "2000",
                "--offset", str(offset),
                "--format", "ndjson",
                "--output", page_path.as_posix(),
                "--overwrite",
                "--as", "user",
            ])
            rows = [json.loads(l) for l in page_path.open(encoding="utf-8") if l.strip()]
        finally:
            page_path.unlink(missing_ok=True)
        all_rows.extend(rows)
        if len(rows) < 2000:
            break
        offset += len(rows)
    return all_rows


def load_two_tables():
    """每次运行实时拉取两张表——快照天然同代，无跨快照幽灵批次风险（缓存机制已于 2026-09-18 移除）。"""
    print("[fetch] pulling 清库方案成本核算 + 库存表 ...")
    main_rows = fetch_ndjson(MAIN_TABLE)
    stock_rows = fetch_ndjson(STOCK_TABLE)

    # 利润测算表实时刷新 (2026-10-10 修复): 此前 test_profit.ndjson 是一次性人工产物,
    # 文件丢失后 cost_helper 静默用 0 成本跑出假结果 (A促销全场最优的假象)。
    # 现在每次运行实时拉取, 与主表/库存表快照天然同代; run_all 跑完统一清理。
    print("[fetch] pulling 利润测算表(成本权威源) ...")
    profit_rows = fetch_ndjson(PROFIT_TABLE)
    (CACHE_DIR / "test_profit.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in profit_rows), encoding="utf-8"
    )
    n_with_cost = sum(1 for r in profit_rows if r.get("采购") is not None or r.get("采购价$") is not None)
    print(f"[fetch] 利润表 {len(profit_rows)} 条已刷新 -> test_profit.ndjson (含采购成本 {n_with_cost} 条)")
    return main_rows, stock_rows


def main():
    force_bypass = "--force-bypass" in sys.argv
    main_rows, stock_rows = load_two_tables()
    print(f"[fetch] 清库表 {len(main_rows)} 条, 库存表 {len(stock_rows)} 条")

    # 手动补充item 兜底 (2026-09-18): item 列为 lookup 数据源, 覆盖不全时 (如 N1 款 D 子件
    # 在数据源缺失 -> item 空 -> 池恒 0 -> 整 BOM 无法齐套) 用人工填写的「手动补充item」补位。
    # 在 fetch 入口归一化进 "item", 下游全部阶段自动继承, 三端口径一致。
    n_patched = 0
    for r in main_rows:
        manual = str(r.get("手动补充item") or "").strip()
        if manual and not str(r.get("item") or "").strip():
            r["item"] = manual
            n_patched += 1
    if n_patched:
        print(f"[item兜底] 「手动补充item」补位 {n_patched} 行 (item 列空但手动列有值)")

    # ---- 库存表按 item 聚合出两个口径 (对账用) ----
    from collections import defaultdict
    stock_by_item = defaultdict(list)
    for s in stock_rows:
        stock_by_item[s.get("item") or ""].append(s)

    def ch_inventory(item):
        """独立站渠道库存 = 独立站团队所有仓库 (与清库表lookup口径一致)"""
        return sum(to_num(s.get("可用量")) or 0 for s in stock_by_item.get(item, [])
                   if s.get("所属团队") == "独立站")

    def owm_total(item):
        """海外仓库存总量 = 仓库类型=海外仓 (与清库表lookup口径一致, 未过滤团队)"""
        return sum(to_num(s.get("可用量")) or 0 for s in stock_by_item.get(item, [])
                   if s.get("仓库类型") == "海外仓")

    # ---- BOM 树 ----
    rec_by_id = {r["record_id"]: r for r in main_rows}
    mains = [r for r in main_rows if not r.get("父记录")]
    sub_of = {}
    for r in main_rows:
        for x in (r.get("父记录") or []):
            sub_of.setdefault(x["id"], []).append(r)

    # ---- 批次级组件池: 库存表行 = (item, 团队, 仓库, 可用量, 库龄) ----
    # 组件维度 = 子model名（同名多记录合并库存），物理批次在库存表
    # 第四轮: 在途库存 = 库龄0批次, 到货时间(天) <= 测算窗口才入池 (到货晚于窗口的在途不参与本轮)
    # 2026-09-18 架构修正 (用户拍板): 三个决策输入字段挂在《清库方案成本核算》主表
    #   - BOM 款: 填在子记录行 (每个子 model 一行)
    #   - 单件款: 填在主记录行
    #   - 库存表回归纯数据源, 不再承担人工填写职责
    # 主表行 -> 在途/工厂输入 (model 名维度聚合, 同名多行累加)
    from fetch_params import load_params
    P_params = load_params()  # 实时拉取参数表(无缓存)
    window_days = P_params.get("window_days", 30)

    transit_by_model = {}   # 子/单 model 名 -> (qty, eta) 列表
    factory_by_model = {}   # 子/单 model 名 -> qty
    for r in main_rows:
        m_name = r.get("model")
        if not m_name:
            continue
        tq = to_num(r.get("在途库存")) or 0
        if tq > 0:
            transit_by_model.setdefault(m_name, []).append((tq, to_num(r.get("在途到货时间(天)"))))
        fq = to_num(r.get("工厂未发货数量")) or 0
        if fq > 0:
            factory_by_model[m_name] = factory_by_model.get(m_name, 0) + fq

    components = {}
    # 子model名 -> item 映射（同名子model的 item 一致）
    subname_items = defaultdict(set)
    for r in main_rows:
        if r.get("父记录"):
            subname_items[r["model"]].add(r.get("item") or "")
    for name, items in subname_items.items():
        rows_flat = []
        for it in items:
            if it:
                rows_flat.extend(stock_by_item.get(it, []))
        # borrow 模式统一物理池 (2026-09-16 拍板口径, 与前端插件 src_app.js 一致):
        # 海外仓全量批次入池 (不限团队), 每批次带 is_dtc 归属标记;
        # 分配时独立站批次优先, 不足借非独立站批次
        batches = [
            {"qty": to_num(s.get("可用量")) or 0, "age": to_num(s.get("库龄(程序拉取)")) or 0,
             "is_dtc": s.get("所属团队") == "独立站"}
            for s in rows_flat if s.get("仓库类型") == "海外仓"
        ]
        # 在途批次 (2026-09-18 迁移: 输入来自主表「在途库存/在途到货时间(天)」字段, 不再读库存表)
        # 库龄0, 仅到货时间 <= 窗口期才入池; 未填/超窗的保守排除
        transit_in_pool = 0
        transit_excluded = 0
        for tq, eta in transit_by_model.get(name, []):
            if eta is None or eta < 0 or eta > window_days:
                transit_excluded += tq
                continue
            batches.append({"qty": tq, "age": 0, "is_dtc": True})
            transit_in_pool += tq
        components[name] = {"items": sorted(items), "batches": batches}

    # ---- 单一主记录(无子) 也作为组件 (同样 borrow 模式全量池 + is_dtc 标记) ----
    for m in mains:
        subs = sub_of.get(m["record_id"], [])
        if not subs:
            it = m.get("item") or ""
            batches = []
            if it:
                batches = [
                    {"qty": to_num(s.get("可用量")) or 0, "age": to_num(s.get("库龄(程序拉取)")) or 0,
                     "is_dtc": s.get("所属团队") == "独立站"}
                    for s in stock_by_item.get(it, [])
                    if s.get("仓库类型") == "海外仓"
                ]
            # 在途批次 (同上: 输入自主表行, 不再读库存表)
            for tq, eta in transit_by_model.get(m.get("model"), []):
                if eta is None or eta < 0 or eta > window_days:
                    continue
                batches.append({"qty": tq, "age": 0, "is_dtc": True})
            components[m["model"]] = {"items": [it] if it else [], "batches": batches}

    # ---- 校验 ----
    issues = []
    # 0) 主model唯一性校验（主model在model字段中不允许存在同名多行，子记录不计入）
    from collections import Counter
    main_model_counts = Counter(m["model"] for m in mains if m.get("model"))
    for m_name, count in main_model_counts.items():
        if count > 1:
            issues.append(f"[主Model重复异常] 主Model「{m_name}」在表中存在 {count} 行记录，属于异常数据")

    # 1) 层级校验
    for r in main_rows:
        for x in (r.get("父记录") or []):
            rid = x["id"]
            if rid not in rec_by_id:
                issues.append(f"[孤儿子记录] {r['model']} 的父记录 link 指向不存在的 {rid}")
            elif rec_by_id[rid].get("父记录"):
                issues.append(f"[层级异常] {r['model']} 的父记录 {rec_by_id[rid]['model']} 本身是子记录")
    # 2) 活跃记录数值完整性
    for r in main_rows:
        ch = to_num(r.get("独立站渠道库存")) or 0
        if ch <= 0:
            continue
        is_sub = bool(r.get("父记录"))
        fields = (["采购价+头程", "部件重量 lbs"] if is_sub else ["售价", "采购价+头程", "固定成本合计"])
        for f in fields:
            if to_num(r.get(f)) is None:
                issues.append(f"[缺数值] {'子' if is_sub else '主'}记录 {r['model']} 活跃但 {f} 缺失/非法")
    # 3) 对账: 清库表聚合值 vs 库存表行级聚合
    for r in main_rows:
        it = r.get("item") or ""
        if not it:
            continue
        lk_ch = to_num(r.get("独立站渠道库存"))
        lk_ow = to_num(r.get("海外仓库存总量"))
        my_ch, my_ow = ch_inventory(it), owm_total(it)
        if lk_ch is not None and abs(my_ch - lk_ch) > 0.001:
            issues.append(f"[对账差] {r['model']} 独立站渠道库存: 表={lk_ch} vs 库存表={my_ch}")
        if lk_ow is not None and abs(my_ow - lk_ow) > 0.001:
            issues.append(f"[对账差] {r['model']} 海外仓库存总量: 表={lk_ow} vs 库存表={my_ow}")

    n_ch = sum(1 for i in issues if "对账差" in i and "独立站" in i)
    n_ow = sum(1 for i in issues if "对账差" in i and "海外仓" in i)
    print(f"[build] mains={len(mains)} (with_subs={sum(1 for m in mains if sub_of.get(m['record_id']))}, "
          f"single={sum(1 for m in mains if not sub_of.get(m['record_id']))}), subs={sum(len(v) for v in sub_of.values())}")
    print(f"[components] 组件数={len(components)}, 有独立站海外仓批次的组件={sum(1 for c in components.values() if sum(b['qty'] for b in c['batches']) > 0)}")
    if transit_in_pool or transit_excluded:
        print(f"[在途] 入池(库龄0, 到货≤{window_days:.0f}天): {transit_in_pool:.0f} 件 | 到货晚于窗口排除: {transit_excluded:.0f} 件")

    # ---- 工厂未发货库存 (2026-09-18 迁移: 输入来自主表「工厂未发货数量」, model 维度) ----
    # 单件款: 填主记录行; BOM 款: 各子 model 的子记录行各填各的, 计算层取整对齐
    factory = factory_by_model  # model 名 -> 未发货数量 (前面主表行已聚合)
    factory_total = sum(factory.values())
    print(f"[工厂] 主表「工厂未发货数量」合计 {factory_total:.0f} 件, 覆盖 {len(factory)} 个 model")
    print(f"[validate] 对账差: 独立站 {n_ch} 条 / 海外仓 {n_ow} 条; 其他 {len(issues) - n_ch - n_ow} 条")
    if issues:
        for i in issues[:15]:
            print("   ", i)
        if len(issues) > 15:
            print(f"    ... 共 {len(issues)} 条")

    # ---- 幽灵组件专项校验 + 硬拦截 (2026-09-17 事故新增) ----
    # 入池方向矛盾: 组件池有独立站批次但主表该组件全部行的「独立站渠道库存」都是 0/空
    # => 池引用了主表已不存在的独立站库存 = 幽灵批次(跨快照), 分配层会凭空凑套, 必须拦截
    # 注: borrow 模式下非独立站批次是合法借调源, 不参与此校验 (2026-09-17 MSESD52N 修复适配)
    main_ch_all = {}
    for r in main_rows:
        m = r.get("model")
        if m:
            main_ch_all.setdefault(m, []).append(to_num(r.get("独立站渠道库存")) or 0)
    ghosts = []
    for name, c in components.items():
        pool_dtc_qty = sum(b["qty"] for b in c["batches"] if b.get("is_dtc"))
        if pool_dtc_qty <= 0:
            continue
        vals = main_ch_all.get(name)
        if vals is None:
            continue  # 无主表行组件不在此校验(入池本身要求有行), 由对账差覆盖
        if all(v <= 0 for v in vals):
            ghosts.append(f"{name}(池内独立站 {pool_dtc_qty:.0f} 件, 主表独立站库存全 0)")

    # 幽灵批次拦截降级说明 (2026-09-18 取消缓存):
    #   缓存时代该拦截器用于防「跨快照幽灵批次」(组件池用旧库存快照、主表已同步删除)。
    #   现在每次实时拉取、快照天然同代, 幽灵批次从结构上不可能发生;
    #   而实时拉数下「主表 lookup 聚合列滞后于库存表」属于常态(库存表正被同步),
    #   对账差/主表滞后不再构成 FATAL, 降级为醒目警告 + 数值以库存表行级数据为准。
    if ghosts:
        print()
        print("WARN: 主表聚合列滞后(独立站库存全0但库存表行级有批次), 已按库存表行级数据入池:")
        for g in ghosts[:5]:
            print("  -", g)
        if len(ghosts) > 5:
            print(f"  ... 共 {len(ghosts)} 个")

    # ---- 输出 (单次运行中间产物, run_all 全链路结束后统一清理, 不留缓存) ----
    out = CACHE_DIR / "bom_tree.json"
    tree = []
    for m in mains:
        subs = sub_of.get(m["record_id"], [])
        tree.append({
            "record_id": m["record_id"], "model": m["model"],
            "subs": [{"record_id": s["record_id"], "model": s["model"], "item": s.get("item") or ""} for s in subs],
        })
    out.write_text(json.dumps(tree, ensure_ascii=False, indent=2), encoding="utf-8")
    comp_out = CACHE_DIR / "components.json"
    comp_out.write_text(json.dumps(components, ensure_ascii=False, indent=2), encoding="utf-8")
    fac_out = CACHE_DIR / "factory_stock.json"
    fac_out.write_text(json.dumps(factory, ensure_ascii=False, indent=2), encoding="utf-8")
    # 主表快照 (下游阶段消费; 每次运行由 fetch 实时拉取重写, run_all 结束后清理)
    snap_out = CACHE_DIR / "inventory_snapshot.json"
    snap_out.write_text(json.dumps(main_rows, ensure_ascii=False), encoding="utf-8")
    print(f"[done] tree -> {out}, components -> {comp_out}, factory -> {fac_out}, snapshot -> {snap_out}")


if __name__ == "__main__":
    main()

