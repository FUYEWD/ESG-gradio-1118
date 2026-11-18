"""公開資訊資料瀏覽器 — Gradio + PWA + RWD（可部署到 Render）

架構：FastAPI 作為外層，提供 PWA 所需的 manifest / service worker / 離線頁 / 圖示，
再把 Gradio 介面掛載到根路徑 "/"。
"""
import io
import os
import tempfile
from pathlib import Path

import gradio as gr
import pandas as pd
import requests
import urllib3
from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_URL = "https://mopsfin.twse.com.tw/opendata/t187ap46_O_1.csv"


# ======================= 核心功能 =======================
def load_data(url, drop_na, verify_ssl):
    """下載 CSV 並轉成 DataFrame"""
    try:
        response = requests.get(url.strip(), verify=verify_ssl, timeout=30)
        response.raise_for_status()
        text = response.content.decode("utf-8-sig")  # 繁體中文
        df = pd.read_csv(io.StringIO(text))
        if drop_na:
            df = df.dropna()
        df = df.reset_index(drop=True)
    except Exception as e:
        return (
            pd.DataFrame(),
            f"❌ 讀取失敗：{e}",
            "",
            gr.update(choices=[], value=[]),
            pd.DataFrame(),
            None,
        )

    status = f"✅ 讀取成功！共 **{len(df):,}** 筆資料、**{df.shape[1]}** 個欄位"
    cols = list(df.columns)
    return (
        df,
        status,
        build_summary(df),
        gr.update(choices=cols, value=cols),
        df,
        save_csv(df),
    )


def build_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return ""
    missing = int(df.isna().sum().sum())
    numeric_cols = df.select_dtypes("number").columns.tolist()
    return "\n".join(
        [
            "| 項目 | 數值 |",
            "|---|---|",
            f"| 資料筆數 | {len(df):,} |",
            f"| 欄位數量 | {df.shape[1]} |",
            f"| 數值欄位 | {len(numeric_cols)} |",
            f"| 缺值總數 | {missing:,} |",
        ]
    )


def save_csv(df: pd.DataFrame):
    """存成 CSV 供下載（utf-8-sig 讓 Excel 開啟不亂碼）"""
    if df is None or df.empty:
        return None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".csv", prefix="data_")
    df.to_csv(tmp.name, index=False, encoding="utf-8-sig")
    return tmp.name


def filter_data(df, keyword, columns):
    if df is None or len(df) == 0:
        return pd.DataFrame(), None, "尚未載入資料"

    result = df[columns] if columns else df
    if keyword and keyword.strip():
        mask = (
            result.astype(str)
            .apply(lambda col: col.str.contains(keyword.strip(), case=False, na=False))
            .any(axis=1)
        )
        result = result[mask]

    return result, save_csv(result), f"🔎 顯示 **{len(result):,}** / {len(df):,} 筆"


def describe_data(df):
    if df is None or len(df) == 0:
        return pd.DataFrame()
    desc = df.describe(include="all").T.reset_index()
    return desc.rename(columns={"index": "欄位"})


# ======================= PWA：<head> 注入 =======================
HEAD = """
<meta name="theme-color" content="#4f46e5">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="TWSE資料">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<link rel="apple-touch-icon" href="/pwa/icons/apple-touch-icon.png">
<link rel="icon" type="image/png" href="/pwa/icons/icon-192.png">
<style>
  #pwa-install {
    position: fixed; z-index: 9999; display: none;
    right: max(16px, env(safe-area-inset-right));
    bottom: max(16px, env(safe-area-inset-bottom));
    padding: 12px 18px; border: 0; border-radius: 999px; cursor: pointer;
    font: 600 0.95rem "Noto Sans TC", system-ui, sans-serif; color: #fff;
    background: linear-gradient(90deg, #4f46e5, #06b6d4);
    box-shadow: 0 6px 20px rgba(79, 70, 229, .45);
  }
  #pwa-install.show { display: inline-flex; align-items: center; gap: 6px; }
</style>
<script>
  // 1) 註冊 Service Worker
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js", { scope: "/" })
        .catch(function (e) { console.warn("SW 註冊失敗", e); });
    });
  }

  // 2) 安裝按鈕（Android / 桌面 Chrome、Edge）＋ iOS 說明
  (function () {
    var deferredPrompt = null;
    var isStandalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone;
    var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);

    function getBtn() {
      var b = document.getElementById("pwa-install");
      if (!b) {
        b = document.createElement("button");
        b.id = "pwa-install";
        b.type = "button";
        b.textContent = "📲 安裝 App";
        document.body.appendChild(b);
      }
      return b;
    }

    window.addEventListener("beforeinstallprompt", function (e) {
      e.preventDefault();
      deferredPrompt = e;
      if (!isStandalone) {
        var b = getBtn();
        b.classList.add("show");
        b.onclick = function () {
          deferredPrompt.prompt();
          deferredPrompt.userChoice.then(function () {
            deferredPrompt = null;
            b.classList.remove("show");
          });
        };
      }
    });

    window.addEventListener("appinstalled", function () {
      var b = document.getElementById("pwa-install");
      if (b) b.classList.remove("show");
    });

    window.addEventListener("load", function () {
      if (isIOS && !isStandalone) {
        var b = getBtn();
        b.classList.add("show");
        b.onclick = function () {
          alert("安裝方式：點選 Safari 底部的「分享」圖示，再選「加入主畫面」。");
        };
      }
    });
  })();
</script>
"""

