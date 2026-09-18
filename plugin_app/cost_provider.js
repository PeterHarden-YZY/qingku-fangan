import profitData from './profit_data.json';

export function getProfitItem(it) {
  if (!it) return null;
  return profitData[it] || null;
}

export function getItemCostAndDim(it, rowObj) {
  const p = getProfitItem(it);
  let buy = p ? p.buy : 0;
  let head = p ? p.head : 0;
  let tariff = p ? p.tariff : 0;
  let last = p ? p.last : 0;
  let cbm = p ? p.cbm : 0;
  let weight_lbs = p ? p.weight_lbs : (rowObj?.weight || 0);

  // 兜底回退清库主表字段
  if (buy <= 0 && rowObj?.buy_price > 0) buy = rowObj.buy_price;
  if (head <= 0 && rowObj?.pp_first > 0 && buy > 0) head = Math.max(0, rowObj.pp_first - buy);

  return { buy, head, tariff, last, cbm, weight_lbs };
}

export function getModelCostAndCbm(mRec, subs, recById) {
  if (subs && subs.length > 0) {
    let buy = 0, head = 0, tariff = 0, last = 0, cbm = 0;
    for (const s of subs) {
      const sRow = recById ? recById[s.record_id] : s;
      const info = getItemCostAndDim(s.item || sRow?.item, sRow);
      buy += info.buy;
      head += info.head;
      tariff += info.tariff;
      last += info.last;
      cbm += info.cbm;
    }
    return { buy, head, tariff, last, cbm };
  } else {
    const info = getItemCostAndDim(mRec.item, mRec);
    return {
      buy: info.buy,
      head: info.head,
      tariff: info.tariff,
      last: info.last,
      cbm: info.cbm
    };
  }
}
