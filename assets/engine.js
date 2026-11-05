/* 水位計算引擎 v2：只讀設定檔，不寫死任何商品代號。瀏覽器與 Node 共用。
 *
 * 每個因子：
 *   原始值 raw → z = clamp((raw − mean) ÷ std, −3, 3) → 對水位影響 = weight × z（百分點）
 *   mean、std、weight 由 scripts/calibrate.py 用歷史資料推導後寫入 config/model.json。
 * 水位 = base + Σ 影響，若啟用波動率覆蓋再乘上 clamp(目標波動 ÷ 實際波動, 下限, 1)，最後限制在 floor～cap。
 *
 * hist：由舊到新的每日報價陣列（每筆為 {商品id: {last, chg, pct}}），最後一筆是要計算的那天。
 * scripts/calibrate.py 的 factor_raw() 是這裡的 Python 對照版，兩邊要一起改。
 */
(function (root) {
  const num = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const round = (x, d) => (x == null ? null : Math.round(x * 10 ** d) / 10 ** d);
  const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
  const sd = (xs) => { const m = mean(xs); return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1)); };

  function members(instruments, tag) {
    return instruments.filter((i) => (i.tags || []).includes(tag)).map((i) => i.id);
  }

  // 單日型因子：只看當天報價。
  const DAY_TYPES = {
    pct: (f, q) => num(q[f.a]?.pct),
    spread: (f, q) => {
      const a = num(q[f.a]?.pct), b = num(q[f.b]?.pct);
      return a == null || b == null ? null : a - b;
    },
    basis: (f, q) => {
      const a = num(q[f.a]?.last), b = num(q[f.b]?.last);
      return a == null || !b ? null : (a - b) / b * 100;
    },
    breadth: (f, q, ins) => {
      const xs = members(ins, f.tag).map((id) => num(q[id]?.pct)).filter((v) => v != null);
      if (!xs.length) return null;
      return (xs.filter((v) => v > 0).length - xs.filter((v) => v < 0).length) / xs.length;
    },
    rel_avg: (f, q, ins) => {
      const xs = members(ins, f.tag).map((id) => num(q[id]?.pct)).filter((v) => v != null);
      const b = num(q[f.b]?.pct);
      if (!xs.length || b == null) return null;
      return mean(xs) - b;
    }
  };

  // 視窗型因子：需要過去 n 個交易日。
  const WINDOW_TYPES = {
    vol: (f, hist) => {
      const xs = hist.slice(-f.n).map((q) => num(q[f.a]?.pct));
      return xs.length < f.n || xs.some((v) => v == null) ? null : sd(xs) * Math.sqrt(252);
    },
    sum_pct: (f, hist) => {
      const xs = hist.slice(-f.n).map((q) => num(q[f.a]?.pct));
      return xs.length < f.n || xs.some((v) => v == null) ? null : xs.reduce((a, b) => a + b, 0);
    },
    ma_gap: (f, hist) => {
      const xs = hist.slice(-f.n).map((q) => num(q[f.a]?.last));
      return xs.length < f.n || xs.some((v) => v == null) ? null : (xs[xs.length - 1] / mean(xs) - 1) * 100;
    }
  };

  function rawAt(f, hist, ins) {
    if (WINDOW_TYPES[f.type]) return WINDOW_TYPES[f.type](f, hist, ins);
    const fn = DAY_TYPES[f.type];
    if (!fn || !hist.length) return null;
    const x = fn(f, hist[hist.length - 1], ins);
    if (x == null || !f.demean) return x;
    // 去均值：減去自身近 demean 日平均，用於去除季節性（例如除息季的期現逆價差）。
    const xs = hist.slice(-f.demean).map((q) => fn(f, q, ins));
    return xs.length < f.demean || xs.some((v) => v == null) ? null : x - mean(xs);
  }

  function tierFor(exposure, policy) {
    const tiers = [...policy.tiers].sort((a, b) => b.min - a.min);
    const i = tiers.findIndex((t) => exposure >= t.min);
    return {index: tiers.length - 1 - i, ...tiers[i]};
  }

  function computeDay(hist, model, instruments) {
    const p = model.policy;
    const factors = model.factors.map((f) => {
      const raw = rawAt(f, hist, instruments);
      const std = Number(f.std) || 1;
      const z = raw == null ? null : clamp((raw - Number(f.mean || 0)) / std, -3, 3);
      const weight = Number(f.weight) || 0;
      return {
        key: f.key, name: f.name, desc: f.desc, type: f.type, weight,
        raw: round(raw, 3), z: round(z, 2),
        pts: z == null ? 0 : round(weight * z, 1),
        missing: raw == null, inactive: weight === 0
      };
    });
    const linear = p.base + factors.reduce((s, f) => s + f.pts, 0);
    let overlay = null;
    const vo = p.volOverlay;
    if (vo && vo.enabled) {
      const vol = WINDOW_TYPES.vol({a: vo.a, n: vo.n}, hist);
      if (vol != null) {
        const mult = clamp(vo.target / vol, vo.minMult ?? 0.5, 1);
        overlay = {vol: round(vol, 1), target: vo.target, mult: round(mult, 2)};
      }
    }
    const exposure = Math.round(clamp(linear * (overlay ? overlay.mult : 1), p.floor, p.cap));
    if (overlay) overlay.pts = round(clamp(linear * overlay.mult, p.floor, p.cap) - clamp(linear, p.floor, p.cap), 1);
    const score = round(exposure - p.base, 1); // 相對基準水位的百分點
    return {factors, linear: round(linear, 1), overlay, score, exposure, tier: tierFor(exposure, p)};
  }

  function pearson(xs, ys) {
    const n = xs.length;
    if (n < 3) return null;
    const mx = mean(xs), my = mean(ys);
    let sxy = 0, sxx = 0, syy = 0;
    for (let i = 0; i < n; i++) { const dx = xs[i] - mx, dy = ys[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy; }
    return sxx && syy ? sxy / Math.sqrt(sxx * syy) : null;
  }

  // 近 window 個交易日的日報酬相關係數與 β（對 benchmark）。
  function relations(history, ids, benchmark, window) {
    const days = history.slice(-window);
    const series = (id) => days.map((d) => num(d.quotes[id]?.pct));
    const pair = (a, b) => {
      const A = series(a), B = series(b), xs = [], ys = [];
      A.forEach((v, i) => { if (v != null && B[i] != null) { xs.push(v); ys.push(B[i]); } });
      return {xs, ys};
    };
    const matrix = ids.map((a) => ids.map((b) => { const {xs, ys} = pair(a, b); return a === b ? 1 : round(pearson(xs, ys), 2); }));
    const beta = {};
    ids.forEach((id) => {
      const {xs, ys} = pair(id, benchmark);
      const n = xs.length; if (n < 3) { beta[id] = null; return; }
      const mx = mean(xs), my = mean(ys);
      let cov = 0, v = 0; for (let i = 0; i < n; i++) { cov += (xs[i] - mx) * (ys[i] - my); v += (ys[i] - my) ** 2; }
      beta[id] = v ? round(cov / v, 2) : null;
    });
    return {days: days.length, matrix, beta};
  }

  const api = {computeDay, relations, rawAt, members, DAY_TYPES, WINDOW_TYPES};
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Engine = api;
})(this);