# ======================= RWD：CSS =======================
CSS = """
:root { --pad: clamp(8px, 2.5vw, 24px); }

.gradio-container {
  max-width: 1200px !important; margin: auto;
  padding-left: max(var(--pad), env(safe-area-inset-left)) !important;
  padding-right: max(var(--pad), env(safe-area-inset-right)) !important;
}

#title { text-align: center; padding: clamp(10px, 3vw, 22px) 0 4px 0; }
#title h1 {
  font-size: clamp(1.4rem, 5vw, 2.3rem); margin: 0 0 4px 0; line-height: 1.25;
  background: linear-gradient(90deg, #4f46e5, #06b6d4);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
#title p { color: #6b7280; margin: 0; font-size: clamp(.85rem, 2.6vw, 1rem); }
#status { font-size: clamp(.95rem, 2.8vw, 1.05rem); }

/* 讓按鈕、輸入框在手機上好點擊 */
button, input, textarea, select { min-height: 44px; }

/* 平板以下：Row 內的元件自動換行、各佔一整列 */
@media (max-width: 768px) {
  .stack-mobile { flex-wrap: wrap !important; }
  .stack-mobile > * { min-width: 100% !important; flex: 1 1 100% !important; }
  .gradio-container { font-size: 15px; }
  table { font-size: 13px; }
}

/* 手機：縮小間距 */
@media (max-width: 480px) {
  .gradio-container { padding-top: 4px !important; }
  .tab-nav button, .tabs > div:first-child button { padding: 8px 10px; font-size: 14px; }
}

/* 超寬螢幕保持舒適閱讀寬度 */
@media (min-width: 1600px) { .gradio-container { max-width: 1400px !important; } }
"""

theme = gr.themes.Soft(
    primary_hue="indigo",
    secondary_hue="cyan",
    font=[gr.themes.GoogleFont("Noto Sans TC"), "sans-serif"],
)

# ======================= Gradio 介面 =======================
with gr.Blocks(
    theme=theme,
    css=CSS,
    head=HEAD,
    title="公開資訊資料瀏覽器",
    analytics_enabled=False,
) as demo:
    df_state = gr.State(pd.DataFrame())

    gr.HTML(
        """
        <div id="title">
          <h1>📊 公開資訊資料瀏覽器</h1>
          <p>即時下載 TWSE / MOPS 開放資料 CSV，搜尋、篩選並匯出</p>
        </div>
        """
    )

    with gr.Group():
        with gr.Row(elem_classes="stack-mobile"):
            url_box = gr.Textbox(
                label="🔗 CSV 連結",
                value=DEFAULT_URL,
                placeholder="貼上要下載的 CSV 網址（可更換連結）",
                scale=6,
            )
            load_btn = gr.Button("🚀 載入資料", variant="primary", scale=1, min_width=120)
        with gr.Row(elem_classes="stack-mobile"):
            drop_na = gr.Checkbox(label="移除含缺值的列 (dropna)", value=True)
            verify_ssl = gr.Checkbox(label="驗證 SSL 憑證 (verify)", value=False)

    status_md = gr.Markdown("尚未載入資料", elem_id="status")

    with gr.Tabs():
        with gr.Tab("📋 資料表"):
            with gr.Row(elem_classes="stack-mobile"):
                keyword = gr.Textbox(
                    label="🔍 關鍵字搜尋（比對所有欄位）",
                    placeholder="例如：公司名稱、代號…",
                    scale=3,
                )
                col_select = gr.Dropdown(
                    label="顯示欄位", choices=[], value=[], multiselect=True, scale=4
                )
                filter_btn = gr.Button("套用篩選", variant="secondary", scale=1)
            filter_msg = gr.Markdown("")
            table = gr.Dataframe(label="資料內容", interactive=False, wrap=True, max_height=520)
            download = gr.File(label="⬇️ 下載目前表格 (CSV)")

        with gr.Tab("📈 資料摘要"):
            summary_md = gr.Markdown("")
            stat_btn = gr.Button("計算敘述統計")
            stat_table = gr.Dataframe(label="敘述統計", interactive=False, wrap=True)

    load_btn.click(
        load_data,
        inputs=[url_box, drop_na, verify_ssl],
        outputs=[df_state, status_md, summary_md, col_select, table, download],
    )
    filter_btn.click(
        filter_data, inputs=[df_state, keyword, col_select], outputs=[table, download, filter_msg]
    )
    keyword.submit(
        filter_data, inputs=[df_state, keyword, col_select], outputs=[table, download, filter_msg]
    )
    stat_btn.click(describe_data, inputs=df_state, outputs=stat_table)


# ======================= FastAPI 外層（PWA 路由） =======================
app = FastAPI(title="TWSE Data Explorer PWA", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/manifest.json", include_in_schema=False)
@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    # Gradio 的 HTML 範本固定連到 /manifest.json（未啟用 pwa 時會回 404），
    # 因此在 Gradio 掛載之前先由這裡回應，瀏覽器才讀得到我們的 manifest
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    # 放在根路徑，scope 才能涵蓋整個網站；no-cache 讓更新能即時生效
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )


@app.get("/offline.html", include_in_schema=False)
def offline():
    return FileResponse(STATIC_DIR / "offline.html", media_type="text/html")


@app.get("/healthz", include_in_schema=False)
def healthz():
    return PlainTextResponse("ok")


# 資料夾是 static/，但網址刻意用 /pwa：Gradio 自己的前端資源就在 /static/，
# 若也掛在 /static 會互相蓋掉導致介面壞掉
app.mount("/pwa", StaticFiles(directory=STATIC_DIR), name="pwa")

# Gradio 必須最後掛載到 "/"
app = gr.mount_gradio_app(app, demo, path="/")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 7860))
    # Render 在反向代理後面，開啟 proxy_headers 才能正確辨識 https
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
