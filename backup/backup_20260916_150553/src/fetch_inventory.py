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


def main():
    use_cache = "--use-cache" in sys.argv
    cache_main = CACHE_DIR / "inventory_cache.json"
    cache_stock = CACHE_DIR / "stock_rows_cache.json"
    if use_cache and cache_main.exists() and cache_stock.exists():
        main_rows = json.loads(cache_main.read_text(encoding="utf-8"))
        stock_rows = json.loads(cache_stock.read_text(encoding="utf-8"))
    else:
        print("[fetch] pulling 清库方案成本核算 + 库存表 ...")
        main_rows = fetch_ndjson(MAIN_TABLE, "inventory_raw.ndjson")
        stock_rows = fetch_ndjson(STOCK_TABLE, "stock_rows_raw.ndjson")
        cache_main.write_text(json.dumps(main_rows, ensure_ascii=False), encoding="utf-8")
        cache_stock.write_text(json.dumps(stock_rows, ensure_ascii=False), encoding="utf-8")
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
    print(f"[validate] 对账差: 独立站 {n_ch} 条 / 海外仓 {n_ow} 条; 其他 {len(issues) - n_ch - n_ow} 条")
    if issues:
        for i in issues[:15]:
            print("   ", i)
        if len(issues) > 15:
            print(f"    ... 共 {len(issues)} 条")

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
    print(f"[done] tree -> {out}, components -> {comp_out}")


if __name__ == "__main__":
    main()

