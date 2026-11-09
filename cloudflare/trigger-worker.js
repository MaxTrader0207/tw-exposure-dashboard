/**
 * 台股水位儀表板：準時觸發器
 *
 * GitHub Actions 的免費排程常延遲數小時，這個 Cloudflare Worker 用 Cron Triggers 準時呼叫
 * GitHub API 的 workflow_dispatch，讓「每日更新水位資料」在收盤後立刻執行。
 *
 * 需要設定（Cloudflare 後台 → Worker → Settings）：
 *   Variables and Secrets → GH_TOKEN（Secret）：GitHub fine-grained token，
 *     只授權 MaxTrader0207/tw-exposure-dashboard，權限 Actions: Read and write。
 *   Triggers → Cron Triggers（UTC 時間）：
 *     0 8 * * MON-FRI    → 台北 16:00（外資、期貨三大法人等資料都已公布）
 *     0 9 * * MON-FRI    → 台北 17:00（補抓，資料延遲時）
 *
 * 開啟 Worker 網址會顯示最近 5 次執行狀態，方便確認有沒有準時跑。
 */
const REPO = 'MaxTrader0207/tw-exposure-dashboard';
const WORKFLOW = 'update.yml';
const API = 'https://api.github.com';

function headers(env) {
  return {
    Authorization: `Bearer ${env.GH_TOKEN}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'tw-exposure-trigger',
  };
}

async function dispatch(env) {
  const r = await fetch(`${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: 'POST',
    headers: {...headers(env), 'Content-Type': 'application/json'},
    body: JSON.stringify({ref: 'main'}),
  });
  if (r.status !== 204) throw new Error(`GitHub 回應 ${r.status}：${await r.text()}`);
}

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(
      dispatch(env)
        .then(() => console.log(`已觸發 ${WORKFLOW}（cron ${event.cron}）`))
        .catch((e) => console.error(`觸發失敗：${e.message}`))
    );
  },

  // 只顯示狀態，不提供手動觸發，避免網址被他人拿來重複執行
  async fetch(request, env) {
    if (!env.GH_TOKEN) return new Response('尚未設定 GH_TOKEN', {status: 500});
    const r = await fetch(`${API}/repos/${REPO}/actions/workflows/${WORKFLOW}/runs?per_page=5`, {headers: headers(env)});
    if (!r.ok) return new Response(`無法讀取 GitHub（${r.status}），請確認 GH_TOKEN 權限`, {status: 502});
    const runs = (await r.json()).workflow_runs || [];
    const tw = (t) => new Date(t).toLocaleString('zh-TW', {timeZone: 'Asia/Taipei', hour12: false});
    const lines = runs.map((x) => `${tw(x.created_at)}　${x.event === 'workflow_dispatch' ? '準時觸發' : 'GitHub 排程'}　${x.conclusion || x.status}`);
    return new Response(`台股水位儀表板｜最近 5 次更新\n\n${lines.join('\n')}\n`, {headers: {'Content-Type': 'text/plain; charset=utf-8'}});
  },
};
