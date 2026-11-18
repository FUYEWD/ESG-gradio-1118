# 📊 公開資訊資料瀏覽器（PWA + RWD 版）

使用 [Gradio](https://www.gradio.app/) 製作，可即時下載臺灣證券交易所／公開資訊觀測站的開放資料 CSV，並進行搜尋、篩選、統計與匯出。
本版本支援 **PWA（可安裝到手機／桌面）** 與 **RWD（手機、平板、桌機自適應）**。

## ✨ 功能

- 🔗 自訂 CSV 連結（預設 `t187ap46_O_1.csv`），可選擇是否 `dropna`、是否驗證 SSL
- 🔍 關鍵字搜尋（比對所有欄位）、自選顯示欄位
- 📈 資料摘要與敘述統計
- ⬇️ 匯出目前表格為 CSV（`utf-8-sig`，Excel 開啟不亂碼）
- 📲 **PWA**：可「安裝」成 App、獨立視窗開啟、離線時顯示友善提示頁
- 📱 **RWD**：手機上輸入框與按鈕自動上下堆疊、觸控友善（44px 以上點擊區）、支援瀏覽器瀏海安全區

## 🏗️ 架構

```
瀏覽器 ──► FastAPI（外層）
            ├─ /manifest.json         PWA manifest（同時提供 /manifest.webmanifest）
            ├─ /sw.js                 Service Worker（放根路徑，scope 才能涵蓋全站）
            ├─ /offline.html          離線頁
            ├─ /pwa/*                 對應專案的 static/ 資料夾（圖示等）
            ├─ /healthz               Render 健康檢查
            └─ /  ──► Gradio 介面（mount_gradio_app）
```

## 📁 專案結構

```
.
├── app.py                    # FastAPI + Gradio 主程式
├── static/
│   ├── manifest.webmanifest  # 應用程式資訊（名稱、圖示、主題色）
│   ├── sw.js                 # Service Worker
│   ├── offline.html          # 離線頁
│   └── icons/                # 192 / 512 / maskable / apple-touch 圖示
├── tools/
│   └── make_icons.py         # （選用）重新產生圖示，需 pillow
├── .gitignore
├── .python-version           # 指定 Python 版本（Render 會讀取）
├── README.md
├── render.yaml               # Render Blueprint（選用）
└── requirements.txt
```

> `static/` 內的檔案對外網址是 `/pwa/...`（不是 `/static/...`），因為 Gradio 自己的前端資源佔用了 `/static/`。

## 💻 本機執行

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

開啟 http://localhost:7860 。`localhost` 被瀏覽器視為安全來源，PWA 功能可直接測試。

## 🚀 部署到 Render

### 1. 上傳到 GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/<你的帳號>/<你的repo>.git
git push -u origin main
```

> 請確認 `static/` 資料夾（含 `icons/`）與 `.python-version` 一併上傳，否則 PWA 無法運作。

### 2. 在 Render 建立服務

**方法 A：手動建立 Web Service**

| 項目 | 值 |
|---|---|
| Runtime | `Python 3` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python app.py` |
| Health Check Path | `/healthz` |
| Instance Type | `Free` |
| Python 版本 | 由 repo 根目錄的 `.python-version`（`3.11.9`）決定，不需另設環境變數 |

**方法 B：Blueprint** — 在 Render 選 **New → Blueprint**，指定此 repo，會自動讀取 `render.yaml`。

Render 會自動提供 HTTPS 網址，這是 PWA 的必要條件。

## 📲 如何安裝成 App

| 平台 | 方式 |
|---|---|
| Android / Windows / macOS（Chrome、Edge） | 點畫面右下角「📲 安裝 App」，或網址列右側的安裝圖示 |
| iPhone / iPad（Safari） | 點「分享」→「加入主畫面」（畫面右下角的按鈕也會提示操作方式） |

## 🔎 驗證 PWA 是否正常

1. Chrome 開啟網站 → `F12` → **Application** 分頁
2. **Manifest**：確認名稱、圖示、無錯誤，並顯示可安裝
3. **Service Workers**：狀態為 *activated and is running*
4. 也可用 **Lighthouse → Progressive Web App** 檢查

## ⚠️ 注意事項

- **離線能力有限**：資料需要連線下載，離線時只會顯示離線提示頁，不會有資料。Service Worker 只快取靜態資源，Gradio 的動態 API 一律走網路。
- **更新快取**：修改 `static/sw.js` 的快取行為時，請調整檔內的 `VERSION`，舊快取會自動清除。
- **Free 方案會休眠**：閒置後第一次開啟需等約 30–60 秒喚醒。
- **SSL 驗證**：預設關閉（`verify=False`），因為來源站台憑證在部分環境會驗證失敗；環境允許時建議勾選驗證。
- **Gradio 版本**：限制為 5.x。Gradio 6 對 `css` 等參數的位置有調整，升級前請先測試。

## 📄 資料來源

[臺灣證券交易所／公開資訊觀測站開放資料](https://mopsfin.twse.com.tw/opendata/t187ap46_O_1.csv)

## 📝 授權

MIT License
