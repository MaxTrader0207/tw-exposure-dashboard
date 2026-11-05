"""抓取 config/instruments.json 列出的所有商品收盤資料，寫入 data/history.json。

來源類型以 ADAPTERS 註冊；新增資料來源 = 新增一個 adapter 函式。
每個 adapter 都會核對資料日期，日期不符就回傳 None，不會把舊資料寫成今天。

用法：
  python scripts/fetch.py                     抓今天（非交易日自動略過）
  python scripts/fetch.py --date 2026-11-03   抓指定日期
  python scripts/fetch.py --backfill 30       回補最近 30 個日曆日中的交易日
"""
import argparse
import csv
import io
import json
import re
import sys
import time
from datetime import date as Date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "instruments.json"
HISTORY = ROOT / "data" / "history.json"
MAX_DAYS = 400
TW = timezone(timedelta(hours=8))
UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}
PAUSE = 2.5  # 證交所對連續請求敏感，每次網路請求間隔

_cache = {}
_last_req = [0.0]


def http(method, url, **kw):
    key = (method, url, json.dumps(kw.get("data"), sort_keys=True))
    if key not in _cache:
        wait = PAUSE - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                r = requests.request(method, url, headers=UA, timeout=60, **kw)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(5 * (attempt + 1))
        _last_req[0] = time.time()
        _cache[key] = r
    return _cache[key]


def num(v):
    if v is None:
        return None
    s = re.sub(r"<[^>]+>", "", str(v)).replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--", "---", "X", "NULL"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def html_sign(v):
    """證交所以 HTML 標示漲跌方向：含 '-' 為跌。"""
    s = re.sub(r"<[^>]+>", "", str(v)).strip()
    return -1 if s.startswith("-") else 1


def quote(last, chg):
    if last is None or chg is None:
        return None
    prev = last - chg
    return {"last": round(last, 4), "chg": round(chg, 4), "pct": round(chg / prev * 100, 2) if prev else None}


def roc(d):
    return f"{d.year - 1911}{d:%m%d}"


# ---------- 證交所（網站版 API，收盤後當天即有資料） ----------

