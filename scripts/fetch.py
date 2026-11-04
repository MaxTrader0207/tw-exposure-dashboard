"""每日收盤後抓取 config/instruments.json 列出的所有商品，寫入 data/history.json。

來源類型以 ADAPTERS 註冊；新增資料來源 = 新增一個 adapter 函式。
用法：python scripts/fetch.py [--force]（--force：非交易日也寫入，用於手動補資料）
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "instruments.json"
HISTORY = ROOT / "data" / "history.json"
MAX_DAYS = 400
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}

TWSE_STOCKS = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TWSE_INDEX = "https://openapi.twse.com.tw/v1/exchangeReport/MI_INDEX"
TPEX_STOCKS = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TAIFEX_FUT = "https://openapi.taifex.com.tw/v1/DailyMarketReportFut"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=5d&interval=1d"

_cache = {}


def get_json(url):
    if url not in _cache:
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        _cache[url] = r.json()
    return _cache[url]


def num(v):
    if v is None:
        return None
    s = str(v).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "---", "X"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def pick(d, *names):
    for n in names:
        if n in d and d[n] not in (None, ""):
            return d[n]
    return None


def quote(last, chg):
    if last is None or chg is None:
        return None
    prev = last - chg
    return {"last": round(last, 4), "chg": round(chg, 4), "pct": round(chg / prev * 100, 2) if prev else None}


def roc_to_iso(s):
    """'1151104' 或 '115/11/04' → '2026-11-04'；西元 '20261104' 也接受。"""
    s = str(s).replace("/", "").strip()
    if len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    y = int(s[:-4]) + 1911
    return f"{y}-{s[-4:-2]}-{s[-2:]}"


# ---------- adapters：每個回傳 {"last","chg","pct"} 或 None ----------

def twse_stock(symbol):
    for r in get_json(TWSE_STOCKS):
        if pick(r, "Code", "證券代號") == symbol:
            return quote(num(pick(r, "ClosingPrice", "收盤價")), num(pick(r, "Change", "漲跌價差")))
    return None


def tpex_stock(symbol):
    for r in get_json(TPEX_STOCKS):
        if pick(r, "SecuritiesCompanyCode", "代號") == symbol:
            return quote(num(pick(r, "Close", "收盤")), num(pick(r, "Change", "漲跌")))
    return None


def twse_index(symbol):
    for r in get_json(TWSE_INDEX):
        if pick(r, "指數", "Index") == symbol:
            last = num(pick(r, "收盤指數", "ClosingIndex"))
            pts = num(pick(r, "漲跌點數", "Change"))
            sign = -1 if str(pick(r, "漲跌", "Dir") or "").strip().startswith("-") else 1
            if pts is None:
                return None
            return quote(last, sign * abs(pts))
    return None


def taifex_fut(symbol):
    rows = [
        r for r in get_json(TAIFEX_FUT)
        if str(pick(r, "Contract", "契約")).strip() == symbol
        and "/" not in str(pick(r, "ContractMonth(Week)", "到期月份(週別)"))
        and str(pick(r, "TradingSession", "交易時段") or "一般").strip() == "一般"
    ]
    rows = [r for r in rows if num(pick(r, "Last", "收盤價")) is not None]
    if not rows:
        return None
    near = min(rows, key=lambda r: str(pick(r, "ContractMonth(Week)", "到期月份(週別)")).strip())
    return quote(num(pick(near, "Last", "收盤價")), num(pick(near, "Change", "漲跌價")))


def yahoo(symbol):
    res = get_json(YAHOO.format(requests.utils.quote(symbol)))["chart"]["result"][0]
    closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
    last = res["meta"].get("regularMarketPrice") or (closes[-1] if closes else None)
    if last is None or len(closes) < 2:
        return None
    prev = closes[-2] if abs(closes[-1] - last) < 1e-9 else closes[-1]
    return quote(last, last - prev)


ADAPTERS = {
    "twse_stock": twse_stock,
    "tpex_stock": tpex_stock,
    "twse_index": twse_index,
    "taifex_fut": taifex_fut,
    "yahoo": yahoo,
}


def trade_date():
    """以證交所指數資料的日期判斷今天是否為交易日。"""
    for r in get_json(TWSE_INDEX):
        d = pick(r, "日期", "Date")
        if d:
            return roc_to_iso(d)
    return None


def main():
    force = "--force" in sys.argv
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    today = datetime.now(TW).strftime("%Y-%m-%d")
    date = trade_date() or today
    if date != today and not force:
        print(f"證交所最新資料日 {date} ≠ 今天 {today}，判定非交易日或資料未更新，略過。")
        return 0

    quotes, failed = {}, []
    for ins in cfg["instruments"]:
        src = ins["source"]
        fn = ADAPTERS.get(src["type"])
        try:
            q = fn(src["symbol"]) if fn else None
        except Exception as e:  # 單一商品失敗不影響其他商品
            print(f"  {ins['id']} 錯誤：{e}")
            q = None
        if q:
            quotes[ins["id"]] = q
            print(f"  {ins['id']:<8} {q['last']:>12} {q['pct']:+.2f}%")
        else:
            failed.append(ins["id"])

    if not quotes:
        print("所有商品都抓取失敗。")
        return 1

    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    history = [d for d in history if d["date"] != date]
    history.append({
        "date": date,
        "source": "TWSE／TPEx／期交所 OpenAPI、Yahoo Finance（GitHub Actions 自動抓取）",
        "fetchedAt": datetime.now(TW).isoformat(timespec="seconds"),
        "missing": failed,
        "quotes": quotes,
    })
    history.sort(key=lambda d: d["date"])
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(history[-MAX_DAYS:], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {date}：{len(quotes)} 檔成功" + (f"，失敗 {failed}" if failed else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
