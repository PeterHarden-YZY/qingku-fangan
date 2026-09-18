# -*- coding: utf-8 -*-
"""
数据契约层：拉取《清库方案成本核算》全表 + 《库存表》行级批次数据
-> 校验 -> 构建主/子 BOM 树 + 批次级库存池

关键口径（2026-09-16 确认）:
- 库龄字段已改为数组: 数组元素 = 库存表的行（批次），每行自带 可用量 + 库龄(程序拉取)
- 清库表「独立站渠道库存」= sum(库存表.可用量 | item匹配 & 所属团队=独立站)
- 清库表「海外仓库存总量」= sum(库存表.可用量 | item匹配 & 仓库类型=海外仓)  # 注意: 未过滤团队, 与独立站口径有重叠设计
- 瀑布分配的组件池直接用库存表行级数据(数量+库龄一一对应), 不再依赖清库表的聚合值
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

BASE_TOKEN = "HPsJbdMCva1IersNsFQco976n7B"
MAIN_TABLE = "tblvEAMIFUSzjYmW"   # 清库方案成本核算
STOCK_TABLE = "tblNnE4WXy0esyGt"  # 库存表
CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"

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


def fetch_ndjson(table_id, out_name):
    """拉取整表为 NDJSON（自动翻页），返回 record list"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ndjson = CACHE_DIR / out_name
    all_rows = []
    offset = 0
    page = 0
    while True:
        page_path = CACHE_DIR / f"{out_name.replace('.ndjson', '')}_p{page}.ndjson"
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
        all_rows.extend(rows)
        page += 1
        if len(rows) < 2000:
            break
        offset += len(rows)
    ndjson.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows), encoding="utf-8")
    # 清理分页临时文件
    for p in CACHE_DIR.glob(f"{out_name.replace('.ndjson', '')}_p*.ndjson"):
        p.unlink(missing_ok=True)
    return all_rows


def load_two_tables(force_bypass=False):
    """拉取/加载两张表，保证「库存表快照」不早于「清库表快照」。

    背景 (2026-09-17 幽灵库存事故):
      旧逻辑允许 主表缓存(18:36) 比库存表缓存(16:49) 新 —— 库存表里那 1 件
      HNESD217W-US 在两次拉取之间被删除, 但组件池仍用旧库存批次 {qty:1,age:308},
      导致 HNESD216LW-140A-V-US 被算出 K=1 并写回线上(实际应为 0)。
    规则: --use-cache 时若 stock 缓存 mtime < main 缓存 mtime, 或 stock 缓存
      超过 STALE_HOURS(默认24h), 自动重新拉取库存表, 消除跨快照幽灵批次。
    """
    import time
    use_cache = "--use-cache" in sys.argv
    cache_main = CACHE_DIR / "inventory_cache.json"
    cache_stock = CACHE_DIR / "stock_rows_cache.json"
    stale_hours = 24.0

    def _mt(p):
        return p.stat().st_mtime if p.exists() else 0.0

    if use_cache and cache_main.exists() and cache_stock.exists():
        main_rows = json.loads(cache_main.read_text(encoding="utf-8"))
        stock_rows = json.loads(cache_stock.read_text(encoding="utf-8"))
        stock_older = _mt(cache_stock) < _mt(cache_main)
        stock_stale = (time.time() - _mt(cache_stock)) > stale_hours * 3600
        if stock_older or stock_stale:
            def _fmt(p):
                return time.strftime("%m-%d %H:%M", time.localtime(_mt(p)))
            why = []
            if stock_older:
                why.append(f"库存表缓存({_fmt(cache_stock)}) 早于主表缓存({_fmt(cache_main)})")
            if stock_stale:
                why.append(f"库存表缓存已超过 {stale_hours:.0f} 小时")
            print(f"[cache-guard] {('; '.join(why))} -> 重新拉取两张表(保证快照同代), 防幽灵批次")
            stock_rows = fetch_ndjson(STOCK_TABLE, "stock_rows_raw.ndjson")
            main_rows = fetch_ndjson(MAIN_TABLE, "inventory_raw.ndjson")
            cache_stock.write_text(json.dumps(stock_rows, ensure_ascii=False), encoding="utf-8")
            cache_main.write_text(json.dumps(main_rows, ensure_ascii=False), encoding="utf-8")
    else:
        print("[fetch] pulling 清库方案成本核算 + 库存表 ...")
        main_rows = fetch_ndjson(MAIN_TABLE, "inventory_raw.ndjson")
        stock_rows = fetch_ndjson(STOCK_TABLE, "stock_rows_raw.ndjson")
        cache_main.write_text(json.dumps(main_rows, ensure_ascii=False), encoding="utf-8")
        cache_stock.write_text(json.dumps(stock_rows, ensure_ascii=False), encoding="utf-8")
    return main_rows, stock_rows


