# -*- coding: utf-8 -*-
"""
参数配置层：从 Base《清库参数配置》表读取启用场景 -> params dict
所有计算模块统一从这里取参数；参数表不可用时回退代码默认值。

缓存策略（2026-09-18 用户拍板取消缓存）:
- 每次实时拉取参数表，无任何缓存
"""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"
PARAMS_TABLE = "tblaGnwkT7yEa8cW"
BASE_TOKEN = "HPsJbdMCva1IersNsFQco976n7B"
LARK_CLI_CANDIDATES = [
    r"C:\Users\yangziyang\.workbuddy\binaries\node\cli-connector-packages\lark-cli.cmd",
    r"C:\Users\yangziyang\AppData\Roaming\npm\lark-cli.cmd",
]

# 代码默认值（与历史拍板值一致）——参数表读不到时的兜底
DEFAULTS = {
    "scenario": "代码默认(参数表不可用)",
    "window_days": 30,
    "discount_a": 0.20,
    "discount_b": 0.15,
    "discount_c": 0.80,
    "promo_rate": 0.30,
    "lambda": 1.0,
    "m_min": 1.0,
    "m_max": 3.0,
    "bonus_t1_cap": 20.0,     # 万USD
    "bonus_r1": 0.010,
    "bonus_t2_cap": 50.0,     # 万USD
    "bonus_r2": 0.015,
    "bonus_r3": 0.020,
    "scrap_fee_light": 2.0,
    "scrap_fee_heavy": 3.0,
    "weight_threshold": 50.0,
    "prov_age1": 120,         # ≤120天不计提
    "prov_rate1": 0.30,       # 121-180天
    "prov_age2": 180,
    "prov_rate2": 0.80,       # 181-270天
    "prov_rate3": 1.50,       # >270天
}

# 参数表字段名 -> params key 映射
FIELD_MAP = {
    "场景名": "scenario",
    "销量预测窗口(天)": "window_days",
    "A降价幅度": "discount_a",
    "B线下折扣": "discount_b",
    "C促销售价折扣": "discount_c",
    "A推广费率": "promo_rate",
    "销量放大系数λ": "lambda",
    "放大系数上限": "m_max",
    "放大系数下限": "m_min",
    "奖金档1上限(万USD)": "bonus_t1_cap",
    "奖金档1比例": "bonus_r1",
    "奖金档2上限(万USD)": "bonus_t2_cap",
    "奖金档2比例": "bonus_r2",
    "奖金档3比例": "bonus_r3",
    "报废费-轻(USD/件)": "scrap_fee_light",
    "报废费-重(USD/件)": "scrap_fee_heavy",
    "重量分界(lbs)": "weight_threshold",
    "计提库龄分界1(天)": "prov_age1",
    "计提比例1(120-180天)": "prov_rate1",
    "计提库龄分界2(天)": "prov_age2",
    "计提比例2(181-270天)": "prov_rate2",
    "计提比例3(>270天)": "prov_rate3",
}


def to_num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_params_from_base():
    """从参数表实时拉取启用行 -> params dict；失败抛异常由调用方回退。
    拉取文件写入系统临时目录，函数返回前清理——不落任何缓存。"""
    cli = shutil.which("lark-cli")
    if cli is None:
        for cand in LARK_CLI_CANDIDATES:
            if Path(cand).exists():
                cli = cand
                break
    if cli is None:
        raise RuntimeError("lark-cli not found in PATH")
    tmp = Path(tempfile.gettempdir()) / "lark_params_fetch.ndjson"
    try:
        proc = subprocess.run([cli, "base", "+record-list",
                               "--base-token", BASE_TOKEN,
                               "--table-id", PARAMS_TABLE,
                               "--format", "ndjson",
                               "--output", tmp.as_posix(),
                               "--overwrite",
                               "--as", "user"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"lark-cli 退出码 {proc.returncode}: {proc.stderr[:150]}")
        rows = []
        with tmp.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    finally:
        tmp.unlink(missing_ok=True)
    enabled = [r for r in rows if "启用" in (r.get("是否启用") or [])]
    if not enabled:
        raise RuntimeError("参数表中没有「启用」状态的行")
    if len(enabled) > 1:
        raise RuntimeError(f"参数表有 {len(enabled)} 行同时启用，只允许一行启用（当前: "
                           f"{[r.get('场景名') for r in enabled]}）")
    row = enabled[0]
    params = dict(DEFAULTS)
    params["scenario"] = row.get("场景名") or params["scenario"]
    missing = []
    for field, key in FIELD_MAP.items():
        if key == "scenario":
            continue
        v = to_num(row.get(field))
        if v is None:
            missing.append(field)
            continue
        params[key] = v
    if missing:
        print(f"[params] 警告: {len(missing)} 个字段缺失已回退默认值: {missing}")
    return params


def load_params():
    """主入口：每次实时拉取参数表，失败回退代码默认值（无缓存）"""
    try:
        p = fetch_params_from_base()
        print(f"[params] 从参数表实时加载: 场景「{p['scenario']}」")
        return p
    except Exception as e:
        p = dict(DEFAULTS)
        print(f"[params] 参数表读取失败({e})，回退代码默认值")
        return p


if __name__ == "__main__":
    p = load_params()
    print(json.dumps(p, ensure_ascii=False, indent=1))
