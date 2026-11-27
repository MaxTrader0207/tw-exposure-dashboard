/* 水位變動推播：級距改變，或水位較前一交易日變動達門檻（預設 5 個百分點）時，送到 Telegram／LINE。
   需要的 GitHub Secrets（擇一或都設）：
     Telegram：TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID
     LINE Messaging API：LINE_CHANNEL_TOKEN（Channel access token）、LINE_USER_ID
   Repository variable NOTIFY_MIN_CHANGE 可調整門檻；設為 0 表示每天都送。
   同一個資料日只送一次（記在 data/notify_state.json）。用法：node scripts/notify.js [--dry] */
const fs = require('fs');
const path = require('path');
const Engine = require('../assets/engine.js');
const ROOT = path.join(__dirname, '..');
const read = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), 'utf8'));
const STATE = path.join(ROOT, 'data/notify_state.json');
const dry = process.argv.includes('--dry');
const env = process.env;
const URL_PAGE = 'https://maxtrader0207.github.io/tw-exposure-dashboard/';

(async () => {
  const cfg = read('config/instruments.json'), model = read('config/model.json'), history = read('data/history.json');
  if (history.length < 2) return console.log('資料不足，不推播');
  const res = Engine.computeSeries(history.map((d) => d.quotes), model, cfg.instruments);
  const d = history[history.length - 1], t = res[res.length - 1], y = res[res.length - 2];
  const state = fs.existsSync(STATE) ? JSON.parse(fs.readFileSync(STATE, 'utf8')) : {};
  if (state.lastDate === d.date && !dry) return console.log(`${d.date} 已處理過`);
  const raw = env.NOTIFY_MIN_CHANGE, min = raw === undefined || raw === '' ? 5 : Number(raw);
  const delta = t.exposure - y.exposure, st = (r) => r.tier.stance || r.tier.label;
  const tierChanged = st(t) !== st(y);
  const should = tierChanged || Math.abs(delta) >= min;
  const sgn = (v, n = 1) => (v > 0 ? '+' : '') + v.toFixed(n);
  const fs2 = t.factors.filter((f) => f.weight && !f.missing).sort((a, b) => a.contrib - b.contrib);
  const neg = fs2.filter((f) => f.contrib < 0).slice(0, 3), pos = fs2.filter((f) => f.contrib > 0).reverse().slice(0, 2);
  const b = d.quotes[cfg.benchmark];
  const text = [
    `台股水位 ${d.date}`,
    `建議持股 ${t.exposure}%（${st(t)}），較前一日 ${sgn(delta, 0)} 點${tierChanged ? `，由「${st(y)}」轉為「${st(t)}」` : ''}`,
    `加權指數 ${b.last.toLocaleString()}（${sgn(b.pct, 2)}%）｜站上 ${t.ma.filter((r) => r.above).length}/${t.ma.length} 條均線`,
    neg.length ? `扣分：${neg.map((f) => `${f.name} ${sgn(f.contrib)}`).join('、')}` : '',
    pos.length ? `加分：${pos.map((f) => `${f.name} ${sgn(f.contrib)}`).join('、')}` : '',
    URL_PAGE,
  ].filter(Boolean).join('\n');
  if (dry) return console.log(`[試跑] 會送出=${should}\n${text}`);
  const sent = [];
  if (should) {
    if (env.TELEGRAM_BOT_TOKEN && env.TELEGRAM_CHAT_ID) {
      const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {method: 'POST', headers: {'content-type': 'application/json'},
        body: JSON.stringify({chat_id: env.TELEGRAM_CHAT_ID, text, disable_web_page_preview: true})});
      console.log('Telegram', r.status); if (r.ok) sent.push('telegram');
    }
    if (env.LINE_CHANNEL_TOKEN && env.LINE_USER_ID) {
      const r = await fetch('https://api.line.me/v2/bot/message/push', {method: 'POST', headers: {'content-type': 'application/json', authorization: `Bearer ${env.LINE_CHANNEL_TOKEN}`},
        body: JSON.stringify({to: env.LINE_USER_ID, messages: [{type: 'text', text}]})});
      console.log('LINE', r.status); if (r.ok) sent.push('line');
    }
    if (!sent.length) console.log('達到推播條件，但沒有設定可用的 Telegram／LINE 密鑰（或送出失敗）');
  } else console.log(`變動 ${delta} 點、級距未變，不推播`);
  // 已送出或不需送出才記錄；送出失敗時下一次執行（17:00 補抓）會再試
  if (!should || sent.length) fs.writeFileSync(STATE, JSON.stringify({lastDate: d.date, exposure: t.exposure, stance: st(t), sent}, null, 1));
})().catch((e) => { console.error('推播失敗：', e.message); });
