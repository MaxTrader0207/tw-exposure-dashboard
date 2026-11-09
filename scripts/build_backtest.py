"""建立回測用的每日報價（data/backtest_quotes.json），格式與 data/history.json 相同，
每一天的數值都模擬「當天 16:00 實際能拿到的資料」，所以可以直接丟給 assets/engine.js 計算。

資料來源與時點（與 scripts/fetch.py 每日抓取一致）
- 美元指數、台幣：Yahoo 小時線在台北 14:00 取樣（Yahoo 小時線最多約兩年，這也決定回測長度）
- 台指期：期交所每日行情，近月一般盤
- 加權指數：Yahoo ^TWII，均線用當天以前（含）的收盤
- 台積電：Yahoo 還原收盤（漲跌幅不受除息影響）
- VIX、費半、美債：前一個已收盤的美股交易日
- 融資維持率：前一個交易日（證交所晚上才公布）
- 外資買賣超、漲跌家數：證交所當日資料
- 外資台指期未平倉：期交所三大法人

證交所逐日資料存在 data/cache/twse_daily.json，重跑時只抓缺的日子。
用法：python scripts/build_backtest.py [--days 730] [--max-requests 2000]
"""
import argparse
import csv
import io
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402

ROOT = fetch.ROOT
CACHE = ROOT / "data" / "cache" / "twse_daily.json"
OUT = ROOT / "data" / "backtest_quotes.json"


def save_cache(cache):
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")


def twse_day(d, out):
    """補齊一天的證交所資料：外資買賣超、漲跌家數、融資維持率（皆為 d 當天）。
    margin2 為分子不含 ETF 的融資維持率（與財經M平方同定義），margin 為舊算法。"""
    if "fini" not in out:
        try:
            out["fini"] = fetch.twse_foreign_net(None, d)
        except Exception as e:
            print(f"    {d} 外資：{e}")
    if "adl" not in out:
        try:
            out["adl"] = fetch.twse_breadth(None, d)
        except Exception as e:
            print(f"    {d} 漲跌家數：{e}")
    if "margin2" not in out:
        try:
            m = fetch.margin_ratio_on(d)
            out["margin2"] = m["last"] if m else None
            out["margin"] = m["inclEtf"] if m else None
        except Exception as e:
            print(f"    {d} 融資維持率：{e}")
    fetch._cache.clear()  # 個股行情很大，用完即丟
    return out


def taifex_months(start, end, url, data_fn):
    rows, header = [], None
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        stop = min(nxt - timedelta(days=1), end)
        r = fetch.http("POST", url, data=data_fn(d, stop))
        lines = list(csv.reader(io.StringIO(r.content.decode("cp950", errors="replace"))))
        if lines and ("日期" in lines[0][0]):
            header = [x.strip() for x in lines[0]]
            rows += lines[1:]
        else:
            print(f"  期交所 {d:%Y-%m} 無資料")
        d = nxt
    fetch._cache.clear()
    return header, rows


def tx_series(start, end):
    h, rows = taifex_months(start, end, "https://www.taifex.com.tw/cht/3/futDataDown",
                            lambda a, b: {"down_type": "1", "commodity_id": "TX", "commodity_id2": "",
                                          "queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}"})
    ix = {k: h.index(k) for k in ("交易日期", "契約", "到期月份(週別)", "收盤價", "漲跌價", "交易時段")}
    best = {}
    for x in rows:
        if len(x) <= ix["交易時段"] or x[ix["契約"]].strip() != "TX" or x[ix["交易時段"]].strip() != "一般":
            continue
        m = x[ix["到期月份(週別)"]].strip()
        c, ch = fetch.num(x[ix["收盤價"]]), fetch.num(x[ix["漲跌價"]])
        if "/" in m or c is None or ch is None:
            continue
        dd = x[ix["交易日期"]].strip().replace("/", "-")
        if dd not in best or m < best[dd][0]:
            best[dd] = (m, fetch.quote(c, ch))
    return {k: v[1] for k, v in best.items()}


def foi_series(start, end):
    h, rows = taifex_months(start - timedelta(days=10), end, "https://www.taifex.com.tw/cht/3/futContractsDateDown",
                            lambda a, b: {"queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}", "commodityId": "TXF"})
    di, wi, ni = h.index("日期"), h.index("身份別"), h.index("多空未平倉口數淨額")
    s = sorted((x[di].strip().replace("/", "-"), fetch.num(x[ni])) for x in rows if len(x) > ni and x[wi].strip().startswith("外資"))
    out, prev = {}, None
    for dd, v in s:
        if v is None:
            continue
        out[dd] = {"last": v, "chg": None if prev is None else v - prev, "pct": None}
        prev = v
    return out


