"""診斷新資料來源的格式（只印出樣本，不寫檔）。"""
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402


def show(title, fn):
    print(f"\n===== {title}")
    try:
        fn()
    except Exception as e:
        print("錯誤", repr(e))


def csv_rows(r):
    return list(csv.reader(io.StringIO(r.content.decode("cp950", errors="replace"))))


def holidays():
    for u in ("https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date=20260101&response=json",
              "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"):
        r = fetch.http("GET", u)
        print(u, r.status_code, r.text[:1500])


def pcr():
    r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": "2026/10/27", "queryEndDate": "2026/11/06"})
    for x in csv_rows(r)[:6]:
        print(x)
    r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/pcRatioDown", data={"queryStartDate": "2024/11/01", "queryEndDate": "2024/11/30"})
    print("2024/11 筆數", len(csv_rows(r)))


def mtx():
    r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/futContractsDateDown", data={"queryStartDate": "2026/11/05", "queryEndDate": "2026/11/06", "commodityId": "MXF"})
    for x in csv_rows(r)[:8]:
        print(x)
    r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/futDataDown", data={"down_type": "1", "commodity_id": "MTX", "commodity_id2": "", "queryStartDate": "2026/11/06", "queryEndDate": "2026/11/06"})
    for x in csv_rows(r):
        print(x)


def vol():
    j = fetch.http("GET", "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK?date=20261101&response=json").json()
    print(j.get("stat"), j.get("fields"), j.get("data", [])[-3:])


def asia():
    for s in ("^N225", "^KS11"):
        bars, taipei, meta = fetch.yahoo_daily(s, "1mo")
        print(s, meta.get("exchangeTimezoneName"), bars[-4:], meta.get("regularMarketPrice"), meta.get("regularMarketTime"))


def retail_mtx():
    # 期交所也有直接公布的散戶多空比？試試 MTX 大額交易人
    r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/largeTraderFutDown", data={"queryStartDate": "2026/11/06", "queryEndDate": "2026/11/06"})
    for x in csv_rows(r)[:5]:
        print(x)


show("休市日", holidays)
show("P/C Ratio", pcr)
show("小台", mtx)
show("成交量", vol)
show("日韓股", asia)
show("大額交易人", retail_mtx)
