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
    # GBK 输出容错: lark-cli 在 Windows 下可能混入非 UTF-8 字节, 避免 reader 线程解码崩溃
    return subprocess.run([cli] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


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
        "最优窗口期(天)": {"type": "number"},
        "最优窗口方案": {"type": "text"},
        "仓储费": {"type": "number"},
        "工厂发货套数": {"type": "number"},
        "工厂发货决策": {"type": "text"},
        "工厂不发货亏损": {"type": "number"},
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
    rows = json.loads((CACHE_DIR / "inventory_snapshot.json").read_text(encoding="utf-8"))
    rec_by_id = {r["record_id"]: r for r in rows}

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
            "最优窗口期(天)": r.get("best_window"),
            "最优窗口方案": r.get("best_window_plan") or "",
            "仓储费": round(r.get("storage_fee", 0.0), 2),
            "工厂发货套数": (r["factory"]["ship_qty"] if r.get("factory") and r["factory"].get("decided") else 0),
            "工厂发货决策": (r["factory"]["decision"] if r.get("factory") and r["factory"].get("decided")
                             else (r["factory"]["reason"] if r.get("factory") else "")),
            "工厂不发货亏损": (round(r["factory"]["loss_not_ship"], 2)
                               if r.get("factory") and r["factory"].get("decided") else None),
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

    # 2) 无齐套(K=0)主model: 清空旧写回值, 防止残留过期方案/垃圾值 (与前端插件口径一致)
    #    处理方案: 以「本轮拉取的线上现值」为基准, 剥离计算层路径选项(降价售卖/线下清库/
    #    激励方案)后写回 —— K=0 时这些选项必为历史运行残留(如幽灵库存事故写入的幽灵套数方案)
    PATH_OPTIONS = ("降价售卖", "线下清库", "激励方案")
    alloc = json.loads((CACHE_DIR / "allocation_result.json").read_text(encoding="utf-8"))
    k0_models = {al["model"] for al in alloc["allocations"] if al["K"] <= 0}
    for m_name in k0_models:
        for rid in main_rids_by_model.get(m_name, []):
            live_plan = rec_by_id[rid].get("处理方案") if rid in rec_by_id else None
            if not isinstance(live_plan, list):
                live_plan = []
            stripped = [p for p in live_plan if p not in PATH_OPTIONS]
            base_upd = {
                "实际齐套数": 0,
                "最佳方案": "",
                "预计回收资金": 0,
                "最优窗口期(天)": None,
                "最优窗口方案": "",
                "仓储费": 0,
                "工厂发货套数": 0,
                "工厂发货决策": "",
                "工厂不发货亏损": None,
                "处理方案": stripped,
            }
            if rid not in updates:
                updates[rid] = base_upd
            else:
                old_plan = updates[rid].get("处理方案")
                if isinstance(old_plan, list):
                    merged = [p for p in old_plan if p not in PATH_OPTIONS]
                    updates[rid]["处理方案"] = merged

    # 3) 长版报废: 单行挂载 —— 每个 model 只写"该model首条记录行", 其余行清空
    #    (修复: 旧版把报废数量/成本广播到同名model的每一行, 用户列汇总时虚高N倍)
    #    首选子model行; 无子行时用单一主记录行
    def anchor_row_id(model_name):
        rids = comp_rids_by_model.get(model_name, [])
        sub_rids = [rid for rid in rids if rec_by_id[rid].get("父记录")]
        return sub_rids[0] if sub_rids else (rids[0] if rids else None)

    scrap_anchor_by_model = {}
    for s in scrap["items"]:
        if s["scrap_cost"] is None:
            continue
        anchor = anchor_row_id(s["model"]) or s["record_id"]
        scrap_anchor_by_model[s["model"]] = anchor
        scrap_upd = {
            "报废数量": s["scrap_qty"],
            "报废成本": round(s["scrap_cost"], 2),
        }
        if anchor in updates:
            updates[anchor].update(scrap_upd)
            curr_plan = updates[anchor].get("处理方案", [])
            if "报废" not in curr_plan:
                updates[anchor]["处理方案"] = curr_plan + ["报废"]
        else:
            updates[anchor] = {
                "报废数量": s["scrap_qty"],
                "报废成本": round(s["scrap_cost"], 2),
                "处理方案": ["报废"],
            }

    # 4) 同model其余行 + 清库标记=不清库 的所有行: 清空报废值 (防历史广播残留与误读)
    #    2026-09-18 修复: 旧逻辑只对「本轮 updates 里已存在的行」剥离报废选项,
    #    本轮未被 touched 的行(如已被消费完的子件)只清了报废数量、留着 ['报废'] 选项
    #    -> 线上残留 13 行「报废选项挂着但报废数量空」。改为: 全表统一以线上现值为基准剥离。
    anchored_models = set(scrap_anchor_by_model)
    PATH_OPTIONS_ALL = ("降价售卖", "线下清库", "激励方案", "报废")
    for model_name, rids in comp_rids_by_model.items():
        anchor = scrap_anchor_by_model.get(model_name)
        for rid in rids:
            if rid == anchor:
                continue
            live_plan = rec_by_id[rid].get("处理方案") if rid in rec_by_id else None
            if not isinstance(live_plan, list):
                live_plan = []
            stripped = [p for p in live_plan if p not in PATH_OPTIONS_ALL]
            if rid in updates:
                updates[rid]["报废数量"] = None
                updates[rid]["报废成本"] = None
                plan = updates[rid].get("处理方案")
                if not isinstance(plan, list):
                    # 该行本轮没算出路径方案 -> 以线上现值剥离路径选项后回写
                    plan = [p for p in live_plan if p not in PATH_OPTIONS_ALL]
                updates[rid]["处理方案"] = [p for p in plan if p != "报废"]
            else:
                updates[rid] = {"报废数量": None, "报废成本": None}
            # 线上残留的「报废」选项无条件剥离 (报废值只允许挂在 anchor 行)
            if "报废" in live_plan:
                updates[rid]["处理方案"] = [p for p in updates[rid].get("处理方案", []) if p != "报废"]
                if not updates[rid]["处理方案"] and not any(
                    updates[rid].get(k) is not None for k in ("报废数量", "报废成本")
                ):
                    updates[rid]["处理方案"] = stripped

    return updates


def main():
    dry = "--apply" not in sys.argv
    updates = build_updates()
    print(f"[payload] {len(updates)} 条记录待写回")

    if dry:
        rows = json.loads((CACHE_DIR / "inventory_snapshot.json").read_text(encoding="utf-8"))
        rec_by_id = {r["record_id"]: r for r in rows}
        print(f"{'model':30} {'处理方案':20} {'齐套':>5} {'净额$':>10} {'报废量':>6} {'报废$':>9}")
        for rid, u in list(updates.items())[:40]:
            plan_str = "/".join(u.get("处理方案", [])) if u.get("处理方案") else "-"
            print(f"{rec_by_id[rid]['model']:30} {plan_str:20} "
                  f"{u.get('实际齐套数', 0) or 0:5.0f} {u.get('预计回收资金', 0) or 0:10,.0f} "
                  f"{u.get('报废数量', 0) or 0:6.0f} {u.get('报废成本', 0) or 0:9,.0f}")
        if len(updates) > 40:
            print(f"... 共 {len(updates)} 条")
        print()
        print("DRY-RUN 预览. 执行写回: python writeback.py --apply")
        return

    fields = ensure_fields()
    print(f"[fields] {len(fields)} fields available")

    # 批量更新: 受 Windows 命令行长度限制(约8191字符), 除记录数外还需按 payload 字符预算切批
    items = list(updates.items())
    total = 0
    batch_no = 0
    i = 0
    while i < len(items):
        batch = {}
        chars = 0
        while i < len(items) and len(batch) < 200:
            rid, fv = items[i]
            j = len(json.dumps({rid: fv}, ensure_ascii=False))
            if batch and chars + j > 3500:
                break
            batch[rid] = fv
            chars += j
            i += 1
        batch_no += 1
        payload = {"update_records": batch}
        out = run_lark(["base", "+record-batch-update",
            "--base-token", BASE_TOKEN, "--table-id", MAIN_TABLE,
            "--json", json.dumps(payload, ensure_ascii=False), "--as", "user"])
        resp = json.loads(out.stdout)
        if not resp.get("ok"):
            raise RuntimeError(f"batch update failed: {out.stdout[:500]}")
        total += len(batch)
        print(f"[write] batch {batch_no}: {len(batch)} records ok ({chars} chars)")
    print(f"[done] {total} records written back")


if __name__ == "__main__":
    main()
