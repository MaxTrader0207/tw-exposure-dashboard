"""診斷資料來源（只印出結果，不寫檔）。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402
import requests


def show(title, fn):
    print(f"\n===== {title}")
    try:
        print(fn())
    except Exception as e:
        print("錯誤", repr(e))


def cbc():
    out = []
    for u in ("https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01",
              "https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01en"):
        r = requests.get(u, headers=fetch.UA, timeout=40)
        out.append(f"{u} {r.status_code} {len(r.text)} {r.text[:600]} ... {r.text[-300:]}")
    return "\n".join(out)


show("央行匯率", cbc)
for sym in ("^TWII", "TWD=X", "DX-Y.NYB", "^N225"):
    show(f"Yahoo 10y {sym}", lambda sym=sym: (lambda b: (len(b[0]), b[0][0], b[0][-1], b[2].get("exchangeTimezoneName")))(fetch.yahoo_daily(sym, "10y")))
show("TWII ohlc 10y", lambda: (lambda o: (len(o), sorted(o.items())[0]))(fetch.yahoo_ohlc("^TWII", "10y")))
for d in (date(2020, 11, 16), date(2022, 3, 7)):
    show(f"外資 {d}", lambda d=d: fetch.twse_foreign_net(None, d))
    show(f"漲跌家數 {d}", lambda d=d: fetch.twse_breadth(None, d))
    show(f"融資 {d}", lambda d=d: fetch.margin_ratio_on(d))
    show(f"散戶小台 {d}", lambda d=d: fetch.taifex_retail_mtx(None, d))
    show(f"P/C {d}", lambda d=d: fetch.taifex_pcr(None, d))
    show(f"成交 {d}", lambda d=d: fetch.twse_turnover(None, d))
    show(f"台指期 {d}", lambda d=d: fetch.taifex_fut("TX", d))
    show(f"外資期貨 {d}", lambda d=d: fetch.taifex_foreign_oi("TXF", d))
