"""診斷新資料來源（只印出結果，不寫檔）。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402


def show(title, fn):
    print(f"\n===== {title}")
    try:
        print(fn())
    except Exception as e:
        print("錯誤", repr(e))


for d in (date(2026, 11, 6), date(2026, 11, 9)):
    for name, fn in (("散戶小台", fetch.taifex_retail_mtx), ("P/C", fetch.taifex_pcr), ("成交金額", fetch.twse_turnover)):
        show(f"{name} {d}", lambda fn=fn, d=d: fn(None, d))
    for s in ("^N225", "^KS11"):
        show(f"{s} {d}", lambda s=s, d=d: fetch.yahoo(s, d, sameDay=True))
# 玩股網公布的「上市融資扣除 ETF 維持率」：06/30 171.46、07/16 157.87、07/20 142.75、07/28 139.69、07/29 133.12
for d in (date(2026, 6, 30), date(2026, 7, 16), date(2026, 7, 20), date(2026, 7, 28), date(2026, 7, 29), date(2026, 11, 6)):
    show(f"融資維持率 {d}", lambda d=d: fetch.margin_ratio_on(d))
show("停班停課", lambda: fetch.typhoon_closed(date.today()))
for d in (date(2026, 10, 26), date(2026, 11, 9), date(2026, 12, 25)):
    show(f"休市 {d}", lambda d=d: fetch.market_closed(d))
