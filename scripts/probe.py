"""診斷用：期交所台指期夜盤（盤後交易時段）在 CSV 中如何標示日期。"""
import requests

UA = {"User-Agent": "Mozilla/5.0 (tw-exposure-dashboard)"}
r = requests.post("https://www.taifex.com.tw/cht/3/futDataDown", headers=UA, timeout=60, data={
    "down_type": "1", "commodity_id": "TX", "commodity_id2": "", "queryStartDate": "2026/11/04", "queryEndDate": "2026/11/09"})
lines = r.content.decode("cp950", errors="replace").splitlines()
print(lines[0])
for line in lines[1:]:
    parts = line.split(",")
    if len(parts) > 3 and "/" not in parts[2]:
        print(line)
