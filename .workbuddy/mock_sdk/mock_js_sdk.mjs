// 忠实模拟 @lark-base-open/js-sdk v1.0.2 的关键语义:
// 1. FieldType 是模块顶层导出 (bitable.FieldType === undefined!)  ← 线上报错根因
// 2. Table.getFieldList() (deprecated) 返回 Field 实例, 实例只有 getName() 方法, 没有 .name 属性
// 3. Table.getFieldMetaList() 返回 {id, name, type}[] ← 正确的字段元信息 API
// 4. addField({name, type}) 返回 {id}
// 测试驱动通过 globalThis.__MOCK_BITABLE_BASE__ 注入宿主行为
export const FieldType = { Text: 1, Number: 2, DateTime: 5, SingleSelect: 3, MultiSelect: 4 };

export const bitable = {
  get base() { return (typeof globalThis !== 'undefined' && globalThis.__MOCK_BITABLE_BASE__) || undefined; },
  get bridge() { return {}; },
  get ui() { return {}; },
  get dashboard() { return {}; },
  // 关键: 真实 SDK v1.0.2 中 bitable 对象上没有 FieldType —— 这是报错根源
};
