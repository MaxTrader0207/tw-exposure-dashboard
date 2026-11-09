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

def taifex_fut(symbol, d, session="一般"):
    """近月收盤。session="盤後" 取夜盤：期交所把夜盤標在下一個交易日（d 的夜盤＝前一晚 15:00 到 d 清晨 5:00）。"""
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
        and x[ix["契約"]].strip() == symbol and x[ix["交易時段"]].strip() == session
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


def yahoo_daily(symbol, rng="6mo"):
    """回傳 ([(交易所當地日期, 收盤價)], 是否為台北時區交易所, meta)。"""
    res = yahoo_chart(symbol, "1d", rng)
    meta = res["meta"]
    off = meta.get("gmtoffset", 0)
    bars = {}
    for t, c in zip(res.get("timestamp", []), res["indicators"]["quote"][0]["close"]):
        if c is not None:
            bars[datetime.fromtimestamp(t + off, timezone.utc).date()] = c
    return sorted(bars.items()), meta.get("exchangeTimezoneName") == "Asia/Taipei", meta


def yahoo_ohlc(symbol, rng="5y"):
    """{交易所當地日期: {"open", "close", "adj"}}，回測用。"""
    res = yahoo_chart(symbol, "1d", rng)
    off = res["meta"].get("gmtoffset", 0)
    qd = res["indicators"]["quote"][0]
    adj = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") or qd["close"]
    out = {}
    for t, o, c, a in zip(res.get("timestamp", []), qd["open"], qd["close"], adj):
        if c is not None:
            out[datetime.fromtimestamp(t + off, timezone.utc).date()] = {"open": o, "close": c, "adj": a}
    return out


def yahoo_snapshots(symbol, asof="14:00", rng="1mo"):
    """24 小時商品（匯率）在台北時間 asof 當下的價格：取收盤時間 ≤ asof 的最後一根小時線。"""
    hh, mm = map(int, asof.split(":"))
    res = yahoo_chart(symbol, "60m", rng)
    snaps = {}
    for t, c in zip(res.get("timestamp", []), res["indicators"]["quote"][0]["close"]):
        if c is None:
            continue
        end = datetime.fromtimestamp(t, TW) + timedelta(hours=1)
        if (end.hour, end.minute) <= (hh, mm) and end.weekday() < 5:
            snaps[end.date()] = c
    return sorted(snaps.items())


def yahoo(symbol, d, asof=None, sameDay=False):
    """sameDay=True：收盤早於台股資料抓取時間的亞洲市場（日經、韓股），用 d 當天的收盤。"""
    if asof:
        snaps = yahoo_snapshots(symbol, asof, "1mo" if (datetime.now(TW).date() - d).days < 25 else "6mo")
        cur = [c for sd, c in snaps if sd == d]
        prev = [c for sd, c in snaps if sd < d]
        return quote(cur[-1], cur[-1] - prev[-1]) if cur and prev else None
    bars, taipei, meta = yahoo_daily(symbol)
    if sameDay:
        cur = [c for bd, c in bars if bd == d]
        prev = [c for bd, c in bars if bd < d]
    elif taipei:
        cur = [c for bd, c in bars if bd == d]
        if not cur and d == datetime.now(TW).date() and meta.get("regularMarketPrice") is not None:
            cur = [meta["regularMarketPrice"]]  # 尚未形成日線時用即時價
        prev = [c for bd, c in bars if bd < d]
    else:
        # 海外市場：台股收盤時當天尚未開盤或收盤，只能用前一個已收盤交易日。
        done = [c for bd, c in bars if bd < d]
        cur, prev = done[-1:], done[:-1]
    return quote(cur[-1], cur[-1] - prev[-1]) if cur and prev else None


# ---------- 大盤融資維持率 ----------

# ---------- 籌碼與市場內部 ----------

