/* 水位計算引擎：只讀設定檔，不寫死任何商品代號。瀏覽器與 Node 共用。 */
(function (root) {
  const num = (v) => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const clamp = (x, lo, hi) => Math.max(lo, Math.min(hi, x));
  const round = (x, d) => (x == null ? null : Math.round(x * 10 ** d) / 10 ** d);

  function members(instruments, tag) {
    return instruments.filter((i) => (i.tags || []).includes(tag)).map((i) => i.id);
  }

  // 每個因子類型一個函式；要新增類型就在這裡加一個。
  const FACTOR_TYPES = {
    pct: (f, q) => num(q[f.a]?.pct),
    chg: (f, q) => { const v = num(q[f.a]?.[f.field || 'chg']); return v == null ? null : v * (f.mult ?? 1); },
    level: (f, q) => { const v = num(q[f.a]?.[f.field || 'last']); return v == null ? null : v - (f.center ?? 0); },
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
    avg: (f, q, ins) => {
      const xs = members(ins, f.tag).map((id) => num(q[id]?.pct)).filter((v) => v != null);
      return xs.length ? xs.reduce((s, v) => s + v, 0) / xs.length : null;
    },
    rel_avg: (f, q, ins) => {
      const xs = members(ins, f.tag).map((id) => num(q[id]?.pct)).filter((v) => v != null);
      const b = num(q[f.b]?.pct);
      if (!xs.length || b == null) return null;
      return xs.reduce((s, v) => s + v, 0) / xs.length - b;
    }
  };

  function tierFor(exposure, policy) {
    const tiers = [...policy.tiers].sort((a, b) => b.min - a.min);
    const i = tiers.findIndex((t) => exposure >= t.min);
    return {index: tiers.length - 1 - i, ...tiers[i]};
  }

  /**
   * 計算一天的建議水位。prev 是前一個交易日的結果（computeDay 的回傳值），用於：
   *   policy.maxDailyChange：水位每天最多變動幾個百分點，避免大幅來回跳動；
   *   maRules.buffer：均線緩衝帶（%），指數在均線 ± buffer 之間時維持前一天的站上／跌破判斷。
   * 沒有 prev 時兩者都不作用。要算一整段歷史請用 computeSeries，它會依序帶入 prev。
   */
  function computeDay(quotes, model, instruments, prev) {
    const factors = model.factors.map((f) => {
      const fn = FACTOR_TYPES[f.type];
      const raw = fn ? fn(f, quotes, instruments) : null;
      const score = raw == null ? null : clamp(Number(f.dir) * raw / Number(f.scale), -1, 1);
      return {
        key: f.key, name: f.name, desc: f.desc, type: f.type, weight: Number(f.weight),
        raw: round(raw, 3), score: round(score, 2),
        contrib: score == null ? 0 : round(score * Number(f.weight), 1),
        missing: raw == null
      };
    });
    const p = model.policy;
    const score = round(factors.reduce((s, f) => s + f.contrib, 0), 1);
    const ma = maAdjust(quotes, model, prev?.maState);
    const maPts = ma.reduce((s, r) => s + r.pts, 0);
    const rawExposure = clamp(p.base + p.slope * score + maPts, p.floor, p.cap);
    let exposure = rawExposure;
    const lim = Number(p.maxDailyChange) || 0;
    if (lim > 0 && prev?.exposure != null) exposure = clamp(rawExposure, prev.exposure - lim, prev.exposure + lim);
    exposure = Math.round(exposure);
    const maState = Object.fromEntries(ma.filter((r) => r.above != null).map((r) => [r.n, r.above]));
    return {factors, score, ma, maPts, maState, rawExposure: Math.round(rawExposure), exposure,
      capped: Math.round(rawExposure) !== exposure, tier: tierFor(exposure, p)};
  }

  // 依序計算多天，每天把前一天的結果當作 prev。quotesList 由舊到新。
  function computeSeries(quotesList, model, instruments) {
    let prev = null;
    return quotesList.map((q) => (prev = computeDay(q, model, instruments, prev)));
  }

  // 均線規則：站上均線加 above 個百分點，跌破減 below 個百分點，每條均線分開計算。
  // 緩衝帶 buffer（%）：收盤 ≥ 均線 ×(1+buffer) 才算站上、≤ 均線 ×(1−buffer) 才算跌破，介於中間維持前一天判斷。
  // 均線數值由抓取程式寫在報價的 ma30、ma60… 欄位。
  function maAdjust(quotes, model, prevState) {
    const m = model.maRules;
    if (!m) return [];
    const last = num(quotes[m.a]?.last);
    const b = (Number(m.buffer) || 0) / 100;
    return m.rules.map((r) => {
      const ma = num(quotes[m.a]?.['ma' + r.n]);
      if (last == null || ma == null) return {n: r.n, ma: null, gap: null, above: null, pts: 0, missing: true};
      let above, held = false;
      if (last >= ma * (1 + b)) above = true;
      else if (last <= ma * (1 - b)) above = false;
      else if (prevState && prevState[r.n] != null) { above = prevState[r.n]; held = true; }
      else above = last >= ma;
      return {n: r.n, ma, gap: round((last / ma - 1) * 100, 2), above, held, pts: above ? Number(r.above) : -Number(r.below)};
    });
  }

  function pearson(xs, ys) {
    const n = xs.length;
    if (n < 3) return null;
    const mx = xs.reduce((a, b) => a + b, 0) / n, my = ys.reduce((a, b) => a + b, 0) / n;
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
      const my = ys.reduce((a, b) => a + b, 0) / n, mx = xs.reduce((a, b) => a + b, 0) / n;
      let cov = 0, v = 0; for (let i = 0; i < n; i++) { cov += (xs[i] - mx) * (ys[i] - my); v += (ys[i] - my) ** 2; }
      beta[id] = v ? round(cov / v, 2) : null;
    });
    return {days: days.length, matrix, beta};
  }

  const api = {computeDay, computeSeries, relations, FACTOR_TYPES, members};
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.Engine = api;
})(this);
