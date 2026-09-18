# -*- coding: utf-8 -*-
"""
写回 Base + 财务汇总报告
用法:
    python writeback.py --dry-run   # 预览写回内容(默认)
    python writeback.py --apply     # 实际写回飞书多维表格
输出字段(若不存在则创建):
    实际齐套数 / 最佳方案 / 预计回收资金 / 报废数量 / 报废成本
写回规则:
    处理方案(多选) = 按分层结果写入选项: 降价售卖/线下清库/激励方案/报废
    主model行: 齐套结果(实际齐套数/最佳方案/预计回收资金)
    子model或单一主记录: 报废数量/报废成本
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"
BASE_TOKEN = "HPsJbdMCva1IersNsFQco976n7B"
MAIN_TABLE = "tblvEAMIFUSzjYmW"

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
        raise RuntimeError("lark-cli not found")
    return subprocess.run([cli] + args, capture_output=True, text=True, encoding="utf-8")


def ensure_fields():
    """确保输出字段存在, 返回 字段名->field_id 映射"""
    d = json.loads(run_lark(["base", "+field-list",
        "--base-token", BASE_TOKEN, "--table-id", MAIN_TABLE, "--as", "user"]).stdout)
    existing = {f["name"]: f["id"] for f in d["data"]["fields"]}
    wanted = {
        "实际齐套数": {"type": "number"},
        "最佳方案": {"type": "text"},
        "预计回收资金": {"type": "number"},
        "报废数量": {"type": "number"},
        "报废成本": {"type": "number"},
    }
    for name, spec in wanted.items():
        if name not in existing:
            print(f"[field] creating missing field: {name}")
            out = run_lark(["base", "+field-create",
                "--base-token", BASE_TOKEN, "--table-id", MAIN_TABLE,
                "--json", json.dumps([{"name": name, **spec}]), "--as", "user"])
            resp = json.loads(out.stdout)
            if resp.get("ok"):
                fid = resp["data"]["field"]["id"]
                existing[name] = fid
                print(f"[field] created {name} -> {fid}")
            else:
                raise RuntimeError(f"field create failed for {name}: {out.stdout[:300]}")
    return existing


def build_updates():
    """构造写回 payload: record_id -> {字段: 值}
    注意: 表中存在同一个 model 的多条记录行（例如重复的主记录或子记录），
    必须将测算结果广播更新到该 model 名下的所有对应行，防止遗漏。
    """
    scen = json.loads((CACHE_DIR / "scenario_result.json").read_text(encoding="utf-8"))
    scrap = json.loads((CACHE_DIR / "scrap_result.json").read_text(encoding="utf-8"))
    rows = json.loads((CACHE_DIR / "inventory_cache.json").read_text(encoding="utf-8"))

    # model名 -> 该主model在表里的全部主记录行 record_id 列表
    main_rids_by_model = {}
    # model名 -> 该子model/单一主记录在表里的全部记录行 record_id 列表
    comp_rids_by_model = {}

    for r in rows:
        m_name = r.get("model")
        if not m_name:
            continue
        if not r.get("父记录"):
            main_rids_by_model.setdefault(m_name, []).append(r["record_id"])
        comp_rids_by_model.setdefault(m_name, []).append(r["record_id"])

    updates = {}
    # 1) 主model: 齐套结果 (最佳方案 = 净额最优方案, 预计回收资金 = 最优方案净额)
    for r in scen["results"]:
        plan = []
        if r["k_a"] > 0: plan.append("降价售卖")
        if r["k_c"] > 0: plan.append("激励方案")
        if r["k_b"] > 0: plan.append("线下清库")
        upd = {
            "实际齐套数": r["K"],
            "最佳方案": r["best_plan"],
            "预计回收资金": round(r["best_net"], 2),
        }
        if plan:
            # 本轮可卖: 写入分层方案
            upd["处理方案"] = plan
        elif r["dk"] > 0:
            # 未清库(计提): 不动处理方案字段 —— 物理仍在仓, 非报废
            pass

        target_rids = main_rids_by_model.get(r["model"], [r["record_id"]])
        for rid in target_rids:
            updates[rid] = dict(upd)

    # 2) 长版报废: 挂载子model或单一主记录（广播到同名组件的所有记录行）
    for s in scrap["items"]:
        if s["scrap_cost"] is None:
            continue
        scrap_upd = {
            "报废数量": s["scrap_qty"],
            "报废成本": round(s["scrap_cost"], 2),
        }
        target_rids = comp_rids_by_model.get(s["model"], [s["record_id"]])
        for rid in target_rids:
            if rid in updates:
                updates[rid].update(scrap_upd)
                curr_plan = updates[rid].get("处理方案", [])
                if "报废" not in curr_plan:
                    updates[rid]["处理方案"] = curr_plan + ["报废"]
            else:
                updates[rid] = {
                    "报废数量": s["scrap_qty"],
                    "报废成本": round(s["scrap_cost"], 2),
                    "处理方案": ["报废"],
                }
    return updates


def main():
    dry = "--apply" not in sys.argv
    updates = build_updates()
    print(f"[payload] {len(updates)} 条记录待写回")

    if dry:
        rows = json.loads((CACHE_DIR / "inventory_cache.json").read_text(encoding="utf-8"))
        rec_by_id = {r["record_id"]: r for r in rows}
        print(f"{'model':30} {'处理方案':20} {'齐套':>5} {'净额$':>10} {'报废量':>6} {'报废$':>9}")
        for rid, u in list(updates.items())[:40]:
            print(f"{rec_by_id[rid]['model']:30} {'/'.join(u['处理方案']):20} "
                  f"{u.get('实际齐套数', 0) or 0:5.0f} {u.get('预计回收资金', 0) or 0:10,.0f} "
                  f"{u.get('报废数量', 0) or 0:6.0f} {u.get('报废成本', 0) or 0:9,.0f}")
        if len(updates) > 40:
            print(f"... 共 {len(updates)} 条")
        print()
        print("DRY-RUN 预览. 执行写回: python writeback.py --apply")
        return

    fields = ensure_fields()
    print(f"[fields] {len(fields)} fields available")

    # 批量更新: 200/批
    items = list(updates.items())
    total = 0
    for i in range(0, len(items), 200):
        batch = dict(items[i:i + 200])
        payload = {"update_records": batch}
        out = run_lark(["base", "+record-batch-update",
            "--base-token", BASE_TOKEN, "--table-id", MAIN_TABLE,
            "--json", json.dumps(payload, ensure_ascii=False), "--as", "user"])
        resp = json.loads(out.stdout)
        if not resp.get("ok"):
            raise RuntimeError(f"batch update failed: {out.stdout[:500]}")
        total += len(batch)
        print(f"[write] batch {i // 200 + 1}: {len(batch)} records ok")
    print(f"[done] {total} records written back")


if __name__ == "__main__":
    main()
