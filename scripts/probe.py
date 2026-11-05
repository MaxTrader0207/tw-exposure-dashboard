"""診斷用：檢查財經M平方大盤融資維持率頁面與資料端點能否從 GitHub Actions 取得。"""
import re

import requests

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9",
}
s = requests.Session()
page = "https://www.macromicro.me/charts/53117/taiwan-taiex-maintenance-margin"
r = s.get(page, headers=UA, timeout=30)
print("page", r.status_code, r.headers.get("content-type"), len(r.text), r.headers.get("server"), r.headers.get("cf-mitigated"))
print(r.text[:500].replace("\n", " "))
for pat in [r"data-stk=\"([^\"]+)\"", r"/charts/data/\d+", r"\"stk\"\s*:\s*\"([^\"]+)\"", r"Bearer [A-Za-z0-9]+"]:
    print(pat, re.findall(pat, r.text)[:3])
m = re.search(r"data-stk=\"([^\"]+)\"", r.text)
hdr = {**UA, "Referer": page, "X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
if m:
    hdr["Authorization"] = "Bearer " + m.group(1)
d = s.get("https://www.macromicro.me/charts/data/53117", headers=hdr, timeout=30)
print("data", d.status_code, d.headers.get("content-type"), len(d.text))
print(d.text[:800].replace("\n", " "))
