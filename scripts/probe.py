"""診斷新資料來源（只印出結果，不寫檔）。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402

for d in (date(2026, 11, 6), date(2026, 11, 9)):
    print(d, "P/C", fetch.taifex_pcr(None, d))
print("融資", fetch.twse_margin_ratio(None, date(2026, 11, 9), calib=0.901))
