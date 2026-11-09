/**
 * 以回測推導因子權重（交叉驗證，避免過度配適）。
 *
 * 做法
 * 1. 固定權重的因子（--fixed，預設台幣匯率、台積電、費城半導體 = 15）不動，其餘權重從 {0,5,10,15,20} 中挑選。
 * 2. 權重只能 ≥ 0：方向沿用設定檔的經濟意義，回測顯示反效果的因子權重歸 0，不反向使用。
 * 3. 把樣本切成 5 段連續區間。每次拿 4 段做座標下降法找使夏普值最高的權重，再到剩下那 1 段測試（樣本外）。
 * 4. 最終權重 = 5 次結果的中位數（不受單一段極端結果影響）；同時列出全樣本最佳化結果與樣本外績效供比較。
 * 其他設定（基準水位、斜率、均線規則、每日變動上限、成本）與 scripts/backtest.js 相同。
 *
 * 用法：node scripts/derive_weights.js [--fixed fx,anchor,sox] [--write]
 *   --write 會把推導出的權重寫回 config/model.json
 */
const fs = require('fs');
const path = require('path');
const Engine = require('../assets/engine.js');

const ROOT = path.join(__dirname, '..');
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const args = process.argv.slice(2);
const fixedKeys = (args.includes('--fixed') ? args[args.indexOf('--fixed') + 1] : 'fx,anchor,sox').split(',');
const model = read('config/model.json');
const cfg = read('config/instruments.json');
const days = read('data/backtest_quotes.json');
const ins = cfg.instruments;
const COST = 0.001, GRID = [0, 5, 10, 15, 20], FOLDS = 5;
const r2 = (x, d = 2) => (x == null || !Number.isFinite(x) ? null : Math.round(x * 10 ** d) / 10 ** d);

// 每天各因子分數（−1～1）與均線點數都與權重無關，先算好
const base = Engine.computeSeries(days.map((d) => d.quotes), model, ins);
const S = model.factors.map((f, k) => base.map((r) => r.factors[k].score ?? 0));
const MA = base.map((r) => r.maPts);
const close = days.map((d) => d.quotes[cfg.benchmark].last), open = days.map((d) => d.open);
const P = model.policy, lim = Number(P.maxDailyChange) || 0;
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));

function exposures(w) {
  const e = [];
  for (let t = 0; t < days.length; t++) {
    let s = 0;
    for (let k = 0; k < w.length; k++) s += w[k] * S[k][t];
    let x = clamp(P.base + P.slope * s + MA[t], P.floor, P.cap);
    if (lim && t) x = clamp(x, e[t - 1] - lim, e[t - 1] + lim);
    e.push(Math.round(x));
  }
  return e;
}
// 第 t+1 天的報酬（t 從 1 起），與 backtest.js 相同
function returns(e) {
  const out = [];
  for (let t = 1; t < days.length - 1; t++) {
    const gap = open[t + 1] / close[t] - 1, intra = close[t + 1] / open[t + 1] - 1;
    out.push(e[t - 1] / 100 * gap + e[t] / 100 * intra - Math.abs(e[t] - e[t - 1]) / 100 * COST);
  }
  return out;
}
const sharpe = (rs) => {
  const n = rs.length, m = rs.reduce((a, b) => a + b, 0) / n;
  const sd = Math.sqrt(rs.reduce((a, b) => a + (b - m) ** 2, 0) / (n - 1));
  return sd ? m / sd * Math.sqrt(252) : 0;
};
const perf = (rs) => {
  let eq = 1, pk = 1, dd = 0;
  rs.forEach((r) => { eq *= 1 + r; pk = Math.max(pk, eq); dd = Math.min(dd, eq / pk - 1); });
  return {sharpe: r2(sharpe(rs)), cagr: r2((eq ** (252 / rs.length) - 1) * 100), maxdd: r2(dd * 100)};
};

const nR = days.length - 2;
const blocks = Array.from({length: FOLDS}, (_, i) => [Math.floor(i * nR / FOLDS), Math.floor((i + 1) * nR / FOLDS)]);
const pick = (rs, idx) => idx.map((i) => rs[i]);
const allIdx = [...Array(nR).keys()];
const free = model.factors.map((f, k) => (fixedKeys.includes(f.key) ? null : k)).filter((k) => k != null);
const w0 = model.factors.map((f) => (fixedKeys.includes(f.key) ? 15 : Number(f.weight)));

