"""用歷史資料客觀推導水位模型的權重，寫回 config/model.json，推導過程寫到 data/calibration.json。

流程
1. 歷史資料（約兩年，受限於匯率小時線的可取得長度）
   - 上市櫃個股：Yahoo 還原日線（排除除權息跳空）；加權指數：Yahoo ^TWII。
   - 台指期：期交所每日行情，近月一般盤，與每日自動抓取相同來源。
   - 匯率、美元指數：Yahoo 小時線在台北 14:00 取樣，與每日自動抓取的時點相同，避免用到台股收盤後的資訊。
   - 其他海外市場：使用前一個已收盤交易日。
2. 每個因子的每日原始值，定義與 assets/engine.js 完全相同（factor_raw 是它的 Python 對照版）。
3. 預測目標：加權指數未來 H 個交易日報酬（H = model.json 的 horizonDays）。
4. 標準化：z = (原始值 − 平均) ÷ 標準差，截斷在 ±3；缺值視為 0（平均狀態）。
5. 權重：脊迴歸。懲罰強度用「訓練期最後 25% 當驗證集」挑選，候選包含「完全不用訊號」；
   若任何懲罰強度都無法在驗證集上勝過「不用訊號」，該期所有權重為 0。
   係數方向與 prior（事前經濟邏輯）相反的因子逐一剔除後重估，避免擬合雜訊。
6. 換算：權重 = slopePerSigma × 係數 ÷ 綜合訊號標準差，即「該因子偏離 1σ 時水位變動幾個百分點」。
7. 前推式樣本外回測：前 250 個交易日起，每 20 個交易日只用當時以前的資料重估一次，
   與舊版手調模型、固定水位、買進持有比較；波動率覆蓋只有在樣本外同時改善夏普與最大回撤時才啟用。

用法：python scripts/calibrate.py
"""
import csv
import io
import json
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch  # noqa: E402  共用連線、重試與 Yahoo 解析

ROOT = fetch.ROOT
CONFIG = ROOT / "config" / "instruments.json"
MODEL = ROOT / "config" / "model.json"
OUT = ROOT / "data" / "calibration.json"
INDEX_TO_YAHOO = {"發行量加權股價指數": "^TWII"}
TRAIN_MIN = 250
STEP = 20
ALPHAS = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0]

# 舊版手調模型（v1），只用來比較
OLD = [
    {"type": "pct", "a": "USDTWD", "w": 20, "scale": 0.5, "dir": -1},
    {"type": "spread", "a": "USDTWD", "b": "DXY", "w": 15, "scale": 1, "dir": -1},
    {"type": "pct", "a": "TAIEX", "w": 15, "scale": 2, "dir": 1},
    {"type": "basis", "a": "TXF", "b": "TAIEX", "w": 10, "scale": 0.3, "dir": 1},
    {"type": "pct", "a": "2330", "w": 15, "scale": 3, "dir": 1},
    {"type": "breadth", "tag": "ai_chain", "w": 10, "scale": 1, "dir": 1},
    {"type": "rel_avg", "tag": "ai_chain", "b": "TAIEX", "w": 15, "scale": 3, "dir": 1},
]


# ---------------- 歷史資料 ----------------

def pairs_to_df(pairs):
    df = pd.DataFrame(pairs, columns=["date", "last"]).drop_duplicates("date", keep="last").set_index("date").sort_index()
    df.index = pd.to_datetime(df.index)
    df["pct"] = df["last"].pct_change() * 100
    return df