def mtx_retail(start, end):
    """散戶小台多空比：−三大法人小台淨未平倉 ÷ 小台全市場一般盤未平倉 × 100。"""
    h, rows = taifex_months(start, end, "https://www.taifex.com.tw/cht/3/futContractsDateDown",
                            lambda a, b: {"queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}", "commodityId": "MXF"})
    di, ni = h.index("日期"), h.index("多空未平倉口數淨額")
    inst = {}
    for x in rows:
        if len(x) > ni and fetch.num(x[ni]) is not None:
            k = x[di].strip().replace("/", "-")
            inst.setdefault(k, []).append(fetch.num(x[ni]))
    h, rows = taifex_months(start, end, "https://www.taifex.com.tw/cht/3/futDataDown",
                            lambda a, b: {"down_type": "1", "commodity_id": "MTX", "commodity_id2": "",
                                          "queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}"})
    ix = {k: h.index(k) for k in ("交易日期", "契約", "到期月份(週別)", "未沖銷契約數", "交易時段")}
    oi = {}
    for x in rows:
        if len(x) > ix["交易時段"] and x[ix["契約"]].strip() == "MTX" and x[ix["交易時段"]].strip() == "一般" and "/" not in x[ix["到期月份(週別)"]]:
            v = fetch.num(x[ix["未沖銷契約數"]])
            if v is not None:
                k = x[ix["交易日期"]].strip().replace("/", "-")
                oi[k] = oi.get(k, 0) + v
    return {k: {"last": round(-sum(v) / oi[k] * 100, 2), "chg": None, "pct": None, "oi": oi[k], "instNet": sum(v)}
            for k, v in inst.items() if len(v) >= 3 and oi.get(k)}


def pcr_series(start, end):
    h, rows = taifex_months(start, end, "https://www.taifex.com.tw/cht/3/pcRatioDown",
                            lambda a, b: {"queryStartDate": f"{a:%Y/%m/%d}", "queryEndDate": f"{b:%Y/%m/%d}"})
    di, oi = h.index("日期"), h.index("買賣權未平倉量比率%")
    return {x[di].strip().replace("/", "-"): fetch.num(x[oi]) for x in rows if len(x) > oi and fetch.num(x[oi]) is not None}


