"""診斷資料來源（只印出結果，不寫檔）。"""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402
import requests

j = requests.get("https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName=BP01D01en", headers=fetch.UA, timeout=60).json()
print("keys", list(j.keys()), list(j["data"].keys()))
for k, v in j["data"].items():
    if k != "dataSets":
        print(k, json.dumps(v, ensure_ascii=False)[:1500])
ds = j["data"]["dataSets"]
print(len(ds), ds[0], ds[-3:])
for d in (date(2023, 6, 1), date(2023, 11, 20), date(2023, 12, 20), date(2024, 3, 1)):
    try:
        print(d, "FOI", fetch.taifex_foreign_oi("TXF", d), "MTXR", fetch.taifex_retail_mtx(None, d))
    except Exception as e:
        print(d, repr(e))
