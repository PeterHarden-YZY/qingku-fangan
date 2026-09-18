import('@lark-base-open/js-sdk').then(m => {
  const { bitable } = m;
  console.log('--- 复现前提 ---');
  console.log('bitable 存在:', typeof bitable === 'object', '| bitable.base 存在:', !!bitable.base);
  console.log('bitable.FieldType:', bitable.FieldType);

  // src_app.js:573 的原代码
  const fName = "最优窗口期(天)";   // 模拟表里缺失的字段
  try {
    const fType = fName === "最佳方案" ? bitable.FieldType.Text : bitable.FieldType.Number;
    console.log('未报错?!', fType);
  } catch (e) {
    console.log('');
    console.log('=== 复现成功, 抛出与用户完全一致的错误 ===');
    console.log('Error:', e.message);
    const expected = "Cannot read properties of undefined (reading 'Number')";
    console.log('用户报错:', expected);
    console.log('一致:', e.message === expected);
  }
});
