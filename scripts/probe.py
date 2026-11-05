"""診斷用：檢查籌碼資料來源的格式與歷史可回溯長度。"""
import csv
import io
import json
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}


def show(name, r):
    print(f"\n===== {name}  status {r.status_code}  {r.headers.get('content-type')}  len {len(r.content)}")


for d in ["20261104", "20211105"]:
    r = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={d}&response=json", headers=UA, timeout=60)
    show(f"BFI82U {d}", r)
    j = r.json()
    print({k: j.get(k) for k in ("stat", "date", "title")}, j.get("fields"))
    for row in j.get("data", []):
        print("  ", row)
    time.sleep(3)

    r = requests.get(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={d}&selectType=MS&response=json", headers=UA, timeout=60)
    show(f"MI_MARGN {d}", r)
    j = r.json()
    print({k: j.get(k) for k in ("stat", "date")}, "keys", list(j.keys()))
    for t in j.get("tables", [])[:2]:
        print("  title", t.get("title"), "fields", t.get("fields"))
        for row in t.get("data", [])[:5]:
            print("    ", row)
    time.sleep(3)

for start, end in [("2026/10/01", "2026/11/04"), ("2025/11/05", "2026/11/04"), ("2021/11/01", "2021/11/30")]:
    r = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", headers=UA, timeout=60,
                      data={"queryStartDate": start, "queryEndDate": end, "commodityId": "TXF"})
    show(f"futContractsDateDown {start}~{end}", r)
    txt = r.content.decode("cp950", errors="replace")
    lines = txt.splitlines()
    print("  lines", len(lines))
    for line in lines[:4] + lines[-2:]:
        print("   ", line[:300])
    time.sleep(3)
