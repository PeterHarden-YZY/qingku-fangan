// 阶梯日费率 ($/m³/天)
export function tierRate(age) {
  if (age <= 60) return 0.0;
  if (age <= 90) return 0.25;
  if (age <= 180) return 0.78;
  if (age <= 360) return 0.90;
  return 1.25;
}

// 方式A: 阶梯累进增量仓储费 (age -> age + deltaDays)
export function calcIncrementalStorageFee(age, deltaDays, cbm) {
  if (!cbm || cbm <= 0 || !deltaDays || deltaDays <= 0) return 0.0;
  const startD = Math.max(0, Math.floor(age));
  const endD = Math.max(0, Math.floor(age + deltaDays));
  let totalRate = 0;
  for (let d = startD; d < endD; d++) {
    totalRate += tierRate(d);
  }
  return totalRate * cbm;
}

export function getCurrentBDays() {
  return new Date().getDate() + 10;
}
