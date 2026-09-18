# -*- coding: utf-8 -*-
"""
运行日志：每次测算后把结果摘要写入 Base《测算运行日志》表
一行 = 一次运行（场景名 + 关键参数 + 核心结果），用于在多维表格内横向对比场景。
"""
import json
import subprocess
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"
BASE_TOKEN = "HPsJbdMCva1IersNsFQco976n7B"
LOG_TABLE = "tblXNMVM0MNDgOzr"


def run_lark(args):
    """与 writeback.py 一致：GBK 输出容错，避免中文 stdout 解码崩溃"""
    import shutil
    cli = shutil.which("lark-cli")
    if cli is None:
        for cand in (r"C:\Users\yangziyang\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd",
                     r"C:\Users\yangziyang\AppData\Roaming\npm\lark-cli.cmd"):
            from pathlib import Path as _P
            if _P(cand).exists():
                cli = cand
                break
    if cli is None:
        raise RuntimeError("lark-cli not found")
    return subprocess.run([cli] + args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def log_run():
    scen = json.loads((CACHE_DIR / "scenario_result.json").read_text(encoding="utf-8"))
    params = scen["params"]
    S = scen["summary"]
    # 最优方案分布
    from collections import Counter
    dist = Counter(r["best_plan"] for r in scen["results"])
    dist_s = " / ".join(f"{k}×{v}" for k, v in dist.most_common())

    key_params = (f"窗口{params['window_days']:.0f}天；A降{params['discount_a']*100:.0f}%；"
                  f"B {params['discount_b']*10:.1f}折；C {params['discount_c']*10:.0f}折；"
                  f"推广{params['promo_rate']*100:.0f}%；λ={params['lambda']}")
    best_total = sum(r["best_net"] for r in scen["results"])

    payload = {
        "create_records": [{
            "运行时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "场景名": params.get("scenario", ""),
            "关键参数": key_params,
            "总齐套K": S["total_k"],
            "A层套数": S["total_k_a"],
            "C层套数": S["total_k_c"],
            "B层套数": S["total_k_b"],
            "ΔK未清库": S["total_dk"],
            "计提USD": round(S["total_provision"], 2),
            "报废成本USD": round(S["total_scrap_cost"], 2),
            "混合净额USD": round(S["net_channel_total"], 2),
            "最优方案净额USD": round(best_total, 2),
            "最优分布": dist_s,
        }]
    }
    out = run_lark([
        "base", "+record-batch-create",
        "--base-token", BASE_TOKEN,
        "--table-id", LOG_TABLE,
        "--json", json.dumps(payload, ensure_ascii=False),
        "--as", "user",
    ])
    try:
        ok = json.loads(out.stdout).get("ok")
    except json.JSONDecodeError:
        ok = None
        print(f"[log] 输出解析失败 stdout={out.stdout[:120]!r} stderr={out.stderr[:200]!r}")
    print(f"[log] 运行日志写入{'成功' if ok else '失败'}: 场景「{params.get('scenario','')}」 混合净额 ${S['net_channel_total']:,.0f} / 最优 ${best_total:,.0f}")


if __name__ == "__main__":
    log_run()
