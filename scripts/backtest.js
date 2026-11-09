/**
 * 回測目前的水位規則。用與網頁相同的 assets/engine.js 計算每天的建議水位，再模擬照表操作的績效。
 *
 * 成交假設
 * - 每天 16:00 算出水位，隔天開盤以開盤價調整部位（無法在當天收盤前得知 16:00 的資料）。
 * - 因此隔天的報酬 ＝ 前一天的水位 × 夜間跳空（昨收→今開）＋ 新水位 × 盤中（今開→今收）。
 * - 標的為加權指數（價格指數，不含股利），交易成本每調整 100 個百分點扣 0.1%（約 0050 手續費折扣後＋證交稅）。
 *
 * 輸出 data/backtest.json：各策略績效、平滑參數比較、各因子邊際貢獻、淨值曲線。
 * 用法：node scripts/backtest.js
 */
const fs = require('fs');
const path = require('path');
const Engine = require('../assets/engine.js');

const ROOT = path.join(__dirname, '..');
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const cfg = read('config/instruments.json');
const baseModel = read('config/model.json');
const days = read('data/backtest_quotes.json');
const ins = cfg.instruments;
const bench = cfg.benchmark;
const COST = 0.001;
const clone = (x) => JSON.parse(JSON.stringify(x));
const r2 = (x, d = 2) => (x == null || !Number.isFinite(x) ? null : Math.round(x * 10 ** d) / 10 ** d);

const close = days.map((d) => d.quotes[bench].last);
const open = days.map((d) => d.open);

function exposures(model) {
  return Engine.computeSeries(days.map((d) => d.quotes), model, ins).map((r) => r.exposure);
}

// 以水位序列模擬：e[t] 在第 t 天收盤後決定，第 t+1 天開盤生效
function simulate(e) {
  const rets = [], dates = [];
  let turnover = 0;
  for (let t = 1; t < days.length - 1; t++) {
    const gap = open[t + 1] / close[t] - 1;
    const intra = close[t + 1] / open[t + 1] - 1;
    const prevE = e[t - 1] / 100, newE = e[t] / 100;
    const cost = Math.abs(newE - prevE) * COST;
    turnover += Math.abs(newE - prevE);
    rets.push(prevE * gap + newE * intra - cost);
    dates.push(days[t + 1].date);
  }
  return {rets, dates, turnover};
}

function metrics(name, e, extra = {}) {
  const {rets, dates, turnover} = simulate(e);
  let eq = 1, peak = 1, mdd = 0;
  const curve = rets.map((r) => { eq *= 1 + r; peak = Math.max(peak, eq); mdd = Math.min(mdd, eq / peak - 1); return eq; });
  const n = rets.length, yrs = n / 252;
  const mean = rets.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(rets.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1));
  const used = e.slice(1, -1);
  const moves = used.slice(1).map((v, i) => Math.abs(v - used[i]));
  return {
    name, ...extra,
    cagr: r2((eq ** (1 / yrs) - 1) * 100),
    vol: r2(sd * Math.sqrt(252) * 100),
    sharpe: r2(mean / sd * Math.sqrt(252)),
    maxdd: r2(mdd * 100),
    calmar: r2(mdd ? ((eq ** (1 / yrs) - 1) / -mdd) : null),
    avgExp: r2(used.reduce((a, b) => a + b, 0) / used.length, 1),
    avgMove: r2(moves.reduce((a, b) => a + b, 0) / moves.length, 1),
    bigMoves: moves.filter((m) => m >= 15).length,
    annualTurnover: r2(turnover / yrs * 100, 0),
    _curve: curve, _dates: dates,
  };
}

const variant = (cap, buffer) => {
  const m = clone(baseModel);
  m.policy.maxDailyChange = cap;
  m.maRules.buffer = buffer;
  return m;
};

// 1. 平滑參數比較
const grid = [];
for (const cap of [0, 5, 10, 15, 20]) {
  for (const buffer of [0, 0.5, 1, 2]) {
    grid.push(metrics(`上限 ${cap || '無'}／緩衝 ${buffer}%`, exposures(variant(cap, buffer)), {cap, buffer}));
  }
}

// 2. 目前設定（設定檔中的上限與緩衝）與對照組
const cur = clone(baseModel);
const eCur = exposures(cur);
const raw = metrics('原始規則（不平滑）', exposures(variant(0, 0)));
const now = metrics(`目前設定（上限 ${cur.policy.maxDailyChange || '無'}／緩衝 ${cur.maRules.buffer || 0}%）`, eCur);
const avg = now.avgExp;
const fixedSame = metrics(`固定 ${Math.round(avg)}%（與目前設定平均水位相同）`, eCur.map(() => Math.round(avg)));
const fixed60 = metrics('固定 60%', eCur.map(() => 60));
const hold = metrics('買進持有 100%', eCur.map(() => 100));
const noMA = clone(cur); delete noMA.maRules;
const maOnly = clone(cur); maOnly.factors.forEach((f) => { f.weight = 0; });
const parts = [metrics('只用因子（拿掉均線規則）', exposures(noMA)), metrics('只用均線規則', exposures(maOnly))];