def turnover_series(start, end):
    out = {}
    m = date(start.year, start.month, 1)
    while m <= end:
        out.update(fetch.turnover_rows(date(m.year + (m.month == 12), m.month % 12 + 1, 1) - timedelta(days=1)))
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=725)
    ap.add_argument("--max-requests", type=int, default=2400, help="本次最多抓幾天證交所資料（每天 3 個請求）")
    a = ap.parse_args()
    cfg = json.loads(fetch.CONFIG.read_text(encoding="utf-8"))
    today = datetime.now(fetch.TW).date()
    start = today - timedelta(days=a.days)

    print("== Yahoo")
    twii_ohlc = fetch.yahoo_ohlc("^TWII", "5y")
    twii = {d: v["close"] for d, v in twii_ohlc.items()}
    cal = sorted(d for d in twii if start <= d < today)  # 只用已收盤的完整交易日
    closes_all = sorted(twii.items())
    tsmc = fetch.yahoo_ohlc("2330.TW", "5y")
    tsmc_raw = {d: v["close"] for d, v in tsmc.items()}
    tsmc_adj = {d: v["adj"] for d, v in tsmc.items()}
    us = {k: fetch.yahoo_daily(sym, "5y")[0] for k, sym in (("VIX", "^VIX"), ("SOX", "^SOX"), ("TNX", "^TNX"))}
    asia = {k: dict(fetch.yahoo_daily(sym, "5y")[0]) for k, sym in (("N225", "^N225"), ("KS11", "^KS11"))}
    fx = {k: fetch.yahoo_snapshots(sym, "14:00", "2y") for k, sym in (("DXY", "DX-Y.NYB"), ("USDTWD", "TWD=X"))}
    fetch._cache.clear()
    print(f"  交易日 {len(cal)}：{cal[0]} ~ {cal[-1]}")

    print("== 期交所")
    tx = tx_series(cal[0], cal[-1])
    foi = foi_series(cal[0], cal[-1])
    retail = mtx_retail(cal[0], cal[-1])
    pcr = pcr_series(cal[0] - timedelta(days=45), cal[-1])
    print(f"  台指期 {len(tx)} 天、外資未平倉 {len(foi)} 天、散戶小台 {len(retail)} 天、P/C {len(pcr)} 天")
    print("== 證交所成交金額（月資料）")
    turn = turnover_series(cal[0] - timedelta(days=45), cal[-1])
    fetch._cache.clear()

    print("== 證交所逐日（有快取的略過）")
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    complete = lambda v: all(k in v for k in ("fini", "adl", "margin2"))  # noqa: E731
    need = [d for d in [cal[0] - timedelta(days=7)] + cal if not complete(cache.get(str(d), {}))]
    # 融資維持率用前一交易日，所以第一天之前的那個交易日也要
    need = [d for d in need if d.weekday() < 5][: a.max_requests // 3]
    for k, d in enumerate(need, 1):
        cache[str(d)] = twse_day(d, cache.get(str(d), {}))
        if k % 20 == 0:
            save_cache(cache)
            print(f"  {k}/{len(need)} {d}")
    save_cache(cache)
    missing = [d for d in cal if not complete(cache.get(str(d), {}))]
    if missing:
        print(f"尚有 {len(missing)} 天證交所資料未抓，請再執行一次。")

    print("== 組合每日報價")
    def prev_bar(bars, d):
        done = [(bd, c) for bd, c in bars if bd < d]
        return fetch.quote(done[-1][1], done[-1][1] - done[-2][1]) if len(done) > 1 else None

    def snap(snaps, d):
        cur = [c for sd, c in snaps if sd == d]
        prev = [c for sd, c in snaps if sd < d]
        return fetch.quote(cur[-1], cur[-1] - prev[-1]) if cur and prev else None

    out, last_margin = [], None
    calib = next(i["source"].get("calib", 1.0) for i in cfg["instruments"] if i["source"]["type"] == "twse_margin_ratio")
    margin_days = sorted(k for k, v in cache.items() if v.get("margin2"))
    for d in cal:
        q = {}
        for k in ("DXY", "USDTWD"):
            v = snap(fx[k], d)
            if v:
                q[k] = v
        if str(d) in tx:
            q["TXF"] = tx[str(d)]
        past = [c for bd, c in closes_all if bd <= d]
        t = fetch.quote(past[-1], past[-1] - past[-2])
        for n in (30, 60, 100):
            if len(past) >= n:
                t[f"ma{n}"] = round(sum(past[-n:]) / n, 2)
        q["TAIEX"] = t
        adj = sorted((bd, c) for bd, c in tsmc_adj.items() if bd <= d)
        if d in tsmc_raw and len(adj) > 1 and adj[-1][0] == d:
            q["2330"] = {"last": tsmc_raw[d], "chg": None, "pct": round((adj[-1][1] / adj[-2][1] - 1) * 100, 2)}
        for k in ("VIX", "SOX", "TNX"):
            v = prev_bar(us[k], d)
            if v:
                q[k] = v
        c = cache.get(str(d), {})
        if c.get("fini"):
            q["FINI"] = c["fini"]
        if c.get("adl"):
            q["ADL"] = c["adl"]
        if str(d) in foi:
            q["FOI"] = foi[str(d)]
        pm = [k for k in margin_days if k < str(d)]
        if pm:
            v = round(cache[pm[-1]]["margin2"] * calib, 2)
            q["MARGIN"] = {"last": v, "chg": None if last_margin is None else round(v - last_margin, 2), "pct": None, "asOf": pm[-1]}
            last_margin = v
        for k in ("N225", "KS11"):
            bars = asia[k]
            prev = [bd for bd in bars if bd < d]
            if d in bars and prev:
                q[k] = fetch.quote(bars[d], bars[d] - bars[prev[-1]])
        if str(d) in retail:
            q["MTXR"] = retail[str(d)]
        last, dev = fetch.with_dev(pcr, d)
        if last is not None:
            q["PCR"] = {"last": last, "chg": None, "pct": None, "dev": round(dev, 2)}
        last, dev = fetch.with_dev({k: v[0] for k, v in turn.items()}, d)
        if last is not None:
            ratio = dev / (last - dev) * 100
            mv = turn[str(d)][1] or 0
            q["VOL"] = {"last": round(last, 1), "chg": None, "pct": None, "ratio": round(ratio, 1),
                        "flow": round(ratio * (1 if mv > 0 else -1 if mv < 0 else 0), 1)}
        open_ = twii_ohlc[d]["open"]
        out.append({"date": str(d), "open": open_, "quotes": q})
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    have = {k: sum(1 for x in out if k in x["quotes"]) for k in [i["id"] for i in cfg["instruments"]]}
    print(f"寫入 {len(out)} 天；各商品有資料的天數：{have}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
