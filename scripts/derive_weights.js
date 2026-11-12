/**
 * 以回測推導因子的權重與觀察天數（交叉驗證，避免過度配適）。
 *
 * 做法
 * 1. 固定權重的因子（--fixed，預設台幣匯率、台積電、費城半導體 = 15）權重不動，其餘權重從 {0,5,10,15,20} 中挑選。
 * 2. 每個因子的觀察天數（window）從 {1,3,5,10,20} 個交易日中挑選：漲跌類取 N 日累計、水準類取 N 日平均。
 *    門檻跟著放大：N 日門檻 = 單日門檻 × (N 日數值的標準差 ÷ 單日數值的標準差)，嚴格程度與單日相同。
 * 3. 權重只能 ≥ 0：方向沿用設定檔的經濟意義，回測顯示反效果的因子權重歸 0，不反向使用。
 * 4. 把樣本切成 6 段連續區間。每次拿 5 段做座標下降法找使夏普值最高的組合，再到剩下那 1 段測試（樣本外）。
 * 5. 最終權重 = 各段結果的中位數；觀察天數 = 各段（權重 > 0 者）最常出現的天數。
 * 其他設定（基準水位、斜率、均線規則、每日變動上限、成本）與 scripts/backtest.js 相同。
 *
 * 用法：node scripts/derive_weights.js [--fixed fx,anchor,sox] [--no-window] [--write]
 *   --no-window 只推導權重（觀察天數維持設定檔現值）；--write 把結果寫回 config/model.json
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
const COST = 0.001, GRID = [0, 5, 10, 15, 20], FOLDS = 6;
const WINS = args.includes('--no-window') ? null : [1, 3, 5, 10, 20];
const r2 = (x, d = 2) => (x == null || !Number.isFinite(x) ? null : Math.round(x * 10 ** d) / 10 ** d);
const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
const F = model.factors, K = F.length, T = days.length;
const scale1 = (f) => Number(f.scale1 ?? f.scale);          // 單日門檻
const curWin = (f) => Math.max(1, Number(f.window) || 1);

// 單日數值與均線點數都與權重、天數無關，先算好
const daily = JSON.parse(JSON.stringify(model));
daily.factors.forEach((f) => { f.window = 1; });
const base = Engine.computeSeries(days.map((d) => d.quotes), daily, ins);
const D = F.map((f, k) => base.map((r) => (r.factors[k].missing ? null : r.factors[k].day)));
const MA = base.map((r) => r.maPts);
const std = (xs) => { const v = xs.filter((x) => x != null); const m = v.reduce((a, b) => a + b, 0) / v.length; return Math.sqrt(v.reduce((a, b) => a + (b - m) ** 2, 0) / Math.max(1, v.length - 1)); };

// 各因子在每種觀察天數下的數值、門檻與分數
const allWins = [...new Set([...(WINS || []), ...F.map(curWin), 1])].sort((a, b) => a - b);
const R = {}, SC = {}, S = {};
F.forEach((f, k) => {
  R[k] = {}; SC[k] = {}; S[k] = {};
  const sd1 = std(D[k]);
  for (const W of allWins) {
    R[k][W] = D[k].map((_, t) => (W === 1 ? D[k][t] : Engine.aggregate({type: f.type, agg: f.agg, window: W}, D[k].slice(Math.max(0, t - W + 1), t + 1))));
    SC[k][W] = W === 1 ? scale1(f) : scale1(f) * std(R[k][W]) / sd1;
    S[k][W] = R[k][W].map((v) => (v == null ? 0 : clamp(Number(f.dir) * v / SC[k][W], -1, 1)));
  }
});

const close = days.map((d) => d.quotes[cfg.benchmark].last), open = days.map((d) => d.open);
const P = model.policy, lim = Number(P.maxDailyChange) || 0;
function exposures(w, win) {
  const e = [];
  for (let t = 0; t < T; t++) {
    let s = 0;
    for (let k = 0; k < K; k++) if (w[k]) s += w[k] * S[k][win[k]][t];
    let x = clamp(P.base + P.slope * s + MA[t], P.floor, P.cap);
    if (lim && t) x = clamp(x, e[t - 1] - lim, e[t - 1] + lim);
    e.push(Math.round(x));
  }
  return e;
}
// 第 t+1 天的報酬（t 從 1 起），與 backtest.js 相同：16:00 決定、隔天開盤調整
function returns(e) {
  const out = [];
  for (let t = 1; t < T - 1; t++) {
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

const nR = T - 2;
const blocks = Array.from({length: FOLDS}, (_, i) => [Math.floor(i * nR / FOLDS), Math.floor((i + 1) * nR / FOLDS)]);
const pick = (rs, idx) => idx.map((i) => rs[i]);
const allIdx = [...Array(nR).keys()];
const isFixed = F.map((f) => fixedKeys.includes(f.key));
const w0 = F.map((f, k) => (isFixed[k] ? 15 : Number(f.weight)));
const win0 = F.map(curWin);

// 座標下降：逐一調整每個因子的（權重、天數），有改善才採用，直到不再變動
function optimize(idx, wins) {
  const w = F.map((f, k) => (isFixed[k] ? 15 : 10)), win = wins ? F.map(() => 1) : [...win0];
  const score = () => sharpe(pick(returns(exposures(w, win)), idx));
  let best = score();
  for (let pass = 0; pass < 8; pass++) {
    let moved = false;
    for (let k = 0; k < K; k++) {
      for (const v of (isFixed[k] ? [15] : GRID)) {
        for (const W of (wins || [win[k]])) {
          if (v === w[k] && W === win[k]) continue;
          if (v === 0 && w[k] === 0) continue;  // 權重 0 時天數沒有差別
          const ow = w[k], oW = win[k]; w[k] = v; win[k] = W;
          const s = score();
          if (s > best + 1e-9) { best = s; moved = true; } else { w[k] = ow; win[k] = oW; }
        }
      }
    }
    if (!moved) break;
  }
  return {w, win, sharpe: best};
}

function crossValidate(wins) {
  const folds = blocks.map(([a, b], i) => {
    const test = allIdx.filter((t) => t >= a && t < b), train = allIdx.filter((t) => t < a || t >= b);
    const o = optimize(train, wins);
    return {fold: i + 1, from: days[a + 2].date, to: days[b + 1].date, weights: o.w, windows: o.win, trainSharpe: r2(o.sharpe), test};
  });
  const oos = new Array(nR);  // 樣本外報酬：每段用「沒看過這段」的設定
  folds.forEach((f) => { const rs = returns(exposures(f.weights, f.windows)); f.test.forEach((t) => { oos[t] = rs[t]; }); });
  folds.forEach((f) => { f.testSharpe = r2(sharpe(pick(oos, f.test))); });
  return {folds, oos};
}
const median = (xs) => { const v = [...xs].sort((a, b) => a - b), m = v.length >> 1; return v.length % 2 ? v[m] : Math.round((v[m - 1] + v[m]) / 2 / 5) * 5; };
const mode = (xs, fallback) => {
  if (!xs.length) return fallback;
  const c = {}; xs.forEach((x) => { c[x] = (c[x] || 0) + 1; });
  const top = Math.max(...Object.values(c)), cand = Object.keys(c).filter((x) => c[x] === top).map(Number).sort((a, b) => a - b);
  return cand.includes(fallback) ? fallback : cand[cand.length >> 1];
};

const cv = crossValidate(WINS);
const full = optimize(allIdx, WINS);
const finalW = F.map((f, k) => (isFixed[k] ? 15 : median(cv.folds.map((x) => x.weights[k]))));
const finalWin = F.map((f, k) => (finalW[k] ? mode(cv.folds.filter((x) => x.weights[k] > 0).map((x) => x.windows[k]), full.win[k]) : 1));
const cvW = WINS ? crossValidate(null) : null;  // 對照：只推導權重、天數全部維持現值
const zeros = F.map(() => 0), ones = F.map(() => 1);
const fixedOnly = F.map((f, k) => (isFixed[k] ? 15 : 0));
const avgExp = exposures(w0, win0).reduce((a, b) => a + b, 0) / T;

const cmp = [
  ['交叉驗證（每段用沒看過該段的設定）', cv.oos],
  ...(cvW ? [['交叉驗證：只推導權重、不改天數', cvW.oos]] : []),
  ['推導前的設定', returns(exposures(w0, win0))],
  ['推導結果 ※ 含樣本內，會高估', returns(exposures(finalW, finalWin))],
  ['全樣本最佳化 ※ 樣本內，會高估', returns(exposures(full.w, full.win))],
  ['只留固定的 3 個因子＋均線', returns(exposures(fixedOnly, win0))],
  ['所有每日因子歸 0（只用均線）', returns(exposures(zeros, ones))],
  [`固定 ${Math.round(avgExp)}%`, returns(new Array(T).fill(Math.round(avgExp)))],
  ['買進持有 100%', returns(new Array(T).fill(100))],
].map(([name, rs]) => ({name, ...perf(rs), byFold: blocks.map(([a, b]) => r2(sharpe(rs.slice(a, b))))}));

// 各因子在不同觀察天數下，與未來 5 日、20 日報酬的等級相關（僅供參考）
const rank = (xs) => { const s = xs.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]); const r = []; s.forEach(([, i], j) => { r[i] = j; }); return r; };
const corr = (a, b) => { const n = a.length, ma = a.reduce((x, y) => x + y, 0) / n, mb = b.reduce((x, y) => x + y, 0) / n; let sab = 0, saa = 0, sbb = 0; for (let i = 0; i < n; i++) { sab += (a[i] - ma) * (b[i] - mb); saa += (a[i] - ma) ** 2; sbb += (b[i] - mb) ** 2; } return saa && sbb ? sab / Math.sqrt(saa * sbb) : null; };
const icOf = (k, W, h) => {
  const xs = [], ys = [];
  for (let t = 0; t + h < T; t++) if (R[k][W][t] != null) { xs.push(R[k][W][t] * Number(F[k].dir)); ys.push(close[t + h] / close[t] - 1); }
  return xs.length > 60 ? r2(corr(rank(xs), rank(ys)), 3) : null;
};
const icTable = F.map((f, k) => ({name: f.name, ...Object.fromEntries((WINS || [win0[k]]).flatMap((W) => [[`${W}日→5日`, icOf(k, W, 5)], [`${W}日→20日`, icOf(k, W, 20)]]))}));

const nice = (x) => { const p = 10 ** (1 - Math.floor(Math.log10(Math.abs(x)))); return Math.round(x * p) / p; };  // 取兩位有效數字
const table = F.map((f, k) => ({
  key: f.key, name: f.name, fixed: isFixed[k], now: Number(f.weight), nowWindow: win0[k],
  folds: cv.folds.map((x) => x.weights[k]), foldWindows: cv.folds.map((x) => (x.weights[k] ? x.windows[k] : null)),
  derived: finalW[k], window: finalWin[k], scale: finalWin[k] === 1 ? scale1(f) : nice(SC[k][finalWin[k]]), scale1: scale1(f),
  n: D[k].filter((v) => v != null).length, fullSample: full.w[k], fullWindow: full.w[k] ? full.win[k] : null,
}));
const out = {generatedAt: new Date().toISOString(), sample: {start: days[2].date, end: days[T - 1].date, days: nR},
  method: `${FOLDS} 段連續區間交叉驗證，座標下降法挑使夏普值最高的權重（0/5/10/15/20，不得為負）${WINS ? `與觀察天數（${WINS.join('/')} 個交易日）` : ''}；最終權重取各段中位數${WINS ? '，觀察天數取各段最常出現者' : ''}。`,
  fixed: fixedKeys, windows: WINS, folds: cv.folds.map(({test, ...r}) => r), table, compare: cmp, ic: icTable};
fs.writeFileSync(path.join(ROOT, 'data/weights_derivation.json'), JSON.stringify(out, null, 1));
console.log(`樣本 ${out.sample.start} ~ ${out.sample.end}（${nR} 天）`);
console.table(table.map(({folds, foldWindows, key, fixed, scale1: _s, ...r}) => ({...r, folds: folds.join('/'), foldWin: foldWindows.map((v) => v ?? '-').join('/')})));
console.table(cv.folds.map(({test, weights, windows, ...r}) => r));
console.table(cmp.map(({byFold, ...r}) => ({...r, byFold: byFold.join(' ')})));
console.table(icTable);
if (args.includes('--write')) {
  F.forEach((f, k) => {
    const t = table[k], sum = (f.agg || (Engine.SUM_TYPES.includes(f.type) ? 'sum' : 'mean')) === 'sum';
    f.desc1 = f.desc1 || f.desc; f.scale1 = t.scale1;
    f.weight = t.derived; f.window = t.window; f.scale = t.scale;
    f.desc = t.window > 1 ? `${f.desc1}【改看近 ${t.window} 個交易日${sum ? '累計' : '平均'}，門檻 ${t.scale}】` : f.desc1;
  });
  fs.writeFileSync(path.join(ROOT, 'config/model.json'), JSON.stringify(model, null, 2));
  console.log('已寫回 config/model.json');
}
