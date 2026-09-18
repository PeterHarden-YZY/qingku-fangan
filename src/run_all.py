# -*- coding: utf-8 -*-
"""
一键全链路：拉数 → 分配 → 报废 → 测算 → 写回 → 报告
（缓存机制已于 2026-09-18 移除：每次实时拉取线上数据，跑完自动清理中间产物）

用法:
    python run_all.py                # 全链路（实时拉数）
    python run_all.py --no-writeback # 只算不写回（预览）
所有参数从 Base《清库参数配置》表实时读取（fetch_params.py）。
"""
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
CACHE_DIR = SRC.parent / ".workbuddy"
STEPS = [
    ("fetch_inventory.py",  "拉取数据 + 校验 + 组件池"),
    ("allocation.py",       "营收优先级 + 批次瀑布分配"),
    ("scrap.py",            "长版报废核算"),
    ("scenarios.py",        "混合清库财务测算 + 方案对比"),
    ("writeback.py",        "写回 Base"),
    ("report.py",           "生成财务汇总报告"),
    ("run_log.py",          "运行日志写入 Base"),
]

# 单次运行中间产物 (跑完全链路后统一删除, 不留任何缓存数据)
RUN_ARTIFACTS = [
    "bom_tree.json", "components.json", "factory_stock.json",
    "allocation_result.json", "scrap_result.json", "scenario_result.json",
    "inventory_snapshot.json",
]


def cleanup_artifacts():
    removed = []
    for name in RUN_ARTIFACTS:
        p = CACHE_DIR / name
        if p.exists():
            p.unlink()
            removed.append(name)
    if removed:
        print(f"[cleanup] 已清理本次运行中间产物 {len(removed)} 个: {', '.join(removed)}")


def main():
    args = sys.argv[1:]
    no_writeback = "--no-writeback" in args
    if "--use-cache" in args:
        print("[warn] --use-cache 已废弃（缓存机制已移除），本次将实时拉取线上数据")

    print("=" * 62)
    print("清库方案全链路测算 (无缓存 · 实时拉数)")
    print("=" * 62)

    failed = None
    try:
        for i, (script, desc) in enumerate(STEPS, 1):
            if no_writeback and script == "writeback.py":
                print(f"[{i}/{len(STEPS)}] 跳过写回 (--no-writeback)")
                continue
            print(f"\n[{i}/{len(STEPS)}] {script} — {desc}")
            print("-" * 62)
            cmd = [sys.executable, str(SRC / script)]
            if script == "writeback.py":
                cmd.append("--apply")
            proc = subprocess.run(cmd, cwd=SRC.parent)
            if proc.returncode != 0:
                failed = script
                break
    finally:
        # 无论成败都清理中间产物——不留任何缓存数据
        cleanup_artifacts()

    print("\n" + "=" * 62)
    if failed:
        print(f"✗ 链路在 {failed} 处失败，已中止（中间产物已清理）")
        sys.exit(1)
    report = SRC.parent / "output" / "清库方案财务汇总报告.html"
    print(f"✓ 全链路完成" + ("（未写回）" if no_writeback else ""))
    print(f"  报告: {report}")
    print(f"  参数: 修改 Base《清库参数配置》表后重跑本脚本即可换场景")


if __name__ == "__main__":
    main()
