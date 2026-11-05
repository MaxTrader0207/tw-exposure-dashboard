# 台股水位儀表板

每個交易日收盤後，GitHub Actions 自動抓取官方資料，依規則式模型算出建議大盤持股水位，結果發布在 GitHub Pages。

## 架構

```
config/instruments.json   商品清單（要看哪些商品、從哪裡抓）
config/model.json         水位模型（因子、權重、門檻、換算規則）
scripts/fetch.py          抓取腳本，依來源類型呼叫對應 adapter
data/history.json         每日報價歷史（Actions 自動寫入，保留 400 天）
assets/engine.js          水位計算引擎（只讀設定檔，不寫死代號）
assets/app.js             儀表板畫面（所有區塊依設定自動產生）
.github/workflows/update.yml  排程：週一到週五 台北 14:40、15:30
```

計算只在瀏覽器端做一次，歷史每一天的水位都用「目前的」模型重算，所以調整權重後整段歷史會一起更新。

## 部署

1. 建立 repo（例如 `MaxTrader0207/tw-exposure`），把這些檔案推上 `main`。
2. Settings → Pages → Source 選 `Deploy from a branch`，分支 `main`、資料夾 `/ (root)`。
3. Settings → Actions → General → Workflow permissions 選 `Read and write permissions`。
4. Actions 分頁手動執行一次「每日更新水位資料」確認抓取成功。

## 替換商品

改 `config/instruments.json` 即可，例如把台達電換成奇鋐：

```json
{"id": "3017", "name": "奇鋐", "group": "散熱", "source": {"type": "twse_stock", "symbol": "3017"}, "tags": ["ai_chain"], "role": "AI 伺服器散熱模組"}
```

| source.type | symbol 範例 | 說明 |
|---|---|---|
| `twse_stock` | `2330` | 上市股票（證交所 OpenAPI） |
| `tpex_stock` | `3324` | 上櫃股票（櫃買中心 OpenAPI） |
| `twse_index` | `發行量加權股價指數`、`電子類指數` | 證交所指數，填中文全名 |
| `taifex_fut` | `TX`、`MTX`、`TE`、`TF` | 期交所期貨近月一般盤 |
| `twse_margin_ratio` | （不需要） | 上市大盤融資維持率，由證交所個股融資餘額與收盤價計算 |
| `yahoo` | `DX-Y.NYB`、`TWD=X`、`^SOX`、`^VIX`、`^TNX` | 任何 Yahoo Finance 代號；海外市場用前一個已收盤交易日，加 `"asof": "14:00"` 取台北時間小時線（匯率用） |

`tags` 決定商品屬於哪個族群；族群因子（`breadth`、`rel_avg`）會自動納入同標籤的所有商品，所以加一檔進 `ai_chain` 就會進入族群計算。

換掉的商品若被因子直接引用（例如 `model.json` 裡 `"a": "2330"`），記得一起改因子的 `a`／`b`。缺資料的因子會以 0 分計，頁面上方會顯示警告。

## 調整模型

`config/model.json` 的每個因子：

- `type`：`pct`、`chg`（漲跌值 × `mult`）、`level`（數值 − `center`）、`spread`、`basis`、`breadth`、`rel_avg`
- `a`／`b`：商品 id；`tag`：族群標籤
- `weight`：權重；`scale`：原始值到這個大小時計滿分；`dir`：1 越高越好、−1 越高越差

新增因子類型：在 `assets/engine.js` 的 `FACTOR_TYPES` 加一個函式，並在 `assets/app.js` 的 `NARRATE` 加一句解讀。新增資料來源：在 `scripts/fetch.py` 寫一個 adapter 並登記到 `ADAPTERS`。

## 注意

- 資料以證交所指數日期判斷是否為交易日；休市日不寫入。要補特定日期可在 Actions 手動執行並填入日期。
- Yahoo 的美元指數與台幣匯率是抓取當下的即時值，不是當日收盤。
- 首次部署可在 Actions 手動執行並填「回補天數」（例如 45），一次補齊歷史，β 與相關係數立即可用。
- 規則式模型，僅供部位管理參考，不構成投資建議。
