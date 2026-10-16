# -*- coding: utf-8 -*-
"""生成 HTML 财务汇总报告 -> output/清库方案财务汇总报告.html"""
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".workbuddy"
OUT = ROOT / "output" / "清库方案财务汇总报告.html"

scen = json.loads((CACHE / "scenario_result.json").read_text(encoding="utf-8"))
scrap = json.loads((CACHE / "scrap_result.json").read_text(encoding="utf-8"))
rows = json.loads((CACHE / "inventory_snapshot.json").read_text(encoding="utf-8"))
rec = {r["record_id"]: r for r in rows}
S = scen["summary"]
P = scen["params"]

def usd(v, dec=0):
    return f"${v:,.{dec}f}" if v >= 0 else f"-${abs(v):,.{dec}f}"

results = sorted(scen["results"], key=lambda r: -r["best_net"])
W = scen["params"].get("window_days", 30)

tbody_main = ""
for r in results:
    plan = []
    if r["k_a"] > 0: plan.append(f"A×{r['k_a']:.0f}")
    if r["k_c"] > 0: plan.append(f"C×{r['k_c']:.0f}")
    if r["k_b"] > 0: plan.append(f"B×{r['k_b']:.0f}")
    plan_s = " + ".join(plan) if plan else "未清库(计提)"
    cls = "pos" if r["best_net"] >= 0 else "neg"
    # 细化成本
    c_buy = r.get("cost_buy", 0.0)
    c_head = r.get("cost_head", 0.0)
    c_tariff = r.get("cost_tariff", 0.0)
    c_last = r.get("cost_last", 0.0)
    c_storage = r.get("storage_fee", 0.0)
    cost_detail = f"采{usd(c_buy,1)}+头{usd(c_head,1)}+关{usd(c_tariff,1)}+尾{usd(c_last,1)}+仓{usd(c_storage,2)}"

    # 方案对比迷你行
    cmp_parts = []
    for k in ("A_降价卖", "B_线下清库", "C_激励方案", "最优组合"):
        v = r["compare"].get(k, {})
        n = v.get("net")
        cmp_parts.append(f"{k.replace('_',' ')}:{'--' if n is None else usd(n)}")
    cmp_s = "<br>".join(cmp_parts)
    tbody_main += f"""<tr>
      <td>{r['model']}</td><td>{r['K']:.0f}</td><td>{r['s30']:.0f}</td><td>{plan_s}</td>
      <td class="muted">{cost_detail}</td>
      <td>{r['dk']:.0f}</td><td>{usd(r['provision'])}</td>
      <td><b>{r['best_plan']}</b></td><td class="{cls}">{usd(r['best_net'])}</td>
      <td>{r.get('best_window') if r.get('best_window') is not None else '—'}</td>
      <td class="muted" style="white-space:normal">{cmp_s}</td></tr>"""

tbody_scrap = ""
for s in scrap["items"]:
    ages = "、".join(f"{b['qty']:.0f}件@{b['age']:.0f}天" for b in s["batches"])
    tbody_scrap += f"""<tr>
      <td>{s['model']}{' <span class="tag">单一主记录</span>' if s['is_single_main'] else ' <span class="tag">子model</span>'}</td>
      <td>{s['scrap_qty']:.0f}</td><td>{usd(s['purchase_first_leg'],2)}</td>
      <td>{usd(s['disposal_fee'],2)}</td><td>{usd(s['unit_scrap_cost'],2)}</td>
      <td class="neg">{usd(s['scrap_cost'])}</td><td class="muted">{ages}</td></tr>"""

# 工厂未发货决策明细
fac_rows = [r for r in scen["results"] if r.get("factory")]
tbody_factory = ""
for r in fac_rows:
    f = r["factory"]
    comps = "、".join(f"{n}×{q:.0f}" for n, q in f.get("fac_qty_by_comp", {}).items())
    if f.get("decided"):
        cls = "pos" if f["ship_qty"] > 0 else "neg"
        tbody_factory += f"""<tr>
          <td>{r['model']}</td><td>{comps}</td><td>{f['f_min']:.0f}</td>
          <td><b>{f['decision']}</b></td><td>{f['ship_qty']:.0f}</td>
          <td>{usd(f['u_ship'],2)}</td><td>{usd(f['v_best'])}</td>
          <td class="neg">{usd(f['loss_not_ship'] or 0)}</td></tr>"""
    else:
        tbody_factory += f"""<tr>
          <td>{r['model']}</td><td>{comps}</td><td>{f.get('f_min',0):.0f}</td>
          <td class="muted">⚠️ {f.get('reason','')}</td><td>—</td><td>—</td><td>—</td><td>—</td></tr>"""

