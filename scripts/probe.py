"""診斷用：印出各資料來源的原始回應摘要。"""
import json
from datetime import datetime, timedelta, timezone
import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}
d = datetime.now(timezone(timedelta(hours=8)))
ymd, slash = d.strftime("%Y%m%d"), d.strftime("%Y/%m/%d")
roc = f"{d.year-1911}/{d.strftime('%m/%d')}"

URLS = {
    "twse_openapi_index": "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX",
    "twse_openapi_stocks": "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "twse_rwd_index": f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=IND&response=json",
    "twse_rwd_stocks": f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={ymd}&type=ALLBUT0999&response=json",
    "tpex_openapi": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes",
    "tpex_www": f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={slash}&type=EW&response=json",
    "taifex_openapi": "https://openapi.taifex.com.tw/v1/DailyMarketReportFut",
    "yahoo_dxy": "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?range=5d&interval=1d",
    "yahoo_twd": "https://query1.finance.yahoo.com/v8/finance/chart/TWD%3DX?range=5d&interval=1d",
    "yahoo_2330": "https://query1.finance.yahoo.com/v8/finance/chart/2330.TW?range=5d&interval=1d",
    "yahoo_3324": "https://query1.finance.yahoo.com/v8/finance/chart/3324.TWO?range=5d&interval=1d",
    "yahoo_twii": "https://query1.finance.yahoo.com/v8/finance/chart/%5ETWII?range=5d&interval=1d",
}

def find(obj, needles, depth=0):
    """遞迴找出含指定字串的列。"""
    out = []
    if depth > 6:
        return out
    if isinstance(obj, list):
        if any(isinstance(x, (str, int, float)) for x in obj) and any(n in json.dumps(obj, ensure_ascii=False) for n in needles):
            out.append(obj)
        else:
            for x in obj:
                out += find(x, needles, depth + 1)
    elif isinstance(obj, dict):
        s = json.dumps(obj, ensure_ascii=False)
        if any(f'"{n}"' in s for n in needles) and not any(isinstance(v, (list, dict)) for v in obj.values()):
            out.append(obj)
        else:
            for k, v in obj.items():
                if k in ("fields", "title", "date", "stat"):
                    out.append({k: v})
                out += find(v, needles, depth + 1)
    return out

for name, url in URLS.items():
    print(f"\n===== {name}\n{url}")
    try:
        r = requests.get(url, headers=UA, timeout=30)
        print("status", r.status_code, "len", len(r.text), "ctype", r.headers.get("content-type"))
        try:
            j = r.json()
        except Exception:
            print("非 JSON：", r.text[:300].replace("\n", " "))
            continue
        if name.startswith("yahoo"):
            res = j["chart"]["result"][0]
            m = res["meta"]
            print("meta", {k: m.get(k) for k in ("regularMarketPrice", "chartPreviousClose", "previousClose", "regularMarketTime", "exchangeTimezoneName")})
            print("ts", res.get("timestamp"))
            print("close", res["indicators"]["quote"][0]["close"])
            continue
        if isinstance(j, list):
            print("rows", len(j), "keys", list(j[0].keys()) if j else None)
            print("first", json.dumps(j[0], ensure_ascii=False) if j else None)
        hits = find(j, ["2330", "2308", "2454", "3324", "發行量加權股價指數", "TX"])
        for h in hits[:12]:
            print("  ", json.dumps(h, ensure_ascii=False)[:400])
    except Exception as e:
        print("錯誤", repr(e))
