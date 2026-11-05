"""診斷用：用真的 Chromium 開財經M平方頁面，看能否通過 Cloudflare 並攔到圖表資料。"""
import json

from playwright.sync_api import sync_playwright

PAGE = "https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin"
with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
    ctx = b.new_context(locale="zh-TW", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36")
    pg = ctx.new_page()
    hits = []
    pg.on("response", lambda r: hits.append(r) if "/charts/data/" in r.url else None)
    r = pg.goto(PAGE, wait_until="domcontentloaded", timeout=60000)
    print("first", r.status if r else None)
    pg.wait_for_timeout(20000)
    print("title", pg.title())
    print("text", pg.inner_text("body")[:400].replace("\n", " "))
    for h in hits:
        print("data", h.url, h.status)
        try:
            j = h.json()
            s = json.dumps(j)[:600]
            print(s)
        except Exception as e:
            print("not json", e)
    b.close()