def main():
    force_bypass = "--force-bypass" in sys.argv
    main_rows, stock_rows = load_two_tables()
    print(f"[fetch] 清库表 {len(main_rows)} 条, 库存表 {len(stock_rows)} 条")

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
    from fetch_params import load_params
    P_params = load_params(use_cache=True)
    window_days = P_params.get("window_days", 30)

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
        # 独立站海外仓批次（组件清库池）: 团队=独立站
        batches = [
            {"qty": to_num(s.get("可用量")) or 0, "age": to_num(s.get("库龄(程序拉取)")) or 0}
            for s in rows_flat if s.get("所属团队") == "独立站" and s.get("仓库类型") == "海外仓"
        ]
        # 在途批次: 库龄0, 仅到货时间 <= 窗口期
        transit_in_pool = 0
        transit_excluded = 0
        for s in rows_flat:
            if s.get("所属团队") != "独立站":
                continue
            tq = to_num(s.get("在途库存")) or 0
            if tq <= 0:
                continue
            eta = to_num(s.get("在途到货时间(天)"))
            if eta is None or eta < 0 or eta > window_days:
                transit_excluded += tq
                continue
            batches.append({"qty": tq, "age": 0})
            transit_in_pool += tq
        components[name] = {"items": sorted(items), "batches": batches}

    # ---- 单一主记录(无子) 也作为组件 ----
    for m in mains:
        subs = sub_of.get(m["record_id"], [])
        if not subs:
            it = m.get("item") or ""
            batches = []
            if it:
                batches = [
                    {"qty": to_num(s.get("可用量")) or 0, "age": to_num(s.get("库龄(程序拉取)")) or 0}
                    for s in stock_by_item.get(it, [])
                    if s.get("所属团队") == "独立站" and s.get("仓库类型") == "海外仓"
                ]
                for s in stock_by_item.get(it, []):
                    if s.get("所属团队") != "独立站":
                        continue
                    tq = to_num(s.get("在途库存")) or 0
                    if tq <= 0:
                        continue
                    eta = to_num(s.get("在途到货时间(天)"))
                    if eta is None or eta < 0 or eta > window_days:
                        continue
                    batches.append({"qty": tq, "age": 0})
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

    # ---- 工厂未发货库存 (人工填写在库存表「工厂未发货数量」, 按 item 汇总) ----
    # 单件款: 直接是可决策件数; BOM款: 各子件在库存表各自行填写, 计算层再做取整对齐
    factory = {}  # item -> 未发货数量
    factory_total = 0.0
    for s in stock_rows:
        fq = to_num(s.get("工厂未发货数量")) or 0
        if fq > 0 and s.get("item"):
            factory[s["item"]] = factory.get(s["item"], 0) + fq
            factory_total += fq
    print(f"[工厂] 库存表「工厂未发货数量」合计 {factory_total:.0f} 件, 覆盖 {len(factory)} 个 item")
    print(f"[validate] 对账差: 独立站 {n_ch} 条 / 海外仓 {n_ow} 条; 其他 {len(issues) - n_ch - n_ow} 条")
    if issues:
        for i in issues[:15]:
            print("   ", i)
        if len(issues) > 15:
            print(f"    ... 共 {len(issues)} 条")

    # ---- 幽灵组件专项校验 + 硬拦截 (2026-09-17 事故新增) ----
    # 入池方向矛盾: 组件池有批次但主表该组件全部行的「独立站渠道库存」都是 0/空
    # => 池引用了主表已不存在的库存 = 幽灵批次, 分配层会凭空凑套, 必须拦截
    main_ch_all = {}
    for r in main_rows:
        m = r.get("model")
        if m:
            main_ch_all.setdefault(m, []).append(to_num(r.get("独立站渠道库存")) or 0)
    ghosts = []
    for name, c in components.items():
        pool_qty = sum(b["qty"] for b in c["batches"])
        if pool_qty <= 0:
            continue
        vals = main_ch_all.get(name)
        if vals is None:
            continue  # 无主表行组件不在此校验(入池本身要求有行), 由对账差覆盖
        if all(v <= 0 for v in vals):
            ghosts.append(f"{name}(池内 {pool_qty:.0f} 件, 主表独立站库存全 0)")

    fatal = []
    if ghosts:
        fatal.append(f"幽灵组件 {len(ghosts)} 个(组件池有批次但主表库存全0): {'; '.join(ghosts[:5])}")
    ch_recon = [i for i in issues if "对账差" in i and "独立站" in i]
    if ch_recon:
        fatal.append(f"独立站对账矛盾 {len(ch_recon)} 条(主表与库存表快照不同代): {'; '.join(ch_recon[:3])}")
    if fatal and not force_bypass:
        print()
        print("!!" * 30)
        print("FATAL: 数据一致性校验失败, 中止构建组件池 (宁可不算, 不可算错)")
        for f_ in fatal:
            print("  -", f_)
        print("  处置: ① 无缓存重跑 `python fetch_inventory.py` 拉最新两表; "
              "② 确认数据无误后加 --force-bypass 强制通过(风险自负)")
        print("!!" * 30)
        sys.exit(2)

    # ---- 输出 ----
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
    print(f"[done] tree -> {out}, components -> {comp_out}, factory -> {fac_out}")


if __name__ == "__main__":
    main()