def twse_mi_index(d, kind):
    j = http("GET", f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type={kind}&response=json").json()
    if j.get("stat") != "OK" or j.get("date") != f"{d:%Y%m%d}":
        return []
    return j.get("tables", [])


def twse_index(symbol, d):
    for t in twse_mi_index(d, "IND"):
        f = t.get("fields") or []
        if "收盤指數" not in f:
            continue
        for r in t.get("data", []):
            if r and r[0].strip() == symbol:
                return quote(num(r[f.index("收盤指數")]), html_sign(r[2]) * num(r[f.index("漲跌點數")]))
    return None


def twse_stock(symbol, d):
    for t in twse_mi_index(d, "ALLBUT0999"):
        f = t.get("fields") or []
        if "證券代號" not in f:
            continue
        for r in t.get("data", []):
            if r and r[0].strip() == symbol:
                pts = num(r[f.index("漲跌價差")])
                return quote(num(r[f.index("收盤價")]), 0.0 if pts is None else html_sign(r[f.index("漲跌(+/-)")]) * pts)
    return yahoo(f"{symbol}.TW", d)


# ---------- 櫃買中心 ----------

def tpex_stock(symbol, d):
    try:
        j = http("GET", f"https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date={d:%Y/%m/%d}&type=EW&response=json").json()
        if j.get("date") == f"{d:%Y%m%d}" or j.get("stat") == "ok":
            for t in j.get("tables", []):
                f = [x.strip() for x in (t.get("fields") or [])]
                if "代號" not in f:
                    continue
                for r in t.get("data", []):
                    if r and r[0].strip() == symbol:
                        return quote(num(r[f.index("收盤")]), num(r[f.index("漲跌")]))
    except Exception as e:
        print(f"    櫃買網站版失敗：{e}")
    if d == datetime.now(TW).date():
        for r in http("GET", "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes").json():
            if r.get("SecuritiesCompanyCode") == symbol and r.get("Date") == roc(d):
                return quote(num(r.get("Close")), num(r.get("Change")))
    return yahoo(f"{symbol}.TWO", d)


# ---------- 期交所（CSV 下載，當天即有資料） ----------

def taifex_fut(symbol, d):
    r = http("POST", "https://www.taifex.com.tw/cht/3/futDataDown", data={
        "down_type": "1", "commodity_id": symbol, "commodity_id2": "",
        "queryStartDate": f"{d:%Y/%m/%d}", "queryEndDate": f"{d:%Y/%m/%d}"})
    rows = list(csv.reader(io.StringIO(r.content.decode("cp950", errors="replace"))))
    if not rows or "交易日期" not in rows[0][0]:
        return None
    h = [x.strip() for x in rows[0]]
    ix = {k: h.index(k) for k in ("交易日期", "契約", "到期月份(週別)", "收盤價", "漲跌價", "交易時段")}
    cand = [
        x for x in rows[1:]
        if len(x) > ix["交易時段"] and x[ix["交易日期"]].strip() == f"{d:%Y/%m/%d}"
        and x[ix["契約"]].strip() == symbol and x[ix["交易時段"]].strip() == "一般"
        and "/" not in x[ix["到期月份(週別)"]] and num(x[ix["收盤價"]]) is not None
    ]
    if not cand:
        return None
    near = min(cand, key=lambda x: x[ix["到期月份(週別)"]].strip())
    return quote(num(near[ix["收盤價"]]), num(near[ix["漲跌價"]]))


# ---------- Yahoo Finance（任意代號，也是台股備援） ----------

def yahoo_chart(symbol, interval, rng):
    j = http("GET", f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(symbol)}?range={rng}&interval={interval}").json()
    return j["chart"]["result"][0]


def yahoo_daily(symbol, rng="3mo", adjusted=False):
    """回傳 ([(交易所當地日期, 收盤價)], 是否為台北時區交易所, meta)。"""
    res = yahoo_chart(symbol, "1d", rng)
    meta = res["meta"]
    off = meta.get("gmtoffset", 0)
    closes = res["indicators"]["quote"][0]["close"]
    if adjusted and res["indicators"].get("adjclose"):
        closes = res["indicators"]["adjclose"][0]["adjclose"]
    bars = {}
    for t, c in zip(res.get("timestamp", []), closes):
        if c is not None:
            bars[datetime.fromtimestamp(t + off, timezone.utc).date()] = c
    return sorted(bars.items()), meta.get("exchangeTimezoneName") == "Asia/Taipei", meta


def yahoo_snapshots(symbol, asof="14:00", rng="1mo"):
    """24 小時商品（匯率）在台北時間 asof 當下的價格：取收盤時間 ≤ asof 的最後一根小時線。"""
    hh, mm = map(int, asof.split(":"))
    res = yahoo_chart(symbol, "60m", rng)
    closes = res["indicators"]["quote"][0]["close"]
    snaps = {}
    for t, c in zip(res.get("timestamp", []), closes):
        if c is None:
            continue
        end = datetime.fromtimestamp(t, TW) + timedelta(hours=1)
        if (end.hour, end.minute) <= (hh, mm) and end.weekday() < 5:
            snaps[end.date()] = c  # 時間遞增，同一天最後一根會覆蓋前面
    return sorted(snaps.items())


def yahoo(symbol, d, asof=None):
    if asof:
        snaps = yahoo_snapshots(symbol, asof, "1mo" if (datetime.now(TW).date() - d).days < 25 else "6mo")
        cur = [c for sd, c in snaps if sd == d]
        prev = [c for sd, c in snaps if sd < d]
        return quote(cur[-1], cur[-1] - prev[-1]) if cur and prev else None
    bars, taipei, meta = yahoo_daily(symbol, "6mo")
    if taipei:
        cur = [c for bd, c in bars if bd == d]
        if not cur and d == datetime.now(TW).date() and meta.get("regularMarketPrice") is not None:
            cur = [meta["regularMarketPrice"]]  # 尚未形成日線時用即時價
        prev = [c for bd, c in bars if bd < d]
    else:
        # 海外市場：台股收盤時當天尚未開盤或收盤，只能用前一個已收盤交易日。
        done = [c for bd, c in bars if bd < d]
        cur, prev = done[-1:], done[:-1]
    return quote(cur[-1], cur[-1] - prev[-1]) if cur and prev else None


ADAPTERS = {
    "twse_stock": twse_stock,
    "tpex_stock": tpex_stock,
    "twse_index": twse_index,
    "taifex_fut": taifex_fut,
    "yahoo": yahoo,
}


def is_trading_day(d):
    return bool(twse_mi_index(d, "IND"))


def fetch_day(cfg, d):
    quotes, failed = {}, []
    for ins in cfg["instruments"]:
        src = ins["source"]
        fn = ADAPTERS.get(src["type"])
        extra = {k: v for k, v in src.items() if k not in ("type", "symbol")}
        try:
            q = fn(src["symbol"], d, **extra) if fn else None
        except Exception as e:  # 單一商品失敗不影響其他商品
            print(f"    {ins['id']} 錯誤：{e}")
            q = None
        if q:
            quotes[ins["id"]] = q
            print(f"  {ins['id']:<8} {q['last']:>12} {q['pct']:+.2f}%")
        else:
            failed.append(ins["id"])
            print(f"  {ins['id']:<8} 無資料")
    return quotes, failed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD，預設今天")
    ap.add_argument("--backfill", type=int, default=0, help="回補最近 N 個日曆日")
    ap.add_argument("--force", action="store_true", help="相容舊參數，無作用")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    end = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else datetime.now(TW).date()
    days = [end - timedelta(days=i) for i in range(a.backfill, -1, -1)] if a.backfill else [end]

    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    by_date = {h["date"]: h for h in history}
    wrote = 0
    for d in days:
        if d.weekday() >= 5:
            continue
        if not is_trading_day(d):
            print(f"{d} 證交所無收盤資料（休市或尚未公布），略過。")
            continue
        print(f"== {d}")
        quotes, failed = fetch_day(cfg, d)
        if len(quotes) < len(cfg["instruments"]) / 2:
            print(f"{d} 成功不到一半，不寫入。")
            continue
        by_date[str(d)] = {
            "date": str(d),
            "source": "證交所／櫃買／期交所官方資料，美元與匯率為 Yahoo Finance（GitHub Actions 自動抓取）",
            "fetchedAt": datetime.now(TW).isoformat(timespec="seconds"),
            "missing": failed,
            "quotes": quotes,
        }
        wrote += 1

    if not wrote:
        print("沒有寫入任何資料。")
        return 0
    out = sorted(by_date.values(), key=lambda h: h["date"])[-MAX_DAYS:]
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {wrote} 天，歷史共 {len(out)} 天。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
