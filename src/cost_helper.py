# -*- coding: utf-8 -*-
"""
成本与仓储费统一计算助手模块 (Cost & Storage Fee Helper)
统一为 allocation.py, scenarios.py, scrap.py, report.py, writeback.py 提供数据解析与仓储费核算。

核算口径:
1. 彻底废弃旧「固定成本合计」
2. 单件综合成本 = 采购价 + 头程 + 关税 + 尾程 + 仓储费
3. 仓储费规则:
   - 阶梯日费率:
     0-60天: $0 / m³ / 天
     61-90天: $0.25 / m³ / 天
     91-180天: $0.78 / m³ / 天
     181-360天: $0.90 / m³ / 天
     >360天: $1.25 / m³ / 天
   - 计费时间:
     - 历史已结算: 不核算上个月的仓储费 (仅核算本月及以后)
     - A方案: 核算未来清库销售窗口期 W 天的增量仓储费 (age -> age + W)
     - B方案: 核算本月已过天数 + 10 天的仓储费 (age -> age + days_in_month + 10)
     - C方案: 核算本月已过天数 + 10 天的仓储费 (age -> age + days_in_month + 10)
   - BOM 组合款: 单台主 Model 仓储费等于各子组件批次仓储费之和
"""
import datetime
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".workbuddy"


def to_f(v):
    if v in (None, "", "#N/A"):
        return 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def tier_rate(age):
    """阶梯日费率 ($/m³/天)"""
    if age <= 60:
        return 0.0
    elif age <= 90:
        return 0.25
    elif age <= 180:
        return 0.78
    elif age <= 360:
        return 0.90
    else:
        return 1.25


def calc_incremental_storage_fee(age, delta_days, cbm):
    """计算单个批次在 delta_days 天内的增量仓储费 (方式A 阶梯累进)"""
    if cbm <= 0 or delta_days <= 0:
        return 0.0
    start_d = max(0, int(age))
    end_d = max(0, int(age + delta_days))
    total_rate = sum(tier_rate(d) for d in range(start_d, end_d))
    return total_rate * cbm


class CostProvider:
    """成本与尺寸数据提供者，支持从利润测算表拉取长宽高、采购、头程、尾程、关税"""

    def __init__(self):
        self.single_item_profit = {}
        self.multi_item_profit = {}
        self._load_profit_table()

    def _load_profit_table(self):
        profit_file = CACHE_DIR / "test_profit.ndjson"
        if not profit_file.exists():
            return
        with open(profit_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                items_str = r.get("Item") or r.get("item")
                if not items_str:
                    continue
                parts = [p.strip() for p in str(items_str).split(",") if p.strip()]
                if len(parts) == 1:
                    self.single_item_profit[parts[0]] = r
                else:
                    for p in parts:
                        if p not in self.multi_item_profit:
                            self.multi_item_profit[p] = r

    def get_item_cost_and_dim(self, it):
        """获取单个 Item 的成本与尺寸信息"""
        if not it:
            return {"buy": 0.0, "head": 0.0, "tariff": 0.0, "last": 0.0, "cbm": 0.0, "weight_lbs": 0.0}
        r = self.single_item_profit.get(it) or self.multi_item_profit.get(it) or {}
        b = to_f(r.get("采购价$")) or to_f(r.get("采购"))
        head = to_f(r.get("头程"))
        tariff = to_f(r.get("关税"))
        last = to_f(r.get("尾程"))
        l, w, h = to_f(r.get("长cm")), to_f(r.get("宽cm")), to_f(r.get("高cm"))
        cbm = (l * w * h) / 1000000.0 if (l > 0 and w > 0 and h > 0) else 0.0
        weight_kg = to_f(r.get("毛重kg"))
        weight_lbs = weight_kg * 2.20462 if weight_kg > 0 else 0.0
        return {
            "buy": b,
            "head": head,
            "tariff": tariff,
            "last": last,
            "cbm": cbm,
            "weight_lbs": weight_lbs,
        }

    def get_model_cost_and_cbm(self, m_rec, subs=None):
        """获取主 model (含子组件汇总) 的成本与 CBM"""
        if subs:
            buy_tot = sum(self.get_item_cost_and_dim(s.get("item"))["buy"] for s in subs)
            head_tot = sum(self.get_item_cost_and_dim(s.get("item"))["head"] for s in subs)
            tariff_tot = sum(self.get_item_cost_and_dim(s.get("item"))["tariff"] for s in subs)
            last_tot = sum(self.get_item_cost_and_dim(s.get("item"))["last"] for s in subs)
            cbm_tot = sum(self.get_item_cost_and_dim(s.get("item"))["cbm"] for s in subs)
            return {
                "buy": buy_tot,
                "head": head_tot,
                "tariff": tariff_tot,
                "last": last_tot,
                "cbm": cbm_tot,
            }
        else:
            it = m_rec.get("item")
            info = self.get_item_cost_and_dim(it)
            return {
                "buy": info["buy"],
                "head": info["head"],
                "tariff": info["tariff"],
                "last": info["last"],
                "cbm": info["cbm"],
            }


_provider = None


def get_cost_provider():
    global _provider
    if _provider is None:
        _provider = CostProvider()
    return _provider


def get_current_b_days():
    """获取本月已过天数 + 10天 (B/C方案仓储天数)"""
    return datetime.datetime.now().day + 10
