"""診斷用：外資買賣超、外資台指期未平倉、漲跌家數的資料格式。"""
import time

import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}
D = "20261106"

j = requests.get(f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?type=day&dayDate={D}&response=json", headers=UA, timeout=60).json()
print("== BFI82U", j.get("stat"), j.get("date"), j.get("fields"))
for r in j.get("data", []):
    print("  ", r)
time.sleep(3)

r = requests.post("https://www.taifex.com.tw/cht/3/futContractsDateDown", headers=UA, timeout=60,
                  data={"queryStartDate": "2026/10/28", "queryEndDate": "2026/11/06", "commodityId": "TXF"})
txt = r.content.decode("cp950", errors="replace").splitlines()
print("== futContractsDateDown", r.status_code, len(txt))
for line in txt[:1]:
    print("  ", line)
for line in txt[1:]:
    if "外資" in line:
        print("  ", line)
time.sleep(3)

j = requests.get(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={D}&type=ALLBUT0999&response=json", headers=UA, timeout=60).json()
print("== MI_INDEX", j.get("stat"), j.get("date"))
for t in j.get("tables", []):
    title = t.get("title") or ""
    print("  table:", title, "| fields:", (t.get("fields") or [])[:6], "| rows:", len(t.get("data", [])))
    if "漲跌" in title or any("上漲" in str(x[0]) for x in t.get("data", [])[:10] if x):
        for row in t.get("data", []):
            print("     ", row)