html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>清库方案财务汇总报告</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 24px auto; max-width: 1080px; color: #1f2329; background: #f7f8fa; }}
  h1 {{ font-size: 22px; }} h2 {{ font-size: 17px; margin-top: 32px; border-left: 4px solid #3370ff; padding-left: 8px; }}
  .meta {{ color: #646a73; font-size: 12px; margin-bottom: 16px; }}
  .cards {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin: 16px 0; }}
  .card {{ background: #fff; border-radius: 8px; padding: 14px 16px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }}
  .card .v {{ font-size: 20px; font-weight: 600; margin-top: 4px; }}
  .card .l {{ font-size: 12px; color: #646a73; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 13px; box-shadow: 0 1px 3px rgba(0,0,0,.08); border-radius: 8px; overflow: hidden; }}
  th {{ background: #f0f1f5; text-align: left; padding: 8px 10px; font-weight: 600; white-space: nowrap; }}
  td {{ padding: 7px 10px; border-top: 1px solid #f0f1f5; white-space: nowrap; }}
  .pos {{ color: #d83931; font-weight: 600; }}
  .neg {{ color: #2ea121; font-weight: 600; }}
  .muted {{ color: #8f959e; font-size: 12px; white-space: normal; }}
  .tag {{ background: #e8f0ff; color: #3370ff; border-radius: 4px; padding: 1px 6px; font-size: 11px; }}
  .note {{ background: #fff7e8; border: 1px solid #ffe3b3; border-radius: 8px; padding: 12px 16px; font-size: 13px; margin: 12px 0; }}
</style>
</head>
<body>
<h1>清库方案财务汇总报告</h1>
<div class="meta">生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据源: 飞书多维表格《清库方案成本核算》(221条) + 《库存表》行级批次 (7,691条) · 币种: USD</div>

<div class="cards">
  <div class="card"><div class="l">总齐套数 K</div><div class="v">{S['total_k']:.0f} 套</div></div>
  <div class="card"><div class="l">本渠道净额合计</div><div class="v {'pos' if S['net_channel_total']>=0 else 'neg'}">{usd(S['net_channel_total'])}</div></div>
  <div class="card"><div class="l">未清库计提</div><div class="v neg">{usd(S['total_provision'])}</div></div>
  <div class="card"><div class="l">长版报废成本</div><div class="v neg">{usd(S['total_scrap_cost'])}</div></div>
</div>
<div class="cards">
  <div class="card"><div class="l">在途入池（库龄0）</div><div class="v">见组件池</div></div>
  <div class="card"><div class="l">工厂决策 model 数</div><div class="v">{S.get('factory_decided_models', 0):.0f} 个</div></div>
  <div class="card"><div class="l">工厂建议发货</div><div class="v">{S.get('factory_ship_total', 0):.0f} 套</div></div>
  <div class="card"><div class="l">工厂不发货亏损</div><div class="v neg">{usd(S.get('factory_loss_total', 0))}</div></div>
</div>

<h2>清库路径分布（需求分层瀑布，{W} 天销售窗口）</h2>
<div class="note"><b>时间口径</b>：可销量 Q = 过去30天销量 × ({W}/30) × 放大系数M（M = clamp(1 + λ×折扣深度, 1.0, 3.0), λ={P['lambda']}）。即「未来 {W} 天内、在该售价下的预计可销量」——分层套数全部是 {W} 天窗口内的量，不是即时全部卖出。B/C 两路径假设买家一次接盘全量。</div>
<table>
<tr><th>路径</th><th>处置套数</th><th>说明</th></tr>
<tr><td>A 降价售卖（{(1-P['discount_a'])*100:.0f}折）</td><td>{S['total_k_a']:.0f} 套</td><td class="muted">{W}天窗口内预计可卖；u_A = 降价售价 − 固定成本 − 降价售价×30% 推广费</td></tr>
<tr><td>C 激励方案（促销 {(P['discount_c'])*10:.0f} 折）</td><td>{S['total_k_c']:.0f} 套</td><td class="muted">与 A 同价（{(1-P['discount_a'])*100:.0f}折），需求分层下增量恒为 0 —— 见下方参数说明</td></tr>
<tr><td>B 线下清库（{P['discount_b']*10:.1f}折）</td><td>{S['total_k_b']:.0f} 套</td><td class="muted">u_B = 原价×1.5折 − (采购价+头程)；按买家全量接盘测算</td></tr>
<tr><td>未清库 ΔK（计提）</td><td>{S['total_dk']:.0f} 套</td><td class="muted">{W}天窗口内卖不掉的部分，按批次库龄分档计提: 121-180天×30% / 181-270天×80% / &gt;270天×150%（基数=采购价）</td></tr>
</table>

<div class="note"><b>参数说明</b>：当前场景「{P.get('scenario','默认')}」，全部参数来自 Base《清库参数配置》表。方案 A 降价幅度当前为 {(P['discount_a'])*100:.0f}%，与方案 C 促销售价（8折）相同，需求分层中 C 层增量恒为 0。要激活激励方案通道：把参数表中「A降价幅度」调小（如 10%，即 9 折卖）后重跑全链路即可。</div>

<h2>主 Model 方案对比与最优方案（按最优净额降序）</h2>
<div class="meta">最佳方案 = 最优动态组合的具体执行动作（方案+套数）；预计回收资金按该组合口径。W* = 该 model 单独决策时达到最高净额所需的最短销售窗口（净额对窗口不敏感时为 —）。单方案净额：A 卖得掉的按 u_A 算、卖不掉计提；B 全量出清；C 激励全量；最优组合 = 4 选项全局比选后的最优。</div>
<table>
<tr><th>Model</th><th>齐套 K</th><th>S30</th><th>{W}天分层执行</th><th>成本拆解</th><th>ΔK</th><th>计提</th><th>最优方案</th><th>最优净额</th><th>最优窗口</th><th>多方案净额对比</th></tr>
{tbody_main}
</table>

<h2>长版报废清单（挂载子 model / 单一主记录维度）</h2>
<table>
<tr><th>Model</th><th>报废数量</th><th>采购价+头程</th><th>处理费/件</th><th>单件报废成本</th><th>报废成本</th><th>剩余批次（数量@库龄）</th></tr>
{tbody_scrap}
</table>

<h2>工厂未发货库存决策（第四轮新增）</h2>
<div class="note"><b>决策口径</b>：候选发货量 X ∈ {{0, ⌊F/2⌋, F}}（F = BOM 各子件未发货量最小值；整单全发或最多除以2，不能拆散套）。发货的套数按该 model 本轮最优路径（A/B/C）测算单套净回收；不发货的部分按「亏损采购价」计提（Σ未发件×采购价）。BOM 款任一子件未填写工厂未发货量则无法齐套决策，标记 ⚠️。</div>
<table>
<tr><th>Model</th><th>各子件未发货量</th><th>可发上限 F_min</th><th>决策</th><th>发货套数</th><th>单套发货净回收</th><th>决策价值 V</th><th>不发货亏损</th></tr>
{tbody_factory}
</table>

<h2>口径与规则</h2>
<div class="meta" style="font-size:12px;line-height:1.8">
· 优先级：过去30天营收（销量×售价）降序，瀑布分配共享组件，FIFO 最老批次优先消耗<br>
· 组件池：《库存表》「独立站团队 + 海外仓」行级批次 + 在途批次（库龄0，到货时间≤{W}天才入池）；同名子 model 合并，避免关系行重复计数<br>
· 工厂未发货：候选 X ∈ {{0, ⌊F/2⌋, F}}，发货按最优路径净回收测算，不发货亏采购价；在《库存表》「工厂未发货数量」列按 item 人工填写<br>
· min(独立站, 海外仓总量)：独立站库存已验证全部位于海外仓，行级口径自动满足<br>
· 净额 = k_A×u_A + k_B×u_B − 奖金_C − 计提费用；报废成本 = Σ 剩余量×(采购价+头程+处理费)，处理费 &lt;50lbs $2 / ≥50lbs $3<br>
· 已知数据缺失：PDBFC09DW-US / PDBFC09LW-US 固定成本合计为空，按 0 参与测算
</div>
</body>
</html>"""

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(html, encoding="utf-8")
print(f"[done] -> {OUT}")
