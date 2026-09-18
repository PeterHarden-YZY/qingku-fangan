# -*- coding: utf-8 -*-
"""
一键全链路：拉数 → 分配 → 报废 → 测算 → 写回 → 报告

用法:
    python run_all.py                # 全链路（重新拉数）
    python run_all.py --use-cache    # 跳过拉数，用本地缓存
    python run_all.py --no-writeback # 只算不写回（预览）
所有参数从 Base《清库参数配置》表读取（fetch_params.py）。
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
STEPS = [
    ("fetch_inventory.py",  "拉取数据 + 校验 + 组件池"),
    ("allocation.py",       "营收优先级 + 批次瀑布分配"),
    ("scrap.py",            "长版报废核算"),
    ("scenarios.py",        "混合清库财务测算 + 方案对比"),
    ("writeback.py",        "写回 Base"),
    ("report.py",           "生成财务汇总报告"),
    ("run_log.py",          "运行日志写入 Base"),
]


def main():
    args = sys.argv[1:]
    passthrough = []
    if "--use-cache" in args:
        passthrough.append("--use-cache")
    no_writeback = "--no-writeback" in args

    print("=" * 62)
    print("清库方案全链路测算")
    print("=" * 62)

    failed = None
    for i, (script, desc) in enumerate(STEPS, 1):
        if no_writeback and script == "writeback.py":
            print(f"[{i}/{len(STEPS)}] 跳过写回 (--no-writeback)")
            continue
        print(f"\n[{i}/{len(STEPS)}] {script} — {desc}")
        print("-" * 62)
        cmd = [sys.executable, str(SRC / script)] + passthrough
        if script == "writeback.py":
            cmd.append("--apply")
        proc = subprocess.run(cmd, cwd=SRC.parent)
        if proc.returncode != 0:
            failed = script
            break

    print("\n" + "=" * 62)
    if failed:
        print(f"✗ 链路在 {failed} 失败，已中止")
        sys.exit(1)
    report = SRC.parent / "output" / "清库方案财务汇总报告.html"
    print(f"✓ 全链路完成" + ("（未写回）" if no_writeback else ""))
    print(f"  报告: {report}")
    print(f"  参数: 修改 Base《清库参数配置》表后重跑本脚本即可换场景")


if __name__ == "__main__":
    main()
