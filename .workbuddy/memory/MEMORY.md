# MEMORY.md — 清库方案自动计算（长期要点）

## 架构（2026-10-10 收敛为插件唯一端）
- **现役唯一端 = 线上插件**（Python 管线+控制台脚本已归档至 backup/backup_20261010_plugin_only/，src/ 清空）。插件运行时实读 Base 利润表，可独立运行。HTML 财务报告已退役。
- 三个决策输入字段（在途库存 / 在途到货时间(天) / 工厂未发货数量）挂在《清库方案成本核算》主表：BOM款填子记录行，单件款填主记录行。库存表纯只读数据源（每日自动同步，人工输入会被覆盖——永不放人工字段）。
- 四张表 Base token: HPsJbdMCva1IersNsFQco976n7B；主表 tblvEAMIFUSzjYmW / 库存表 tblNnE4WXy0esyGt / 日志 tblXNMVM0MNDgOzr / **利润表(成本权威源) tblHVAkfBvpBHPh4「利润测算表-导入多维表格」889行**（注意另有一张 75 行的欧洲站欧元表 tblXziEIV3SAy3Vx，勿混）。
- 三端同构铁律已于 2026-10-10 失效（三端只剩一端）：现役只有插件 plugin_app/src_app.js → esbuild bundle；Python/控制台在 backup/backup_20261010_plugin_only/ 可随时复活。
- **成本源（2026-10-10 铁律）：三端都必须运行时实读利润表 tblHVAkfBvpBHPh4**。插件/控制台运行时构建字典（内嵌快照仅兜底）；Python fetch_inventory.py 每次拉取刷新 test_profit.ndjson（cost_helper 缺文件硬报错，绝不静默 0 成本）。利润表字段：Item(逗号分隔多item共享行)/采购价$/采购/头程/关税/尾程/长宽高cm/毛重kg；单 item 行优先于多 item 共享行。
- 主表三个输入字段 ID：在途库存 flddngbOgM / 在途到货时间(天) fldiImmyEV / 工厂未发货数量 fldETydQ9I。
- 待办：库存表误建字段 fld6zOxcVk（在途到货时间）、fldIldk2FW（工厂未发货）待删——需 field:delete 权限，二维码 .workbuddy/auth_delete_qr.png 已生成待用户扫码。

## 测试与部署
- e2e: .workbuddy/run_plugin_test_r4.cjs（NO_INJECT=1 基线组；BUNDLE_FILE= 直测部署产物——仅限 mock 别名构建的，真实部署产物内联 SDK 无法 mock 跑）。覆盖率校验: .workbuddy/run_plugin_coverage.cjs（全部主记录写回检查，2026-10-10 起 233/233 全通过）。
- mock_host.cjs MAIN_FIELDS 是硬编码清单，线上加字段后必须同步，否则新逻辑在 e2e 静默失效（手动补充item 教训）。e2e harness 的 getTableList 注入列表也必须与线上表清单同步（2026-10-10 加利润表教训：漏注入会静默回落内嵌旧快照，K 值看似正常实则口径错误）。
- 改模块 import 后必须 grep 验证 import 行真实生效（2026-10-10 教训：callsite 加了 import 没加，e2e 抓 ReferenceError）。
- 注入目标选「独立站有货+有销量」的款（如 HNOFC39DN-LH-US dtc=10 s30=36）；无货款 K 恒 0，会误判注入失效。
- 部署流程：esbuild --minify → index.html 版本号递增 → Sites deploy（链接不变）→ curl 带 cb 参数验证 sha256 → check_bundle_pos.js 校验字段读取位置。
- esbuild minify 中文转 \uXXXX（大写十六进制）：校验脚本须只转义非 ASCII 且大小写两种形式都试（括号等 ASCII 保持原样）。
- 线上验证拉表须带 --as user；lark-cli field-create 幂等，先 field-list 核实再判定成败。
- 库存表约 7700 行，翻页拉全须 4-5 页（offset 0/2000/4000/6000，行数实时变动）。

## 口径（历轮拍板）
- K（实际齐套数）borrow 口径：组件池含非独立站批次可借调；严格模式独立。
- 在途入池：库龄0、到货时间 ≤ 窗口期(60天) 才入池，超窗/未填排除。
- 工厂发货决策：候选 {0, ⌊F_min/2⌋, F_min}；BOM 款按子件最小值对齐，缺子件标记 ⚠️ 跳过。
- 报废挂载：每 model 一条锚定行（选项+数量并存），非锚定行清数量+剥选项（writeback.py 2026-09-18 修复）。
- 「最优窗口方案」字段：W* 处 eval_options 重算的方案构成说明，与当前窗口一致时追加「(即当前窗口)」；K=0/子记录行清空（2026-09-18 三端落地）。
- item 兜底：主表「手动补充item」(fldwg2FsRF) 在 item 列空时补位；三端 fetch 入口归一化（2026-09-18）。
- BOM 子件 item 为空 = 池恒 0 = 整 BOM 无法齐套（N1 三款教训）；新建 BOM 行必须带 item 或填手动补充item。
