# MEMORY.md — 清库方案自动计算（长期要点）

## 架构（2026-09-18 定稿）
- 三个决策输入字段（在途库存 / 在途到货时间(天) / 工厂未发货数量）挂在《清库方案成本核算》主表：BOM款填子记录行，单件款填主记录行。库存表纯只读数据源（每日自动同步，人工输入会被覆盖——永不放人工字段）。
- 四张表 Base token: HPsJbdMCva1IersNsFQco976n7B；主表 tblvEAMIFUSzjYmW / 库存表 tblNnE4WXy0esyGt / 日志 tblXNMVM0MNDgOzr。
- 三端同构：Python 管线（src/run_all.py）、插件（plugin_app/src_app.js → esbuild bundle）、控制台脚本（src/base_script.js）。改口径必须三端同步。
- 主表三个输入字段 ID：在途库存 flddngbOgM / 在途到货时间(天) fldiImmyEV / 工厂未发货数量 fldETydQ9I。
- 待办：库存表误建字段 fld6zOxcVk（在途到货时间）、fldIldk2FW（工厂未发货）待删——需 field:delete 权限，二维码 .workbuddy/auth_delete_qr.png 已生成待用户扫码。

## 测试与部署
- e2e: .workbuddy/run_plugin_test_r4.cjs（NO_INJECT=1 基线组；BUNDLE_FILE= 直测部署产物——仅限 mock 别名构建的，真实部署产物内联 SDK 无法 mock 跑）。
- mock_host.cjs MAIN_FIELDS 是硬编码清单，线上加字段后必须同步，否则新逻辑在 e2e 静默失效（手动补充item 教训）。
- 注入目标选「独立站有货+有销量」的款（如 HNOFC39DN-LH-US dtc=10 s30=36）；无货款 K 恒 0，会误判注入失效。
- 部署流程：esbuild --minify → index.html 版本号递增 → Sites deploy（链接不变）→ curl 带 cb 参数验证 sha256 → check_bundle_pos.js 校验字段读取位置。
- esbuild minify 中文转 \uXXXX（大写十六进制）：校验脚本须只转义非 ASCII 且大小写两种形式都试（括号等 ASCII 保持原样）。
- 线上验证拉表须带 --as user；lark-cli field-create 幂等，先 field-list 核实再判定成败。
- 库存表 9726 行，翻页拉全须 5 页（offset 0/2000/4000/6000/8000，末页 1726）。

## 口径（历轮拍板）
- K（实际齐套数）borrow 口径：组件池含非独立站批次可借调；严格模式独立。
- 在途入池：库龄0、到货时间 ≤ 窗口期(60天) 才入池，超窗/未填排除。
- 工厂发货决策：候选 {0, ⌊F_min/2⌋, F_min}；BOM 款按子件最小值对齐，缺子件标记 ⚠️ 跳过。
- 报废挂载：每 model 一条锚定行（选项+数量并存），非锚定行清数量+剥选项（writeback.py 2026-09-18 修复）。
- 「最优窗口方案」字段：W* 处 eval_options 重算的方案构成说明，与当前窗口一致时追加「(即当前窗口)」；K=0/子记录行清空（2026-09-18 三端落地）。
- item 兜底：主表「手动补充item」(fldwg2FsRF) 在 item 列空时补位；三端 fetch 入口归一化（2026-09-18）。
- BOM 子件 item 为空 = 池恒 0 = 整 BOM 无法齐套（N1 三款教训）；新建 BOM 行必须带 item 或填手动补充item。