def twse_foreign_net(_symbol, d):
    """外資現貨買賣超（億元）＝ 外資及陸資(不含外資自營商) ＋ 外資自營商。約 15:00 後公布。"""
    j = http("GET", f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={d:%Y%m%d}&response=json").json()
    if j.get("stat") != "OK" or j.get("date") != f"{d:%Y%m%d}":
        return None
    f = j.get("fields") or []
    rows = [r for r in j.get("data", []) if r and r[0].startswith("外資")]
    if not rows:
        return None
    net = sum(num(r[f.index("買賣差額")]) or 0 for r in rows) / 1e8
    gross = sum((num(r[f.index("買進金額")]) or 0) + (num(r[f.index("賣出金額")]) or 0) for r in rows) / 1e8
    # ratio：買賣超佔外資成交金額（買進＋賣出）的比例，不受市場規模影響
    return {"last": round(net, 2), "chg": None, "pct": None, "gross": round(gross, 2), "ratio": round(net / gross * 100, 2) if gross else None}


def taifex_foreign_oi(symbol, d):
    """外資台指期多空未平倉口數淨額（口），漲跌＝與前一交易日的差。約 15:00 後公布。"""
    r = http("POST", "https://www.taifex.com.tw/cht/3/futContractsDateDown", data={
        "queryStartDate": f"{d - timedelta(days=14):%Y/%m/%d}", "queryEndDate": f"{d:%Y/%m/%d}", "commodityId": symbol or "TXF"})
    rows = list(csv.reader(io.StringIO(r.content.decode("cp950", errors="replace"))))
    if not rows or "日期" not in rows[0][0]:
        return None
    h = [x.strip() for x in rows[0]]
    di, wi, ni = h.index("日期"), h.index("身份別"), h.index("多空未平倉口數淨額")
    series = sorted((x[di].strip(), num(x[ni])) for x in rows[1:] if len(x) > ni and x[wi].strip().startswith("外資"))
    series = [s for s in series if s[0] <= f"{d:%Y/%m/%d}" and s[1] is not None]
    if not series or series[-1][0] != f"{d:%Y/%m/%d}":
        return None
    last = series[-1][1]
    chg = last - series[-2][1] if len(series) > 1 else None
    return {"last": last, "chg": chg, "pct": None}


def twse_breadth(_symbol, d):
    """上市股票漲跌家數：(上漲 − 下跌) ÷ (上漲 + 下跌) × 100，只計股票、不含 ETF 等。"""
    for t in twse_mi_index(d, "ALLBUT0999"):
        if (t.get("title") or "").startswith("漲跌證券數"):
            f = t.get("fields") or []
            col = f.index("股票") if "股票" in f else 2
            cnt = {}
            for r in t.get("data", []):
                m = re.match(r"\s*([\d,]+)", str(r[col]))
                if m:
                    cnt[r[0][:2]] = int(m.group(1).replace(",", ""))
            up, down = cnt.get("上漲"), cnt.get("下跌")
            if up is None or down is None or up + down == 0:
                return None
            return {"last": round((up - down) / (up + down) * 100, 1), "chg": None, "pct": None, "up": up, "down": down}
    return None


def twse_margin_ratio(_symbol, d):
    """上市融資維持率，固定使用 d 之前最近一個交易日（證交所融資資料晚上才公布，下午抓取時當天還沒有）。"""
    for back in range(1, 10):
        day = d - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        q = margin_ratio_on(day)
        if q:
            q["asOf"] = str(day)
            return q
    return None


def margin_ratio_on(d):
    """d 當天的上市融資維持率 ＝ Σ(各股融資餘額張數 × 1000 × 收盤價，不含 ETF) ÷ 融資金額，與財經M平方的定義相同。
    inclEtf 為分子含 ETF 的舊算法，留作對照。漲跌由主程式依前一筆補上。"""
    j = http("GET", f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d:%Y%m%d}&selectType=ALL&response=json").json()
    if j.get("stat") != "OK" or j.get("date") != f"{d:%Y%m%d}":
        return None
    amount = lots = None
    value = etf_value = 0.0
    closes = {}
    for t in twse_mi_index(d, "ALLBUT0999"):
        f = t.get("fields") or []
        if "證券代號" in f and "收盤價" in f:
            for r in t.get("data", []):
                closes[r[0].strip()] = num(r[f.index("收盤價")])
    for t in j.get("tables", []):
        f = [x.strip() for x in (t.get("fields") or [])]
        rows = t.get("data", [])
        if "項目" in f and "今日餘額" in f:
            for r in rows:
                if r[0].startswith("融資金額"):
                    amount = num(r[f.index("今日餘額")]) * 1000
        code_col = next((i for i, x in enumerate(f) if x in ("代號", "股票代號", "證券代號")), None)
        if code_col is not None and "今日餘額" in f:
            bal_col = f.index("今日餘額")  # 第一個今日餘額是融資
            lots = 0
            for r in rows:
                b, c = num(r[bal_col]), closes.get(r[code_col].strip())
                code = r[code_col].strip()
                if b and c:
                    if code.startswith(("00", "02")):  # ETF、ETN
                        etf_value += b * 1000 * c
                    else:
                        value += b * 1000 * c
                    lots += b
    if not amount or not lots:
        return None
    return {"last": round(value / amount * 100, 2), "chg": None, "pct": None,
            "inclEtf": round((value + etf_value) / amount * 100, 2)}


def macromicro(chart_id, d):
    """財經M平方圖表最新值（日期 ≤ d）。網站有 Cloudflare 機器人驗證，連不上就回傳 None，該因子影響為 0。"""
    page = f"https://www.macromicro.me/charts/{chart_id}/"
    hdr = {**UA, "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
           "Referer": page, "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
    try:
        r = requests.get(f"https://www.macromicro.me/charts/data/{chart_id}", headers=hdr, timeout=30)
        if r.status_code != 200 or "json" not in (r.headers.get("content-type") or ""):
            print(f"    財經M平方無法連線（HTTP {r.status_code}），融資維持率影響設為 0")
            return None
        j = r.json()
    except Exception as e:
        print(f"    財經M平方無法連線（{e}），融資維持率影響設為 0")
        return None

    points = []

    def walk(x):  # 找出所有 [時間, 數值] 序列，不依賴固定 JSON 結構
        if isinstance(x, list) and x and all(isinstance(p, list) and len(p) == 2 for p in x):
            points.extend(x)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
    walk(j)
    rows = []
    for t, v in points:
        try:
            pd_ = datetime.fromtimestamp(t / 1000, TW).date() if isinstance(t, (int, float)) else datetime.strptime(str(t)[:10], "%Y-%m-%d").date()
            rows.append((pd_, float(v)))
        except (TypeError, ValueError):
            continue
    rows = sorted(r for r in rows if r[0] <= d)
    if len(rows) < 2:
        return None
    (_, prev), (day, last) = rows[-2], rows[-1]
    q = quote(last, last - prev)
    q["asOf"] = str(day)  # 財經M平方數值的資料日期，通常晚一天更新
    return q


def taifex_csv(url, data):
    rows = list(csv.reader(io.StringIO(http("POST", url, data=data).content.decode("cp950", errors="replace"))))
    if not rows or not rows[0] or "日期" not in rows[0][0]:
        return None, []
    return [x.strip() for x in rows[0]], rows[1:]


def mtx_oi(d):
    """小台一般盤全市場未平倉口數（各月份與週契約加總，不含價差單）。"""
    h, rows = taifex_csv("https://www.taifex.com.tw/cht/3/futDataDown", {
        "down_type": "1", "commodity_id": "MTX", "commodity_id2": "",
        "queryStartDate": f"{d:%Y/%m/%d}", "queryEndDate": f"{d:%Y/%m/%d}"})
    if not h:
        return None
    ix = {k: h.index(k) for k in ("交易日期", "到期月份(週別)", "未沖銷契約數", "交易時段")}
    oi = [num(x[ix["未沖銷契約數"]]) for x in rows if len(x) > ix["交易時段"] and x[ix["交易日期"]].strip() == f"{d:%Y/%m/%d}"
          and x[ix["交易時段"]].strip() == "一般" and "/" not in x[ix["到期月份(週別)"]]]
    oi = [v for v in oi if v is not None]
    return sum(oi) if oi else None


def taifex_retail_mtx(_symbol, d):
    """散戶小台多空比（%）＝ −(三大法人小台多空未平倉淨額) ÷ 小台全市場未平倉 × 100。
    正值代表散戶淨多單；散戶越偏多通常被視為反向指標。約 15:00 後公布。"""
    h, rows = taifex_csv("https://www.taifex.com.tw/cht/3/futContractsDateDown", {
        "queryStartDate": f"{d:%Y/%m/%d}", "queryEndDate": f"{d:%Y/%m/%d}", "commodityId": "MXF"})
    if not h:
        return None
    di, ni = h.index("日期"), h.index("多空未平倉口數淨額")
    nets = [num(x[ni]) for x in rows if len(x) > ni and x[di].strip() == f"{d:%Y/%m/%d}"]
    if len(nets) < 3 or None in nets:
        return None
    oi = mtx_oi(d)
    if not oi:
        return None
    inst = sum(nets)
    return {"last": round(-inst / oi * 100, 2), "chg": None, "pct": None, "oi": oi, "instNet": inst}


def pcr_rows(start, end):
    h, rows = taifex_csv("https://www.taifex.com.tw/cht/3/pcRatioDown", {"queryStartDate": f"{start:%Y/%m/%d}", "queryEndDate": f"{end:%Y/%m/%d}"})
    if not h:
        return {}
    di, oi = h.index("日期"), h.index("買賣權未平倉量比率%")
    return {x[di].strip().replace("/", "-"): num(x[oi]) for x in rows if len(x) > oi and num(x[oi]) is not None}


def with_dev(series, d, n=20):
    """series：{日期字串: 數值}。回傳 d 當天數值與它減去前 n 個交易日平均的差。"""
    keys = sorted(k for k in series if k <= str(d))
    if not keys or keys[-1] != str(d) or len(keys) < n + 1:
        return None, None
    base = [series[k] for k in keys[-n - 1:-1]]
    return series[str(d)], series[str(d)] - sum(base) / n


def taifex_pcr(_symbol, d):
    """台指選擇權未平倉 Put/Call Ratio（%）。dev ＝ 當天 − 前 20 個交易日平均：賣權未平倉相對增加，代表支撐變強。"""
    last, dev = with_dev(pcr_rows(d - timedelta(days=45), d), d)
    if last is None:
        return None
    return {"last": last, "chg": None, "pct": None, "dev": round(dev, 2)}


def turnover_rows(d):
    """證交所大盤成交金額（億元）與指數漲跌，抓 d 所在月份與前兩個月。"""
    out = {}
    m = Date(d.year, d.month, 1)
    for _ in range(3):
        j = http("GET", f"https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date={m:%Y%m%d}&response=json").json()
        if j.get("stat") == "OK":
            f = j["fields"]
            for r in j.get("data", []):
                y, mm, dd = map(int, r[0].split("/"))
                out[f"{y + 1911}-{mm:02d}-{dd:02d}"] = (num(r[f.index("成交金額")]) / 1e8, num(r[f.index("漲跌點數")]))
        m = (m - timedelta(days=1)).replace(day=1)
    return out


def twse_turnover(_symbol, d):
    """上市成交金額（億元）。ratio ＝ 相對前 20 日均量增減 %；flow ＝ ratio × 指數漲跌方向（量增價漲為正、量增價跌為負）。"""
    rows = turnover_rows(d)
    last, dev = with_dev({k: v[0] for k, v in rows.items()}, d)
    if last is None:
        return None
    ratio = dev / (last - dev) * 100
    move = rows[str(d)][1] or 0
    sign = 1 if move > 0 else -1 if move < 0 else 0
    return {"last": round(last, 1), "chg": None, "pct": None, "ratio": round(ratio, 1), "flow": round(ratio * sign, 1)}


def add_moving_averages(ins, d, q):
    """以 Yahoo 歷史收盤（d 之前）加上當天官方收盤計算均線，寫入 q["ma30"] 等欄位。"""
    bars, _, _ = yahoo_daily(ins["historySymbol"], "2y")
    closes = [c for bd, c in bars if bd < d] + [q["last"]]
    for n in ins["ma"]:
        if len(closes) >= n:
            q[f"ma{n}"] = round(sum(closes[-n:]) / n, 2)


ADAPTERS = {
    "twse_stock": twse_stock,
    "tpex_stock": tpex_stock,
    "twse_index": twse_index,
    "taifex_fut": taifex_fut,
    "twse_margin_ratio": twse_margin_ratio,
    "twse_foreign_net": twse_foreign_net,
    "taifex_foreign_oi": taifex_foreign_oi,
    "twse_breadth": twse_breadth,
    "taifex_retail_mtx": taifex_retail_mtx,
    "taifex_pcr": taifex_pcr,
    "twse_turnover": twse_turnover,
    "macromicro": macromicro,
    "yahoo": yahoo,
}


def twse_holidays(year):
    """證交所公布的休市日（不含「開始交易日」「最後交易日」等仍有交易的日子）。"""
    j = http("GET", f"https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date={year}0101&response=json").json()
    return {r[0]: r[1] for r in j.get("data", []) if "交易日" not in r[1]}


def typhoon_closed(d):
    """行政院人事行政總處停班停課公告：臺北市今天停止上班時，證券市場休市。查不到時視為正常開市。"""
    try:
        r = requests.get("https://www.dgpa.gov.tw/typh/daily/nds.html", headers=UA, timeout=20)
        r.encoding = "utf-8"
        text = re.sub(r"<[^>]+>", " ", r.text)
        m = re.search(r"臺北市\s+([^臺]{0,80})", text)
        return bool(m and "今天停止上班" in m.group(1).replace(" ", ""))
    except Exception as e:
        print(f"    停班停課公告查詢失敗：{e}")
        return False


def market_closed(d):
    """回傳休市原因；正常交易日回傳 None。"""
    if d.weekday() >= 5:
        return "週末"
    try:
        h = twse_holidays(d.year)
        if str(d) in h:
            return h[str(d)]
    except Exception as e:
        print(f"    休市日曆查詢失敗：{e}")
    if d == datetime.now(TW).date() and typhoon_closed(d):
        return "颱風停止上班"
    return None


def is_trading_day(d):
    return bool(twse_mi_index(d, "IND"))


def fetch_day(cfg, d):
    quotes, failed = {}, []
    for ins in cfg["instruments"]:
        src = ins["source"]
        fn = ADAPTERS.get(src["type"])
        extra = {k: v for k, v in src.items() if k not in ("type", "symbol")}
        try:
            q = fn(src.get("symbol"), d, **extra) if fn else None
        except Exception as e:  # 單一商品失敗不影響其他商品
            print(f"    {ins['id']} 錯誤：{e}")
            q = None
        if q and ins.get("ma"):
            try:
                add_moving_averages(ins, d, q)
            except Exception as e:
                print(f"    {ins['id']} 均線計算失敗：{e}")
        if q:
            quotes[ins["id"]] = q
            print(f"  {ins['id']:<8} {q['last']:>12} {'' if q['pct'] is None else format(q['pct'], '+.2f') + '%'}")
        else:
            failed.append(ins["id"])
            print(f"  {ins['id']:<8} 無資料")
    return quotes, failed


PREMARKET = ROOT / "data" / "premarket.json"


def premarket(cfg, d):
    """盤前參考：沿用最近一個交易日的台股收盤，換上昨晚已知的資料，寫到 data/premarket.json（不進歷史）。

    更新項目：海外市場（前一個美股交易日收盤）、24 小時匯率（目前價格對比前一交易日 14:00）、
    台指期換成夜盤收盤、融資維持率換成前一交易日（前晚才公布）。
    """
    history = json.loads(HISTORY.read_text(encoding="utf-8")) if HISTORY.exists() else []
    base = [h for h in history if h["date"] < str(d)]
    if not base:
        print("沒有可沿用的收盤資料。")
        return 1
    base = base[-1]
    quotes = json.loads(json.dumps(base["quotes"]))
    updated, failed = [], []
    for ins in cfg["instruments"]:
        src, k = ins["source"], ins["id"]
        q = None
        try:
            if src["type"] == "yahoo" and src.get("asof"):
                _, _, meta = yahoo_daily(src["symbol"], "5d")
                last, prev = meta.get("regularMarketPrice"), base["quotes"].get(k, {}).get("last")
                q = quote(last, last - prev) if last is not None and prev else None
            elif src["type"] == "yahoo" and src.get("sameDay"):
                continue  # 日韓股開盤前沒有新數值
            elif src["type"] == "yahoo":
                q = yahoo(src["symbol"], d)
            elif src["type"] == "taifex_fut":
                q = taifex_fut(src["symbol"], d, session="盤後")
                if q:
                    q["session"] = "夜盤"
            elif src["type"] == "twse_margin_ratio":
                q = twse_margin_ratio(src.get("symbol"), d)
                prev = base["quotes"].get(k, {}).get("last")
                if q and prev:
                    q["chg"] = round(q["last"] - prev, 4)
                    q["pct"] = round(q["chg"] / prev * 100, 2)
            else:
                continue  # 台股盤中資料，開盤前沒有新數值
        except Exception as e:
            print(f"    {k} 錯誤：{e}")
        if q:
            for key in ("ma30", "ma60", "ma100"):  # 均線等附加欄位沿用
                if key in quotes.get(k, {}):
                    q.setdefault(key, quotes[k][key])
            quotes[k] = q
            updated.append(k)
            print(f"  {k:<8} {q['last']:>12} {'' if q.get('pct') is None else format(q['pct'], '+.2f') + '%'}")
        else:
            failed.append(k)
    out = {"date": str(d), "basedOn": base["date"], "generatedAt": datetime.now(TW).isoformat(timespec="seconds"),
           "updated": updated, "missing": failed, "quotes": quotes}
    PREMARKET.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"盤前參考 {d}：沿用 {base['date']} 收盤，更新 {updated}" + (f"，失敗 {failed}" if failed else ""))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--premarket", action="store_true", help="產生盤前參考水位（早上執行）")
    ap.add_argument("--date", help="YYYY-MM-DD，預設今天")
    ap.add_argument("--backfill", type=int, default=0, help="回補最近 N 個日曆日")
    ap.add_argument("--force", action="store_true", help="相容舊參數，無作用")
    a = ap.parse_args()

    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    if a.premarket:
        d = datetime.strptime(a.date, "%Y-%m-%d").date() if a.date else datetime.now(TW).date()
        reason = market_closed(d)
        if reason:
            print(f"{d} 休市（{reason}），不產生盤前參考。")
            return 0
        return premarket(cfg, d)
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
        # 沒有官方漲跌的數值（例如融資維持率）用前一個交易日補上漲跌
        prev = [h for k, h in sorted(by_date.items()) if k < str(d)]
        for k, q in quotes.items():
            if q.get("chg") is None:
                p = next((h["quotes"][k]["last"] for h in reversed(prev) if k in h["quotes"]), None)
                if p:
                    q["chg"] = round(q["last"] - p, 4)
                    q["pct"] = round(q["chg"] / p * 100, 2)
        if cfg["benchmark"] not in quotes:
            print(f"{d} 沒有{cfg['benchmark']}收盤資料（休市或尚未公布），不寫入。")
            continue
        if len(quotes) < len(cfg["instruments"]) / 2:
            print(f"{d} 成功不到一半，不寫入。")
            continue
        by_date[str(d)] = {
            "date": str(d),
            "source": "證交所／期交所官方資料，匯率與美股為 Yahoo Finance（GitHub Actions 自動抓取）",
            "fetchedAt": datetime.now(TW).isoformat(timespec="seconds"),
            "missing": failed,
            "quotes": quotes,
        }
        wrote += 1

    if not wrote:
        print("沒有寫入任何資料。")
        return 0
    out = [h for h in sorted(by_date.values(), key=lambda h: h["date"]) if cfg["benchmark"] in h["quotes"]][-MAX_DAYS:]
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"寫入 {wrote} 天，歷史共 {len(out)} 天。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
