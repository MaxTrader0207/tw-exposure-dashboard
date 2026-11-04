"""診斷用：檢查證交所個股表結構與期交所當日 CSV。"""
import json
from datetime import datetime, timedelta, timezone
import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}
d = datetime.now(timezone(timedelta(hours=8)))
ymd, slash = d.strftime("%Y%m%d"), d.strftime("%Y/%m/%d")

j = requests.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json", headers=UA, timeout=60).json()
print("stat", j.get("stat"), "date", j.get("date"), "tables", len(j.get("tables", [])))
for t in j.get("tables", []):
    rows = [r for r in t.get("data", []) if r and r[0] in ("2330", "2308", "1519")]
    if rows:
        print("title", t.get("title")); print("fields", t.get("fields"))
        for r in rows: print("  ", r)

print("\n=== TAIFEX CSV")
r = requests.post("https://www.taifex.com.tw/cht/3/futDataDown", headers=UA, timeout=60, data={
    "down_type": "1", "commodity_id": "TX", "commodity_id2": "", "queryStartDate": slash, "queryEndDate": slash})
print("status", r.status_code, r.headers.get("content-type"), len(r.content))
txt = r.content.decode("big5", errors="replace")
for line in txt.splitlines()[:8]: print("  ", line)

print("\n=== TAIFEX 網頁")
r = requests.post("https://www.taifex.com.tw/cht/3/futDailyMarketReport", headers=UA, timeout=60, data={
    "queryType": "2", "marketCode": "0", "commodity_id": "TX", "queryDate": slash, "MarketCode": "0", "commodity_idt": "TX"})
print("status", r.status_code, len(r.text)); i = r.text.find("202611"); print(r.text[i-200:i+600].replace("\n", " ") if i > 0 else r.text[:300])
