"""資料健康檢查：某商品在最近連續 N 個交易日都抓不到時列為異常。

寫入 data/health.json（網頁會顯示警示），並輸出 GitHub Issue 內文供工作流程開立或關閉通知。
用法：python scripts/check_health.py [--days 2]
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402

OUT = fetch.ROOT / "data" / "health.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    a = ap.parse_args()
    cfg = json.loads(fetch.CONFIG.read_text(encoding="utf-8"))
    history = json.loads(fetch.HISTORY.read_text(encoding="utf-8"))
    problems = []
    for ins in cfg["instruments"]:
        n = 0
        for h in reversed(history):
            if ins["id"] in h["quotes"]:
                break
            n += 1
        if n >= a.days and n < len(history):  # 從未有過資料的是新加入的商品，不列入
            problems.append({"id": ins["id"], "name": ins["name"], "days": n, "source": ins["source"]["type"],
                             "lastSeen": next((h["date"] for h in reversed(history) if ins["id"] in h["quotes"]), None)})
    out = {"checkedAt": datetime.now(fetch.TW).isoformat(timespec="seconds"), "date": history[-1]["date"], "threshold": a.days, "problems": problems}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    if problems:
        body = [f"資料日 {out['date']}，以下商品已連續 {a.days} 個以上交易日抓不到資料，相關因子目前以 0 分計：", ""]
        body += [f"- **{p['name']}**（{p['id']}，來源 `{p['source']}`）：連續 {p['days']} 天，最後一次成功 {p['lastSeen'] or '從未'}" for p in problems]
        body += ["", "可能原因：資料來源改版、網址變更或被擋。可到 Actions 的「每日更新水位資料」查看抓取紀錄。", "", "資料恢復後這個 Issue 會自動關閉。"]
        print("\n".join(body))
    else:
        print("所有商品資料正常。")
    gh = os.environ.get("GITHUB_OUTPUT")
    if gh:
        with open(gh, "a", encoding="utf-8") as f:
            f.write(f"problems={len(problems)}\n")
        (fetch.ROOT / "health_issue.md").write_text("\n".join(body) if problems else "", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