// 3. 各因子邊際貢獻：拿掉該因子（權重設 0）後績效變化，正值代表這個因子有幫助
const loo = cur.factors.map((f, k) => {
  const m = clone(cur); m.factors[k].weight = 0;
  const x = metrics(f.name, exposures(m));
  return {key: f.key, name: f.name, weight: f.weight,
    dSharpe: r2(now.sharpe - x.sharpe), dCagr: r2(now.cagr - x.cagr), dMaxdd: r2(now.maxdd - x.maxdd)};
});
{
  const x = parts[0];
  loo.push({key: 'ma', name: '均線規則', weight: null, dSharpe: r2(now.sharpe - x.sharpe), dCagr: r2(now.cagr - x.cagr), dMaxdd: r2(now.maxdd - x.maxdd)});
}

// 4. 各因子與未來 5 日報酬的相關（等級相關）
const res = Engine.computeSeries(days.map((d) => d.quotes), cur, ins);
const fwd5 = close.map((c, t) => (t + 5 < close.length ? close[t + 5] / c - 1 : null));
const rank = (xs) => { const s = xs.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]); const r = []; s.forEach(([, i], k) => { r[i] = k; }); return r; };
const corr = (a, b) => { const n = a.length, ma = a.reduce((x, y) => x + y, 0) / n, mb = b.reduce((x, y) => x + y, 0) / n; let sab = 0, saa = 0, sbb = 0; for (let i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; } return saa && sbb ? sab / Math.sqrt(saa * sbb) : null; };
const ic = cur.factors.map((f, k) => {
  const xs = [], ys = [];
  res.forEach((r, t) => { const v = r.factors[k]; if (!v.missing && fwd5[t] != null) { xs.push(v.raw * Number(f.dir)); ys.push(fwd5[t]); } });
  const c = xs.length > 30 ? corr(rank(xs), rank(ys)) : null;
  return {key: f.key, name: f.name, n: xs.length, ic5: r2(c, 3), t: c == null ? null : r2(c * Math.sqrt(xs.length / 5 - 2) / Math.sqrt(1 - c * c))};
});

// 5. 外資買賣超比例分布（決定門檻用）
const fr = days.map((d) => d.quotes.FINI?.ratio).filter((v) => v != null);
const fm = fr.reduce((a, b) => a + b, 0) / fr.length;
const fsd = Math.sqrt(fr.reduce((a, b) => a + (b - fm) ** 2, 0) / (fr.length - 1));

const strip = (m) => { const {_curve, _dates, ...rest} = m; return rest; };
const strategies = [now, raw, ...parts, fixedSame, fixed60, hold];
const out = {
  generatedAt: new Date().toISOString(),
  sample: {start: days[1].date, end: days[days.length - 1].date, days: days.length - 2},
  assumptions: '每天 16:00 算出水位、隔天開盤調整；夜間跳空沿用前一天水位。標的為加權指數（價格指數，不含股利）。交易成本每調整 100 個百分點扣 0.1%。',
  current: {cap: cur.policy.maxDailyChange || 0, buffer: cur.maRules.buffer || 0},
  strategies: strategies.map(strip),
  grid: grid.map(strip),
  leaveOneOut: loo,
  ic,
  finiRatio: {n: fr.length, mean: r2(fm), std: r2(fsd), p5: r2([...fr].sort((a, b) => a - b)[Math.floor(fr.length * 0.05)]), p95: r2([...fr].sort((a, b) => a - b)[Math.floor(fr.length * 0.95)])},
  equity: {dates: now._dates, series: Object.fromEntries([now, raw, fixedSame, hold].map((m) => [m.name, m._curve.map((v) => r2(v, 4))])),
    exposure: eCur.slice(2)},
};
fs.writeFileSync(path.join(ROOT, 'data/backtest.json'), JSON.stringify(out));
console.log(`樣本 ${out.sample.start} ~ ${out.sample.end}，${out.sample.days} 天`);
console.table(out.strategies.map(({name, cagr, vol, sharpe, maxdd, avgExp, avgMove, bigMoves, annualTurnover}) => ({name, cagr, vol, sharpe, maxdd, avgExp, avgMove, bigMoves, annualTurnover})));
console.table(out.grid.map(({cap, buffer, cagr, sharpe, maxdd, avgMove, bigMoves, annualTurnover}) => ({cap, buffer, cagr, sharpe, maxdd, avgMove, bigMoves, annualTurnover})));
console.table(loo);
console.table(ic);
console.log('外資買賣超比例', out.finiRatio);