def taifex_history(symbol, start):
    rows = []
    d = date(start.year, start.month, 1)
    today = datetime.now(fetch.TW).date()
    while d <= today:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        end = min(nxt - timedelta(days=1), today)
        r = fetch.http("POST", "https://www.taifex.com.tw/cht/3/futDataDown", data={
            "down_type": "1", "commodity_id": symbol, "commodity_id2": "",
            "queryStartDate": f"{d:%Y/%m/%d}", "queryEndDate": f"{end:%Y/%m/%d}"})
        lines = list(csv.reader(io.StringIO(r.content.decode("cp950", errors="replace"))))
        if lines and "交易日期" in lines[0][0]:
            h = [x.strip() for x in lines[0]]
            ix = {k: h.index(k) for k in ("交易日期", "契約", "到期月份(週別)", "收盤價", "漲跌價", "交易時段")}
            for x in lines[1:]:
                if len(x) <= ix["交易時段"] or x[ix["契約"]].strip() != symbol or x[ix["交易時段"]].strip() != "一般":
                    continue
                m = x[ix["到期月份(週別)"]].strip()
                c, ch = fetch.num(x[ix["收盤價"]]), fetch.num(x[ix["漲跌價"]])
                if "/" in m or c is None or ch is None:
                    continue
                rows.append((x[ix["交易日期"]].strip(), m, c, ch))
        else:
            print(f"  期交所 {d:%Y-%m} 無資料或格式不符")
        d = nxt
    df = pd.DataFrame(rows, columns=["date", "month", "last", "chg"])
    df = df.sort_values(["date", "month"]).groupby("date").first()
    df.index = pd.to_datetime(df.index)
    df["pct"] = df["chg"] / (df["last"] - df["chg"]) * 100
    return df[["last", "pct"]]


def load_instrument(ins, start):
    src = ins["source"]
    t, s = src["type"], src["symbol"]
    if t in ("twse_stock", "tpex_stock"):
        bars, _, _ = fetch.yahoo_daily(s + (".TW" if t == "twse_stock" else ".TWO"), "5y", adjusted=True)
        return pairs_to_df(bars), "same"
    if t == "twse_index":
        sym = INDEX_TO_YAHOO.get(s)
        if not sym:
            raise ValueError(f"指數「{s}」沒有對應的歷史資料代號，請在 INDEX_TO_YAHOO 補上")
        bars, _, _ = fetch.yahoo_daily(sym, "5y")
        return pairs_to_df(bars), "same"
    if t == "taifex_fut":
        return taifex_history(s, start), "same"
    if t == "yahoo":
        if src.get("asof"):
            try:
                snaps = fetch.yahoo_snapshots(s, src["asof"], "2y")
            except Exception:
                snaps = fetch.yahoo_snapshots(s, src["asof"], "1y")
            return pairs_to_df(snaps), "same"
        bars, taipei, _ = fetch.yahoo_daily(s, "5y", adjusted=True)
        return pairs_to_df(bars), "same" if taipei else "prev"
    raise ValueError(f"未知來源類型 {t}")


def build_panel(cfg, start):
    raw = {}
    for ins in cfg["instruments"]:
        try:
            raw[ins["id"]] = load_instrument(ins, start)
            print(f"  {ins['id']:<8} {len(raw[ins['id']][0])} 筆")
        except Exception as e:
            print(f"  {ins['id']:<8} 無法取得歷史：{e}")
    bench = cfg["benchmark"]
    cal = raw[bench][0].index
    cal = cal[cal >= pd.Timestamp(start)]
    panel = {}
    for k, (df, align) in raw.items():
        if align == "same":
            panel[k] = df.reindex(cal)
        else:  # 海外市場：取台股日期之前最後一個已收盤交易日
            left = pd.DataFrame(index=cal).reset_index().rename(columns={"index": "date"})
            left.columns = ["date"]
            right = df.reset_index().rename(columns={"index": "date"})
            right.columns = ["date"] + list(df.columns)
            m = pd.merge_asof(left, right, on="date", direction="backward", allow_exact_matches=False)
            panel[k] = m.set_index("date")
    return panel, cal


# ---------------- 因子（與 assets/engine.js 對照） ----------------

def members(cfg, tag):
    return [i["id"] for i in cfg["instruments"] if tag in (i.get("tags") or [])]


def col(P, cal, k, c):
    return P[k][c] if k in P else pd.Series(np.nan, index=cal)


def day_raw(f, P, cal, cfg):
    t = f["type"]
    if t == "pct":
        return col(P, cal, f["a"], "pct")
    if t == "spread":
        return col(P, cal, f["a"], "pct") - col(P, cal, f["b"], "pct")
    if t == "basis":
        b = col(P, cal, f["b"], "last")
        return (col(P, cal, f["a"], "last") - b) / b * 100
    if t in ("breadth", "rel_avg"):
        M = pd.DataFrame({k: col(P, cal, k, "pct") for k in members(cfg, f["tag"])}, index=cal)
        cnt = M.notna().sum(axis=1)
        if t == "breadth":
            r = ((M > 0).sum(axis=1) - (M < 0).sum(axis=1)) / cnt
        else:
            r = M.mean(axis=1) - col(P, cal, f["b"], "pct")
        return r.where(cnt > 0)
    raise ValueError(f"未知因子類型 {t}")


