/* 儀表板畫面：所有區塊都依 config 與 data 自動產生，不寫死商品。 */
(async function () {
  const $ = (id) => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';
  const el = (tag, attrs = {}, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) { if (k === 'class') n.className = v; else if (v != null) n.setAttribute(k, v); }
    kids.flat().forEach((k) => n.append(k instanceof Node ? k : document.createTextNode(String(k))));
    return n;
  };
  const sv = (tag, attrs = {}, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    return n;
  };
  const sgn = (v, d = 2) => (v == null ? '—' : (v > 0 ? '+' : '') + v.toFixed(d));
  const pct = (v) => (v == null ? '—' : sgn(v) + '%');
  const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');
  const fmtNum = (v) => (v == null ? '—' : v.toLocaleString('zh-TW', {maximumFractionDigits: 3}));
  const bust = '?t=' + Date.now();

  let cfg, model, history;
  try {
    [cfg, model, history] = await Promise.all(
      ['config/instruments.json', 'config/model.json', 'data/history.json'].map((u) => fetch(u + bust).then((r) => r.json()))
    );
  } catch (e) {
    $('meta').textContent = '資料載入失敗：' + e.message;
    return;
  }
  if (!history.length) {
    $('meta').textContent = '尚無資料：等待 GitHub Actions 第一次收盤抓取。';
    return;
  }
  const ins = cfg.instruments;
  const byId = Object.fromEntries(ins.map((i) => [i.id, i]));
  const name = (id) => byId[id]?.name || id;
  const bench = cfg.benchmark;
  const latest = history[history.length - 1];
  const prev = history[history.length - 2];
  const q = latest.quotes;
  const today = Engine.computeDay(q, model, ins);
  const yday = prev ? Engine.computeDay(prev.quotes, model, ins) : null;

  /* ---------- 標頭與磁貼 ---------- */
  $('meta').textContent = `資料日 ${latest.date}｜${latest.source}｜累積 ${history.length} 個交易日`;
  const missing = [...(latest.missing || []), ...ins.filter((i) => !q[i.id] && !(latest.missing || []).includes(i.id)).map((i) => i.id)];
  const missF = today.factors.filter((f) => f.missing).map((f) => f.name);
  if (missing.length || missF.length) {
    $('warn').hidden = false;
    const mmNote = (id) => (byId[id]?.source.type === 'macromicro' ? '（財經M平方無法連線，影響設為 0）' : '');
    $('warn').textContent = `注意：${missing.length ? '缺少報價 ' + missing.map((id) => name(id) + mmNote(id)).join('、') + '。' : ''}${missF.length ? '以下因子無法計算、以 0 分計：' + missF.join('、') + '。' : ''}`;
  }
  const delta = yday ? today.exposure - yday.exposure : null;
  const stanceBadge = (t) => el('span', {class: 'stance ' + (t.tone || 'neutral')}, t.stance || t.label);
  const tile = (label, value, sub, c, hero) => el('div', {class: 'tile' + (hero ? ' hero' : '')}, el('span', {class: 'muted'}, label), el('span', {class: 'v ' + (c || '')}, value), el('span', {class: 'muted'}, sub));
  $('tiles').append(
    el('div', {class: 'tile hero'}, el('span', {class: 'muted'}, '建議持股水位'),
      el('span', {class: 'v hero-row'}, today.exposure + '%', stanceBadge(today.tier)),
      el('span', {class: 'muted'}, `${today.tier.label !== today.tier.stance ? today.tier.label + '｜' : ''}${yday ? `較前一日 ${delta > 0 ? '+' : ''}${delta} 個百分點${yday.tier.stance !== today.tier.stance ? `（由「${yday.tier.stance}」轉為「${today.tier.stance}」）` : ''}` : '佔可投資資金比例'}`)),
    tile('綜合分數', sgn(today.score, 1), '負值偏空、正值偏多', cls(today.score)),
    ...(today.ma.length ? [tile('均線調整', sgn(today.maPts, 0) + ' 點', `站上 ${today.ma.filter((r) => r.above).length}／${today.ma.length} 條均線`, cls(today.maPts))] : []),
    tile(name(bench), pct(q[bench]?.pct), fmtNum(q[bench]?.last), cls(q[bench]?.pct))
  );
  const sorted = [...today.factors].filter((f) => !f.missing).sort((a, b) => a.contrib - b.contrib);
  if (sorted.length) {
    const w = sorted[0], s = sorted[sorted.length - 1];
    $('tiles').append(tile('最大扣分', w.name, sgn(w.contrib, 1) + ' 分', 'down'), tile('最大加分', s.name, sgn(s.contrib, 1) + ' 分', s.contrib > 0 ? 'up' : ''));
  }
  $('tier').append(el('b', {}, today.tier.label + '：'), today.tier.advice);

  /* ---------- 水平長條圖 ---------- */
  function hbar(box, items, {domain, fmt, ref, refLabel}) {
    box.replaceChildren();
    const W = Math.max(300, box.clientWidth || 600), rowH = 30, top = ref != null ? 24 : 6, H = top + items.length * rowH + 6;
    const svg = sv('svg', {viewBox: `0 0 ${W} ${H}`});
    box.append(svg);
    const tw = (s) => { const t = sv('text', {}, s); svg.append(t); const w = t.getComputedTextLength(); t.remove(); return w; };
    const labW = Math.max(...items.map((d) => tw(d.label))) + 12;
    const valW = Math.max(...items.map((d) => tw(fmt(d.value)))) + 10;
    const [d0, d1] = domain, x = (v) => labW + valW + (v - d0) / (d1 - d0) * (W - 2 * valW - labW);
    svg.append(sv('line', {x1: x(0), x2: x(0), y1: top - 4, y2: H, stroke: 'var(--ref)'}));
    const tip = el('div', {class: 'tip'});
    box.append(tip);
    items.forEach((d, i) => {
      const y = top + i * rowH;
      svg.append(sv('text', {x: labW - 8, y: y + rowH / 2, dy: '0.35em', 'text-anchor': 'end', fill: 'var(--fg)'}, d.label));
      svg.append(sv('rect', {x: Math.min(x(0), x(d.value)), y: y + rowH / 2 - 8, height: 16, width: Math.max(1, Math.abs(x(d.value) - x(0))), rx: 3, fill: d.value >= 0 ? 'var(--up)' : 'var(--down)'}));
      svg.append(sv('text', {x: d.value >= 0 ? x(d.value) + 4 : x(d.value) - 4, y: y + rowH / 2, dy: '0.35em', 'text-anchor': d.value >= 0 ? 'start' : 'end', fill: 'var(--muted)'}, fmt(d.value)));
      const hit = sv('rect', {x: 0, y, width: W, height: rowH, fill: 'transparent'});
      hit.addEventListener('mousemove', (e) => {
        const r = box.getBoundingClientRect();
        tip.replaceChildren(el('div', {class: 'muted'}, d.label + '　' + fmt(d.value)), el('div', {}, d.tip || ''));
        tip.style.opacity = 1;
        tip.style.left = Math.min(e.clientX - r.left + 12, box.clientWidth - tip.offsetWidth) + 'px';
        tip.style.top = e.clientY - r.top + 14 + 'px';
      });
      hit.addEventListener('mouseleave', () => { tip.style.opacity = 0; });
      svg.append(hit);
    });
    if (ref != null) {
      svg.append(sv('line', {x1: x(ref), x2: x(ref), y1: top - 6, y2: H, stroke: 'var(--ref)', 'stroke-dasharray': '4 3'}));
      svg.append(sv('text', {x: x(ref), y: 12, 'text-anchor': 'middle', fill: 'var(--muted)'}, refLabel));
    }
  }

  function drawFactor() {
    const m = Math.max(...today.factors.map((f) => f.weight));
    hbar($('factorChart'), today.factors.map((f) => ({label: f.name, value: f.contrib, tip: f.missing ? '資料不足，以 0 分計' : f.desc})), {domain: [-m, m], fmt: (v) => sgn(v, 1)});
  }
  function drawRel() {
    const items = ins.filter((i) => i.id !== bench && !(i.tags || []).some((t) => t === 'fx' || t === 'macro') && q[i.id]?.pct != null)
      .map((i) => ({label: i.name, value: q[i.id].pct, tip: i.role})).sort((a, b) => b.value - a.value);
    const ref = q[bench]?.pct;
    const m = Math.max(0.5, ...items.map((d) => Math.abs(d.value)), Math.abs(ref || 0)) * 1.1;
    hbar($('relChart'), items, {domain: [-m, m], fmt: pct, ref, refLabel: `${name(bench)} ${pct(ref)}`});
  }

  /* ---------- 歷史 ---------- */
  const maNs = (model.maRules?.rules || []).map((r) => r.n);
  const maColors = ['#f59e0b', '#a78bfa', '#38bdf8'];
  const series = history.map((d) => ({date: d.date, ...(({exposure, tier}) => ({exposure, stance: tier.stance || tier.label}))(Engine.computeDay(d.quotes, model, ins)), idx: d.quotes[bench]?.last,
    ma: Object.fromEntries(maNs.map((n) => [n, d.quotes[bench]?.['ma' + n] ?? null]))}));
  function drawHist() {
    const box = $('histChart');
    box.replaceChildren();
    $('histNote').textContent = series.length < 2 ? '目前只有一天資料，每個交易日收盤後會自動累積。' : `近 ${series.length} 個交易日：長條為建議水位（左軸），實線為${name(bench)}（右軸），虛線為 ${maNs.map((n, k) => `${n}MA（${['橘', '紫', '藍'][k % 3]}）`).join('、')}。`;
    const W = Math.max(300, box.clientWidth || 600), H = 240, L = 40, R = 64, T = 12, B = 28;
    const svg = sv('svg', {viewBox: `0 0 ${W} ${H}`});
    box.append(svg);
    const n = series.length, bw = (W - L - R) / n;
    const y1 = (v) => T + (1 - v / 100) * (H - T - B);
    [0, 25, 50, 75, 100].forEach((v) => {
      svg.append(sv('line', {x1: L, x2: W - R, y1: y1(v), y2: y1(v), stroke: 'var(--grid)'}));
      svg.append(sv('text', {x: L - 6, y: y1(v), dy: '0.35em', 'text-anchor': 'end', fill: 'var(--muted)'}, v + '%'));
    });
    series.forEach((d, i) => {
      const w = Math.min(28, bw * 0.7), x = L + i * bw + (bw - w) / 2;
      const r = sv('rect', {x, y: y1(d.exposure), width: w, height: y1(0) - y1(d.exposure), rx: 2, fill: 'var(--accent)', opacity: 0.55});
      r.append(sv('title', {}, `${d.date}　水位 ${d.exposure}%（${d.stance}）　${name(bench)} ${fmtNum(d.idx)}`));
      svg.append(r);
    });
    const idx = series.filter((d) => d.idx != null);
    if (idx.length) {
      const vals = idx.flatMap((d) => [d.idx, ...maNs.map((n) => d.ma[n]).filter((v) => v != null)]);
      const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.1 || hi * 0.01;
      const y2 = (v) => T + (1 - (v - lo + pad) / (hi - lo + 2 * pad)) * (H - T - B);
      const pts = series.map((d, i) => (d.idx == null ? null : [L + i * bw + bw / 2, y2(d.idx)])).filter(Boolean);
      maNs.forEach((n, k) => {
        const mp = series.map((d, i) => (d.ma[n] == null ? null : `${L + i * bw + bw / 2},${y2(d.ma[n])}`)).filter(Boolean);
        if (mp.length > 1) svg.append(sv('polyline', {points: mp.join(' '), fill: 'none', stroke: maColors[k % 3], 'stroke-width': 1.5, 'stroke-dasharray': '5 3'}));
      });
      if (pts.length > 1) svg.append(sv('polyline', {points: pts.map((p) => p.join(',')).join(' '), fill: 'none', stroke: 'var(--fg)', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
      pts.forEach((p) => svg.append(sv('circle', {cx: p[0], cy: p[1], r: 3, fill: 'var(--fg)'})));
      [lo, hi].forEach((v) => svg.append(sv('text', {x: W - R + 6, y: y2(v), dy: '0.35em', fill: 'var(--muted)'}, Math.round(v).toLocaleString())));
    }
    const step = Math.ceil(n / Math.max(1, Math.floor((W - L - R) / 70)));
    series.forEach((d, i) => { if (i % step === 0 || i === n - 1) svg.append(sv('text', {x: L + i * bw + bw / 2, y: H - 8, 'text-anchor': 'middle', fill: 'var(--muted)'}, d.date.slice(5))); });
  }

  /* ---------- 均線位置 ---------- */
  function drawMA() {
    const box = $('maCards');
    if (!today.ma.length) { box.closest('section').hidden = true; return; }
    const rules = model.maRules.rules;
    $('maNote').textContent = `${name(bench)}收盤 ${fmtNum(q[bench]?.last)}。每條均線分開計算：收盤站上（≥）均線水位加分，跌破則減分，直接加在水位上。`;
    today.ma.forEach((r, k) => {
      const rule = rules[k];
      box.append(el('div', {class: 'tile'},
        el('span', {class: 'muted'}, `${r.n} 日均線`),
        el('span', {class: 'v'}, r.ma == null ? '—' : fmtNum(r.ma)),
        el('span', {class: cls(r.gap)}, r.missing ? '歷史不足，暫不計算' : `${r.above ? '站上' : '跌破'}，乖離 ${pct(r.gap)}`),
        el('span', {class: 'muted'}, `水位 `, el('b', {class: cls(r.pts)}, sgn(r.pts, 0) + ' 點'), `（站上 +${rule.above}／跌破 −${rule.below}）`)));
    });
  }

  /* ---------- 今日解讀（依因子類型自動產生） ---------- */
  const tagNames = (tag) => Engine.members(ins, tag).map(name);
  const NARRATE = {
    pct: (f, d) => `${name(d.a)} ${pct(q[d.a]?.pct)}${(byId[d.a]?.tags || []).includes('us') ? '（前一美股交易日）' : ''}。`,
    chg: (f, d) => {
      const v = q[d.a]?.last, u = byId[d.a]?.unit || '';
      if (u === '口') {
        const side = v < 0 ? '淨空單' : '淨多單';
        const grew = (v < 0 && f.raw < 0) || (v > 0 && f.raw > 0);
        return `${name(d.a)} ${side} ${fmtNum(Math.abs(v))} 口，${side}${grew ? '增加' : '減少'} ${fmtNum(Math.abs(f.raw))} 口${v < 0 ? (f.raw < 0 ? '，外資加碼避險或看空' : '，外資回補空單') : ''}。`;
      }
      return `${name(d.a)} ${fmtNum(v)}，變動 ${sgn(f.raw, 1)}${d.mult === 100 ? ' bp' : ''}${(byId[d.a]?.tags || []).includes('us') ? '（前一美股交易日）' : ''}。`;
    },
    level: (f, d) => {
      if (d.a === 'FINI' || (byId[d.a]?.unit === '億' && !d.center)) {
        const v = q[d.a]?.last;
        return `外資${v >= 0 ? '買超' : '賣超'} ${fmtNum(Math.abs(v))} 億元${q[d.a]?.gross ? `，約佔外資成交 ${(Math.abs(v) / q[d.a].gross * 100).toFixed(1)}%` : ''}。`;
      }
      if (q[d.a]?.up != null) {
        const x = q[d.a], idx = q[bench]?.pct;
        return `上漲 ${fmtNum(x.up)} 家、下跌 ${fmtNum(x.down)} 家，廣度 ${sgn(x.last, 1)}%。${idx > 0 && x.last < 0 ? '指數上漲但多數股票下跌，屬權值股撐盤。' : idx < 0 && x.last > 0 ? '指數下跌但多數股票上漲，賣壓集中在權值股。' : ''}`;
      }
      const v = q[d.a]?.last, u = byId[d.a]?.unit || '';
      return `${name(d.a)} ${fmtNum(v)}${u}${byId[d.a]?.note ? `（${byId[d.a].note}）` : ''}${q[d.a]?.asOf ? `（資料日 ${q[d.a].asOf}）` : ''}（前一日 ${q[d.a]?.chg == null ? '—' : sgn(q[d.a].chg, 2) + u}），${f.raw >= 0 ? '高於' : '低於'}中性值 ${d.center}${u} ${Math.abs(f.raw).toFixed(1)}${u}。${d.a === 'MARGIN' && v < 145 ? '已接近斷頭警戒區，留意被動賣壓。' : ''}`;
    },
    spread: (f, d) => {
      const a = q[d.a]?.pct, b = q[d.b]?.pct;
      let s = `${name(d.a)} ${pct(a)}、${name(d.b)} ${pct(b)}，落差 ${sgn(f.raw)} 個百分點。`;
      if (d.a === 'USDTWD' && d.b === 'DXY') {
        if (b < 0 && a > 0) s += '美元走弱台幣卻貶，屬台灣特有的資金流出，通常對應外資賣超匯出。';
        else if (b > 0 && a < 0) s += '美元走強台幣仍升，代表資金明顯流入台灣。';
      }
      return s;
    },
    basis: (f, d) => {
      const pts = (q[d.a]?.last ?? NaN) - (q[d.b]?.last ?? NaN);
      return `${name(d.a)} 對 ${name(d.b)} ${pts >= 0 ? '正' : '逆'}價差 ${sgn(pts)} 點（${sgn(f.raw, 3)}%）。${pts >= 0 ? '期貨不比現貨悲觀，賣壓偏現貨端。' : '期貨較現貨悲觀，留意期貨空方主導。'}`;
    },
    breadth: (f, d) => {
      const xs = Engine.members(ins, d.tag).filter((id) => q[id]);
      const up = xs.filter((id) => q[id].pct > 0), dn = xs.filter((id) => q[id].pct < 0);
      return `${tagNames(d.tag).join('、')} 中上漲 ${up.length} 檔、下跌 ${dn.length} 檔${up.length && up.length <= 2 ? `（逆勢：${up.map(name).join('、')}）` : ''}。`;
    },
    rel_avg: (f, d) => {
      const xs = Engine.members(ins, d.tag).filter((id) => q[id]).sort((a, b) => q[a].pct - q[b].pct);
      return `族群相對 ${name(d.b)} ${sgn(f.raw)} 個百分點；最弱 ${name(xs[0])} ${pct(q[xs[0]]?.pct)}，最強 ${name(xs[xs.length - 1])} ${pct(q[xs[xs.length - 1]]?.pct)}。`;
    }
  };
  function drawInsights() {
    const box = $('insights');
    const defs = Object.fromEntries(model.factors.map((d) => [d.key, d]));
    const neg = sorted.filter((f) => f.contrib < 0), pos = sorted.filter((f) => f.contrib > 0).reverse();
    box.append(el('div', {class: 'insight'}, el('b', {}, '總結：'),
      `${neg.length ? '扣分主要來自' + neg.slice(0, 3).map((f) => f.name).join('、') : '沒有明顯扣分因子'}；${pos.length ? '緩衝來自' + pos.slice(0, 2).map((f) => f.name).join('、') : '沒有因子加分'}。${yday ? `水位較前一日 ${delta >= 0 ? '上調' : '下調'} ${Math.abs(delta)} 個百分點。` : ''}`));
    if (today.ma.length) {
      const up = today.ma.filter((r) => r.above), dn = today.ma.filter((r) => r.above === false);
      box.append(el('div', {class: 'insight ' + (today.maPts < 0 ? 'neg' : today.maPts > 0 ? 'pos' : '')},
        el('b', {}, `均線位置（水位 ${sgn(today.maPts, 0)} 點）：`),
        `${up.length ? '站上 ' + up.map((r) => r.n + 'MA').join('、') : ''}${up.length && dn.length ? '；' : ''}${dn.length ? '跌破 ' + dn.map((r) => `${r.n}MA（${pct(r.gap)}）`).join('、') : ''}。${up.length === today.ma.length ? '多頭排列，趨勢支撐完整。' : dn.length === today.ma.length ? '三條均線全數跌破，中長期趨勢轉弱。' : '均線之間，趨勢分歧。'}`));
    }
    today.factors.forEach((f) => {
      const d = defs[f.key];
      const text = f.missing ? (byId[d.a]?.source.type === 'macromicro' ? '財經M平方無法連線，今日影響設為 0。' : '資料不足，今日以 0 分計。') : (NARRATE[d.type] ? NARRATE[d.type](f, d) : '');
      box.append(el('div', {class: 'insight ' + (f.contrib < 0 ? 'neg' : f.contrib > 0 ? 'pos' : '')},
        el('b', {}, `${f.name}（${sgn(f.contrib, 1)} 分）：`), text));
    });
  }

  /* ---------- 商品關聯卡片與相關係數 ---------- */
  const win = model.correlationWindow || 20;
  const ids = ins.map((i) => i.id);
  const rel = Engine.relations(history, ids, bench, win);
  function drawCards() {
    $('relNote').textContent = rel.days < 3 ? `β 與相關係數需要至少 3 個交易日資料，目前 ${rel.days} 天，會隨每日自動更新累積。` : `β 與相關係數以近 ${rel.days} 個交易日的日報酬計算（對${name(bench)}）。`;
    const bi = ids.indexOf(bench);
    ins.forEach((i, k) => {
      const p = q[i.id]?.pct, rb = p != null && q[bench]?.pct != null ? p - q[bench].pct : null;
      const corrRow = rel.matrix[k].map((c, j) => ({id: ids[j], c})).filter((x) => x.id !== i.id && x.c != null).sort((a, b) => b.c - a.c);
      const partner = corrRow[0];
      const peers = (i.tags || []).flatMap((t) => Engine.members(ins, t)).filter((x) => x !== i.id);
      const card = el('div', {class: 'icard'},
        el('div', {class: 'top'}, el('b', {}, i.name), el('span', {class: 'pct ' + cls(p)}, pct(p))),
        el('div', {}, el('span', {class: 'tag'}, i.group), ...(i.tags || []).map((t) => el('span', {class: 'tag'}, t))),
        el('p', {class: 'muted'}, (i.role || '') + (i.note ? '（' + i.note + '）' : '')),
        el('div', {class: 'stats'},
          el('div', {}, '相對大盤', el('b', {class: cls(rb)}, i.id === bench ? '—' : sgn(rb))),
          el('div', {}, `β（${rel.days}日）`, el('b', {}, rel.beta[i.id] == null || i.id === bench ? '—' : rel.beta[i.id].toFixed(2))),
          el('div', {}, '與大盤相關', el('b', {}, i.id === bench || rel.matrix[k][bi] == null ? '—' : rel.matrix[k][bi].toFixed(2)))),
        el('p', {class: 'muted'},
          (peers.length ? `同族群：${[...new Set(peers)].map(name).join('、')}。` : '') +
          (partner ? `近期最同步：${name(partner.id)}（${partner.c.toFixed(2)}）。` : ''))
      );
      $('instCards').append(card);
    });
  }
  function drawCorr() {
    const box = $('corr');
    if (rel.days < 5) { $('corrNote').textContent = `需要至少 5 個交易日資料才有意義，目前 ${rel.days} 天。`; return; }
    $('corrNote').textContent = `近 ${rel.days} 個交易日日報酬的皮爾森相關係數；紅色越深越同向，綠色越深越反向。`;
    const t = el('table', {class: 'heat'});
    t.append(el('tr', {}, el('th', {}, ''), ...ins.map((i) => el('th', {}, i.name))));
    ins.forEach((i, a) => t.append(el('tr', {}, el('th', {}, i.name), ...rel.matrix[a].map((c, b) => {
      const td = el('td', {}, c == null ? '—' : c.toFixed(2));
      if (c != null && a !== b) td.style.background = c >= 0 ? `rgba(239,68,68,${Math.abs(c) * 0.55})` : `rgba(34,197,94,${Math.abs(c) * 0.55})`;
      return td;
    }))));
    box.append(t);
  }

  /* ---------- 試算器 ---------- */
  // 依因子類型決定每個商品要輸入哪個欄位：漲跌幅、漲跌值或數值本身
  const need = {};
  const add = (id, c) => { if (id) (need[id] ||= new Set()).add(c); };
  model.factors.forEach((f) => {
    if (['pct', 'spread'].includes(f.type)) { add(f.a, 'pct'); add(f.b, 'pct'); }
    if (f.type === 'chg') add(f.a, 'chg');
    if (['basis', 'level'].includes(f.type)) { add(f.a, 'last'); add(f.b, 'last'); }
    if (['breadth', 'rel_avg'].includes(f.type)) { Engine.members(ins, f.tag).forEach((id) => add(id, 'pct')); add(f.b, 'pct'); }
  });
  const colLabel = {last: (i) => `${i.name}${i.unit ? '（' + i.unit + '）' : ' 數值'}`, pct: (i) => `${i.name} 漲跌 %`, chg: (i) => `${i.name} 漲跌`};
  const fields = [];
  ins.forEach((i) => ['last', 'pct', 'chg'].forEach((c) => { if (need[i.id]?.has(c)) fields.push({id: i.id, col: c, label: colLabel[c](i)}); }));
  const inputs = fields.map((f) => {
    const input = el('input', {type: 'number', step: 'any', inputmode: 'decimal'});
    input.dataset.id = f.id; input.dataset.col = f.col;
    input.addEventListener('input', runSim);
    $('simForm').append(el('label', {}, f.label, input));
    return input;
  });
  const fill = () => inputs.forEach((i) => { i.value = q[i.dataset.id]?.[i.dataset.col] ?? ''; });
  $('simReset').addEventListener('click', () => { fill(); runSim(); });
  function runSim() {
    const sq = {};
    inputs.forEach((i) => { if (i.value === '') return; (sq[i.dataset.id] ||= {})[i.dataset.col] = Number(i.value); });
    // 均線是過去的收盤平均，試算時沿用最新數值，只拿輸入的指數點位去比較
    if (model.maRules && sq[model.maRules.a]) maNs.forEach((n) => { sq[model.maRules.a]['ma' + n] = q[model.maRules.a]?.['ma' + n]; });
    const r = Engine.computeDay(sq, model, ins);
    const out = $('simOut');
    out.replaceChildren(
      el('span', {class: 'muted'}, '建議持股水位'),
      el('div', {class: 'big'}, r.exposure + '%'),
      el('p', {}, stanceBadge(r.tier), ' ', ...(r.tier.label !== r.tier.stance ? [el('b', {}, r.tier.label), '｜'] : []), '綜合分數 ', el('b', {class: cls(r.score)}, sgn(r.score, 1)))
    );
    const m = Math.max(...r.factors.map((f) => f.weight));
    const bars = el('div', {class: 'bars'});
    r.factors.forEach((f) => {
      const w = Math.abs(f.contrib) / m * 50;
      const bar = el('i');
      bar.style.left = (f.contrib >= 0 ? 50 : 50 - w) + '%'; bar.style.width = w + '%';
      bar.style.background = f.contrib >= 0 ? 'var(--up)' : 'var(--down)';
      bars.append(el('div', {class: 'brow'}, el('span', {}, f.name), el('span', {class: 'track'}, bar, el('s')), el('span', {class: 'num ' + cls(f.contrib)}, f.missing ? '—' : sgn(f.contrib, 1))));
    });
    r.ma.forEach((a) => {
      const w = Math.abs(a.pts) / m * 50;
      const bar = el('i');
      bar.style.left = (a.pts >= 0 ? 50 : 50 - w) + '%'; bar.style.width = w + '%';
      bar.style.background = a.pts >= 0 ? 'var(--up)' : 'var(--down)';
      bars.append(el('div', {class: 'brow'}, el('span', {}, `${a.n}MA ${a.missing ? '' : a.above ? '站上' : '跌破'}`), el('span', {class: 'track'}, bar, el('s')), el('span', {class: 'num ' + cls(a.pts)}, a.missing ? '—' : sgn(a.pts, 0) + '點')));
    });
    out.append(bars, el('p', {class: 'muted'}, `${model.policy.base}% + ${model.policy.slope} × ${sgn(r.score, 1)}${r.ma.length ? ` ${r.maPts >= 0 ? '+' : '−'} ${Math.abs(r.maPts)}（均線）` : ''} = ${r.exposure}%（限制在 ${model.policy.floor}%～${model.policy.cap}%）`));
  }

  /* ---------- 表格 ---------- */
  function drawTables() {
    const p = model.policy;
    $('policyNote').textContent = `建議水位 = ${p.base}% + ${p.slope} × 綜合分數${model.maRules ? ' + 均線調整（' + model.maRules.rules.map((r) => `${r.n}MA 站上 +${r.above}／跌破 −${r.below}`).join('、') + '）' : ''}，限制在 ${p.floor}%–${p.cap}%。分數 = clamp(方向 × 原始值 ÷ 門檻, −1, 1) × 權重。`;
    const defs = Object.fromEntries(model.factors.map((d) => [d.key, d]));
    const ft = $('factorTable');
    ft.append(el('tr', {}, ...['因子', '類型', '權重', '門檻', '原始值', '分數', '貢獻', '定義'].map((h, i) => el('th', {class: i >= 2 && i <= 6 ? 'n' : ''}, h))));
    today.factors.forEach((f) => ft.append(el('tr', {},
      el('td', {}, f.name), el('td', {}, f.type), el('td', {class: 'n'}, f.weight), el('td', {class: 'n'}, defs[f.key].scale),
      el('td', {class: 'n'}, f.raw ?? '—'), el('td', {class: 'n ' + cls(f.score)}, f.score == null ? '—' : sgn(f.score)),
      el('td', {class: 'n ' + cls(f.contrib)}, sgn(f.contrib, 1)), el('td', {class: 'w'}, f.desc))));
    const qt = $('quoteTable');
    qt.append(el('tr', {}, ...['商品', '代號', '來源', '類別', '成交', '漲跌', '幅度'].map((h, i) => el('th', {class: i >= 4 ? 'n' : ''}, h))));
    ins.forEach((i) => {
      const r = q[i.id] || {};
      qt.append(el('tr', {}, el('td', {}, i.name, ...(i.note ? [el('div', {class: 'muted'}, '※ ' + i.note)] : [])), el('td', {}, i.source.symbol || '—'), el('td', {}, i.source.type), el('td', {}, i.group),
        el('td', {class: 'n ' + cls(r.chg)}, fmtNum(r.last) + (i.unit && r.last != null ? i.unit : '')), el('td', {class: 'n ' + cls(r.chg)}, r.chg == null ? '—' : sgn(r.chg, 2)), el('td', {class: 'n ' + cls(r.pct)}, pct(r.pct))));
    });
  }

  const redraw = () => { drawFactor(); drawRel(); drawHist(); };
  redraw(); drawMA(); drawInsights(); drawCards(); drawCorr(); fill(); runSim(); drawTables();
  let t; window.addEventListener('resize', () => { clearTimeout(t); t = setTimeout(redraw, 150); });
})();
