/* 實績紀錄：把每天公布的建議水位記到 data/track.json，寫入後不再回頭修改（只有最新一天在當日補抓時可更新）。
   之後即使調整規則，過去的紀錄仍是當時實際公布的數字。用法：node scripts/track.js */
const fs = require('fs');
const path = require('path');
const Engine = require('../assets/engine.js');
const ROOT = path.join(__dirname, '..');
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const OUT = path.join(ROOT, 'data/track.json');
const SINCE = '2026-11-13';  // 目前這套規則（多日觀察天數）定案的第一個交易日

const cfg = read('config/instruments.json'), model = read('config/model.json'), history = read('data/history.json');
const res = Engine.computeSeries(history.map((d) => d.quotes), model, cfg.instruments);
const track = fs.existsSync(OUT) ? JSON.parse(fs.readFileSync(OUT, 'utf8')) : {since: SINCE, rows: []};
const last = track.rows.length ? track.rows[track.rows.length - 1].date : '';
let n = 0;
history.forEach((d, i) => {
  const b = d.quotes[cfg.benchmark];
  if (d.date < track.since || b?.last == null) return;
  const row = {date: d.date, exposure: res[i].exposure, close: b.last, open: b.open ?? null};
  if (d.date > last) { track.rows.push(row); n++; }
  else if (d.date === last && i === history.length - 1) { const old = track.rows[track.rows.length - 1]; track.rows[track.rows.length - 1] = {...row, open: row.open ?? old.open}; }
});
// 開盤價是市場事實、不是訊號：缺的日子可事後從回測資料補上（水位本身不會改）
const BQ = path.join(ROOT, 'data/backtest_quotes.json');
if (track.rows.some((r) => r.open == null) && fs.existsSync(BQ)) {
  const opens = Object.fromEntries(JSON.parse(fs.readFileSync(BQ, 'utf8')).map((d) => [d.date, d.open]));
  track.rows.forEach((r) => { if (r.open == null && opens[r.date]) r.open = Math.round(opens[r.date] * 100) / 100; });
}
track.updatedAt = new Date().toISOString();
fs.writeFileSync(OUT, JSON.stringify(track, null, 1));
console.log(`實績紀錄 ${track.rows.length} 天（新增 ${n}），最新 ${track.rows[track.rows.length - 1]?.date}`);