function optimize(idx) {
  const w = [...w0];
  let best = sharpe(pick(returns(exposures(w)), idx));
  for (let pass = 0; pass < 6; pass++) {
    let moved = false;
    for (const k of free) {
      for (const v of GRID) {
        if (v === w[k]) continue;
        const old = w[k]; w[k] = v;
        const s = sharpe(pick(returns(exposures(w)), idx));
        if (s > best + 1e-9) { best = s; moved = true; } else w[k] = old;
      }
    }
    if (!moved) break;
  }
  return {w, sharpe: best};
}

// 交叉驗證
const folds = blocks.map(([a, b], i) => {
  const test = allIdx.filter((t) => t >= a && t < b), train = allIdx.filter((t) => t < a || t >= b);
  const o = optimize(train);
  return {fold: i + 1, from: days[a + 2].date, to: days[b + 1].date, weights: o.w, trainSharpe: r2(o.sharpe), test};
});
// 樣本外報酬：每段用「沒看過這段」的權重
function oos(wOf) {
  const out = new Array(nR);
  folds.forEach((f) => { const rs = returns(exposures(wOf(f))); f.test.forEach((t) => { out[t] = rs[t]; }); });
  return out;
}
const median = (xs) => { const v = [...xs].sort((a, b) => a - b), m = v.length >> 1; return v.length % 2 ? v[m] : Math.round((v[m - 1] + v[m]) / 2 / 5) * 5; };
const avgW = model.factors.map((f, k) => (fixedKeys.includes(f.key) ? 15 : median(folds.map((x) => x.weights[k]))));
const full = optimize(allIdx);
const zero = model.factors.map((f) => (fixedKeys.includes(f.key) ? 15 : 0));
const fixedE = (v) => returns(new Array(days.length).fill(v));

const cmp = [
  ['交叉驗證（每段用沒看過該段的權重）', oos((f) => f.weights)],
  ['目前權重', returns(exposures(w0))],
  ['推導權重（5 段中位數）※ 含樣本內', returns(exposures(avgW))],
  ['全樣本最佳化 ※ 樣本內，會高估', returns(exposures(full.w))],
  ['只留固定的 3 個因子＋均線', returns(exposures(zero))],
  ['所有每日因子歸 0（只用均線）', returns(exposures(model.factors.map(() => 0)))],
].map(([name, rs]) => ({name, ...perf(rs)}));
const avgExp = exposures(w0).reduce((a, b) => a + b, 0) / days.length;
cmp.push({name: `固定 ${Math.round(avgExp)}%`, ...perf(fixedE(Math.round(avgExp)))});

const table = model.factors.map((f, k) => ({
  key: f.key, name: f.name, fixed: fixedKeys.includes(f.key), now: Number(f.weight),
  folds: folds.map((x) => x.weights[k]), derived: avgW[k], fullSample: full.w[k],
}));
const out = {generatedAt: new Date().toISOString(), sample: {start: days[2].date, end: days[days.length - 1].date, days: nR},
  method: '5 段連續區間交叉驗證，座標下降法在 {0,5,10,15,20} 中挑使夏普值最高的權重，權重不得為負；最終權重取 5 段結果的中位數。',
  fixed: fixedKeys, folds: folds.map(({test, ...r}) => r), table, compare: cmp};
fs.writeFileSync(path.join(ROOT, 'data/weights_derivation.json'), JSON.stringify(out, null, 1));
console.log(`樣本 ${out.sample.start} ~ ${out.sample.end}（${nR} 天）`);
console.table(table.map(({folds, ...r}) => ({...r, folds: folds.join('/')})));
console.table(folds.map(({test, weights, ...r}) => r));
console.table(cmp);
if (args.includes('--write')) {
  model.factors.forEach((f, k) => { f.weight = avgW[k]; });
  fs.writeFileSync(path.join(ROOT, 'config/model.json'), JSON.stringify(model, null, 2));
  console.log('已寫回 config/model.json');
}