def factor_raw(f, P, cal, cfg):
    t = f["type"]
    if t == "vol":
        return col(P, cal, f["a"], "pct").rolling(f["n"]).std() * math.sqrt(252)
    if t == "sum_pct":
        return col(P, cal, f["a"], "pct").rolling(f["n"]).sum()
    if t == "ma_gap":
        x = col(P, cal, f["a"], "last")
        return (x / x.rolling(f["n"]).mean() - 1) * 100
    x = day_raw(f, P, cal, cfg)
    if f.get("demean"):
        x = x - x.rolling(f["demean"]).mean()
    return x


# ---------------- 估計 ----------------

def ridge_signed(Z, y, alpha, prior):
    """係數方向必須與 prior 相同（prior 0 表示不限）；違反者逐一剔除後重估。"""
    n, k = Z.shape
    active = list(range(k))
    yc = y - y.mean()
    beta = np.zeros(k)
    while active:
        Za = Z[:, active]
        b = np.linalg.solve(Za.T @ Za + alpha * n * np.eye(len(active)), Za.T @ yc)
        viol = [(prior[j] * b[i], i) for i, j in enumerate(active) if prior[j] * b[i] < 0]
        if not viol:
            beta[:] = 0
            beta[active] = b
            return beta
        active.pop(min(viol)[1])
    return beta


def standardize(X, mu, sd):
    return ((X - mu) / sd).clip(-3, 3).fillna(0).to_numpy(dtype=float, copy=True)


def fit(X, y, prior, H):
    """回傳 mu、sd、beta、選到的 alpha、綜合訊號標準差、驗證集資訊。"""
    mu, sd = X.mean(), X.std().replace(0, np.nan)
    ok = sd.notna() & (X.notna().mean() > 0.6)
    sd = sd.fillna(1.0)
    Z = standardize(X, mu, sd)
    Z[:, ~ok.to_numpy()] = 0
    yv = y.to_numpy()
    n = len(yv)
    cut = int(n * 0.75)
    tr, va = slice(0, max(cut - H, 10)), slice(cut, n)
    base_mse = np.mean((yv[va] - yv[tr].mean()) ** 2)
    best = (base_mse, None)
    for a in ALPHAS:
        b = ridge_signed(Z[tr], yv[tr], a, prior)
        mse = np.mean((yv[va] - (yv[tr].mean() + Z[va] @ b)) ** 2)
        if mse < best[0]:
            best = (mse, a)
    alpha = best[1]
    beta = ridge_signed(Z, yv, alpha, prior) if alpha is not None else np.zeros(Z.shape[1])
    comp_sd = float(np.std(Z @ beta, ddof=1)) if np.any(beta) else 0.0
    return {"mu": mu, "sd": sd, "ok": ok, "beta": beta, "alpha": alpha, "comp_sd": comp_sd,
            "val_gain": float(1 - best[0] / base_mse) if base_mse else 0.0}


def weights_of(m, slope):
    return m["beta"] * slope / m["comp_sd"] if m["comp_sd"] > 0 else np.zeros_like(m["beta"])


def exposure_new(X, m, w, policy, vol=None, target=None, min_mult=0.5):
    pts = standardize(X, m["mu"], m["sd"]) @ w
    lin = policy["base"] + pts
    if vol is not None and target:
        mult = np.clip(target / vol.to_numpy(), min_mult, 1.0)
        mult = np.where(np.isnan(mult), 1.0, mult)
        lin = lin * mult
    return np.clip(lin, policy["floor"], policy["cap"])


def exposure_old(P, cal, cfg):
    score = np.zeros(len(cal))
    for f in OLD:
        x = day_raw(f, P, cal, cfg).to_numpy()
        s = np.clip(f["dir"] * x / f["scale"], -1, 1) * f["w"]
        score += np.nan_to_num(s)
    return np.clip(60 + 0.4 * score, 10, 90)


