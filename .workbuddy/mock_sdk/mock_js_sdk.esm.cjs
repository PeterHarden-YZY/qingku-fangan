// ESM mock SDK 的 CJS 桥 (供 run_plugin_test.cjs require)
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, 'mock_js_sdk.mjs'), 'utf-8');
// 简单转换: 提取导出对象
const FieldType = { Text: 1, Number: 2, DateTime: 5, SingleSelect: 3, MultiSelect: 4 };
const bitable = {
  base: undefined,
  bridge: {},
  ui: {},
  dashboard: {},
};
module.exports = { FieldType, bitable };