def metrics(name, r, exp):
    r = pd.Series(r).dropna()
    eq = (1 + r / 100).cumprod()
    yrs = len(r) / 252
    sd = r.std()
    return {
        "name": name,
        "cagr": round((eq.iloc[-1] ** (1 / yrs) - 1) * 100, 2),
        "vol": round(sd * math.sqrt(252), 2),
        "sharpe": round(r.mean() / sd * math.sqrt(252), 2) if sd else None,
        "maxdd": round(float((eq / eq.cummax() - 1).min()) * 100, 2),
        "avgExp": round(float(np.mean(exp)), 1),
        "turnover": round(float(np.mean(np.abs(np.diff(exp)))), 2) if len(exp) > 1 else 0,
    }


def ic_t(ic, n, H):
    neff = n / H
    return ic * math.sqrt(max(neff - 2, 1)) / math.sqrt(max(1 - ic ** 2, 1e-9))


# ---------------- 主程式 ----------------

def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    model = json.loads(MODEL.read_text(encoding="utf-8"))
    pol = model["policy"]
    H = int(model.get("horizonDays", 5))
    slope = float(pol.get("slopePerSigma", 15))
    start = datetime.now(fetch.TW).date() - timedelta(days=729)

    print("== 下載歷史資料")
    P, cal = build_panel(cfg, start)
    bench = cfg["benchmark"]
    facs = model["factors"]
    keys = [f["key"] for f in facs]
    prior = np.array([int(f.get("prior", 0)) for f in facs])
    X = pd.DataFrame({f["key"]: factor_raw(f, P, cal, cfg) for f in facs}, index=cal)
    last = P[bench]["last"]
    y = (last.shift(-H) / last - 1) * 100
    r1 = (last.shift(-1) / last - 1) * 100
    vo = pol.get("volOverlay") or {"a": bench, "n": 20, "minMult": 0.5}
    vol = col(P, cal, vo["a"], "pct").rolling(vo["n"]).std() * math.sqrt(252)

    warm = max([f.get("n", 0) for f in facs] + [f.get("demean", 0) for f in facs] + [vo["n"]])
    keep = np.arange(len(cal)) >= warm
    X, y, r1, vol, cal = X[keep], y[keep], r1[keep], vol[keep], cal[keep]
    P = {k: v[keep] for k, v in P.items()}
    n = len(cal)
    print(f"== 樣本 {cal[0].date()} ~ {cal[-1].date()}，{n} 個交易日")

    # 單因子 IC（全樣本，Spearman）
    uni = []
    for k in keys:
        ok = X[k].notna() & y.notna()
        ic5 = X[k][ok].rank().corr(y[ok].rank()) if ok.sum() > 30 else None
        ok1 = X[k].notna() & r1.notna()
        ic1 = X[k][ok1].rank().corr(r1[ok1].rank()) if ok1.sum() > 30 else None
        uni.append({"ic5": None if ic5 is None or np.isnan(ic5) else round(ic5, 3),
                    "ic1": None if ic1 is None or np.isnan(ic1) else round(ic1, 3),
                    "t5": None if ic5 is None or np.isnan(ic5) else round(ic_t(ic5, ok.sum(), H), 2),
                    "n": int(ok.sum())})

    # 前推式樣本外
    print("== 前推式回測")
    exp_new = np.full(n, np.nan)
    exp_vol = np.full(n, np.nan)
    steps = []
    for t in range(TRAIN_MIN, n, STEP):
        tr = slice(0, t - H)
        ytr = y.iloc[tr]
        good = ytr.notna().to_numpy()
        m = fit(X.iloc[tr][good], ytr[good], prior, H)
        w = weights_of(m, slope)
        te = slice(t, min(t + STEP, n))
        target = float(vol.iloc[tr].median())
        exp_new[te] = exposure_new(X.iloc[te], m, w, pol)
        exp_vol[te] = exposure_new(X.iloc[te], m, w, pol, vol.iloc[te], target, vo.get("minMult", 0.5))
        steps.append({"date": str(cal[t].date()), "alpha": m["alpha"], "active": int(np.count_nonzero(w))})
    exp_old = exposure_old(P, cal, cfg)

    oos = np.arange(n) >= TRAIN_MIN
    oos &= r1.notna().to_numpy()
    rr = r1.to_numpy()
    strategies = [
        ("新模型", exp_new),
        ("新模型＋波動率覆蓋", exp_vol),
        ("舊版手調模型", exp_old),
        (f"固定 {pol['base']}%", np.full(n, float(pol["base"]))),
        ("買進持有 100%", np.full(n, 100.0)),
    ]
    bt = [metrics(nm, rr[oos] * e[oos] / 100, e[oos]) for nm, e in strategies]
    for b in bt:
        print("  ", b)
    oos_ic = pd.Series(exp_new[oos]).rank().corr(pd.Series(y.to_numpy()[oos]).rank())

    s_new, s_vol = bt[0], bt[1]
    overlay_on = bool(s_vol["sharpe"] and s_new["sharpe"] and s_vol["sharpe"] > s_new["sharpe"] and s_vol["maxdd"] >= s_new["maxdd"])

    # 全樣本最終模型
    good = y.notna().to_numpy()
    m = fit(X[good], y[good], prior, H)
    w = weights_of(m, slope)
    target = round(float(vol.median()), 1)

    status = []
    for i, f in enumerate(facs):
        if not m["ok"].iloc[i]:
            status.append("剔除：歷史資料不足")
        elif w[i] != 0:
            status.append("採用")
        elif m["alpha"] is None:
            status.append("未採用：整體訊號未勝過不用訊號")
        elif uni[i]["ic5"] is not None and prior[i] * uni[i]["ic5"] < 0:
            status.append("剔除：歷史方向與預期相反")
        else:
            status.append("剔除：與其他因子重疊，邊際貢獻為反向")

    for i, f in enumerate(facs):
        f["mean"] = round(float(m["mu"].iloc[i]), 4) if not np.isnan(m["mu"].iloc[i]) else 0
        f["std"] = round(float(m["sd"].iloc[i]), 4)
        f["weight"] = round(float(w[i]), 2)
    model["calibratedAt"] = datetime.now(fetch.TW).strftime("%Y-%m-%d")
    pol["volOverlay"] = {**vo, "enabled": overlay_on, "target": target}
    MODEL.write_text(json.dumps(model, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    Z = pd.DataFrame(standardize(X, m["mu"], m["sd"]), columns=keys)
    corr = Z.corr().round(2).fillna(0).values.tolist()

    eq_idx = np.where(oos)[0]
    eq = {}
    for nm, e in strategies:
        rs = rr[eq_idx] * e[eq_idx] / 100
        eq[nm] = [round(v, 4) for v in np.cumprod(1 + rs / 100)]
    out = {
        "calibratedAt": model["calibratedAt"],
        "horizonDays": H,
        "slopePerSigma": slope,
        "sample": {"start": str(cal[0].date()), "end": str(cal[-1].date()), "days": n,
                   "oosStart": str(cal[TRAIN_MIN].date()), "oosDays": int(oos.sum())},
        "alpha": m["alpha"],
        "validationGain": round(m["val_gain"] * 100, 2),
        "oosIC": None if np.isnan(oos_ic) else round(float(oos_ic), 3),
        "factors": [{"key": f["key"], "name": f["name"], "prior": int(f.get("prior", 0)), **uni[i],
                     "beta": round(float(m["beta"][i]), 4), "mean": f["mean"], "std": f["std"],
                     "weight": f["weight"], "status": status[i]} for i, f in enumerate(facs)],
        "corr": {"keys": keys, "names": [f["name"] for f in facs], "matrix": corr},
        "volOverlay": {"enabled": overlay_on, "target": target,
                       "reason": "樣本外同時改善夏普值與最大回撤，啟用" if overlay_on else "樣本外未能同時改善夏普值與最大回撤，不啟用"},
        "backtest": bt,
        "steps": steps,
        "equity": {"dates": [str(cal[i].date()) for i in eq_idx], "series": eq,
                   "exposure": [round(float(v), 1) for v in exp_new[eq_idx]]},
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("== 最終權重（每 1σ 對水位的百分點）")
    for f, s in zip(facs, status):
        print(f"  {f['name']:<12} weight {f['weight']:>7}  mean {f['mean']:>8}  std {f['std']:>8}  {s}")
    print(f"  alpha {m['alpha']}  驗證集 MSE 改善 {out['validationGain']}%  波動率覆蓋 {overlay_on}（目標 {target}%）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
