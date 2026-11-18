# -*- coding: utf-8 -*-
"""
多策略 RAG 文件問答系統 v2 — ChromaDB + PDF/DOCX 版本（優化版）
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import chromadb
import gradio as gr
import numpy as np
from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph
from groq import Groq
from pypdf import PdfReader
from sentence_transformers import SentenceTransformer
from sklearn.feature_extraction.text import TfidfVectorizer


# ══════════════════════════════════════════════════════════
#  RAG 核心邏輯（優化版）
# ══════════════════════════════════════════════════════════
class MultiStrategyRAG:

    STRATEGY_MAP = {
        "semantic":      "1  ChromaDB 語意搜尋",
        "tfidf":         "2  TF-IDF 關鍵詞",
        "hybrid":        "3  混合搜尋",
        "rerank":        "4  重新排序",
        "multi_query":   "5  多查詢擴展",
        "compress":      "6  上下文壓縮",
        "parent_child":  "7  父子文檔",
        "hyde":          "8  假設性答案 HyDE",
    }

    def __init__(
        self,
        chroma_path: str = "./chroma_db",
        collection_name: str = "audit_rag_chunks",
        child_collection_name: str = "audit_rag_child_chunks",
    ):
        # API client 改為 None，由使用者透過 UI 輸入後動態建立
        self.client: Groq | None = None

        self.embedding_model = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )

        self.chroma_client = chromadb.PersistentClient(path=chroma_path)
        self.collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self.child_collection = self.chroma_client.get_or_create_collection(
            name=child_collection_name,
            metadata={"hnsw:space": "cosine"},
        )

        self.session_id: str | None = None
        self.source_name: str = ""
        self.file_type: str = ""
        self.chunks: list[str] = []
        self.child_chunks: list[str] = []
        self.tfidf_vectorizer: TfidfVectorizer | None = None
        self.tfidf_matrix = None

    # ── API Key 管理 ─────────────────────────────────────
    def set_api_key(self, api_key: str) -> None:
        """動態設定 Groq API Key，建立或更新 client。"""
        key = (api_key or "").strip()
        self.client = Groq(api_key=key) if key else None

    # ── 文件載入 ─────────────────────────────────────────
    def load_document(self, file_path: str) -> str:
        try:
            path = Path(file_path)
            if not path.exists():
                return "✗ 載入失敗：找不到檔案"

            suffix = path.suffix.lower()
            if suffix not in (".pdf", ".docx"):
                return "✗ 目前僅支援 PDF 與 DOCX 檔案"

            self.source_name = path.name
            self.file_type = suffix.lstrip(".")
            self.session_id = (
                f"{int(time.time())}_{re.sub(r'[^a-zA-Z0-9]+', '_', path.stem)[:40]}"
            )

            if suffix == ".pdf":
                full_text, stats = self._extract_pdf(path)
            else:
                full_text, stats = self._extract_docx(path)

            if not full_text.strip():
                return "✗ 載入失敗：文件沒有可擷取文字，可能是掃描圖片檔，需先 OCR"

            self.chunks = self._split(full_text, chunk_size=800, overlap=150)
            if not self.chunks:
                return "✗ 載入失敗：切段後沒有有效內容"

            self._build_chroma_index()
            self._build_tfidf_index()
            self._build_child_index()

            return (
                f"✓ 成功載入 {self.source_name}\n"
                f"類型：{suffix.upper().lstrip('.')} · {stats}\n"
                f"{len(self.chunks)} 個主片段 · ChromaDB Session：{self.session_id}"
            )
        except Exception as exc:
            return f"✗ 載入失敗：{type(exc).__name__}: {exc}"

    # ── 文字擷取 ─────────────────────────────────────────
    def _extract_pdf(self, path: Path) -> tuple[str, str]:
        reader = PdfReader(str(path))
        parts = []
        for idx, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"\n[PDF 第 {idx} 頁]\n{text}")
        return "\n".join(parts), f"{len(reader.pages)} 頁"

    def _extract_docx(self, path: Path) -> tuple[str, str]:
        doc = Document(str(path))
        blocks: list[str] = []
        para_count = table_count = 0

        for child in doc.element.body.iterchildren():
            if isinstance(child, CT_P):
                text = Paragraph(child, doc).text.strip()
                if text:
                    para_count += 1
                    blocks.append(text)
            elif isinstance(child, CT_Tbl):
                table_count += 1
                tbl_text = self._table_to_text(Table(child, doc))
                if tbl_text.strip():
                    blocks.append(f"\n[DOCX 表格 {table_count}]\n{tbl_text}")

        return "\n\n".join(blocks), f"{para_count} 段落 / {table_count} 表格"

    def _table_to_text(self, table: Table) -> str:
        rows = []
        for row in table.rows:
            cells = [re.sub(r"\s+", " ", c.text).strip() for c in row.cells if c.text.strip()]
            if cells:
                rows.append(" | ".join(cells))
        return "\n".join(rows)

    def _split(self, text: str, chunk_size: int, overlap: int) -> list[str]:
        clean = re.sub(r"\s+", " ", text).strip()
        step = max(1, chunk_size - overlap)
        return [
            c for start in range(0, len(clean), step)
            if (c := clean[start: start + chunk_size].strip())
        ]

    # ── Index 建立 ───────────────────────────────────────
    def _encode(self, texts: list[str]) -> list[list[float]]:
        return (
            self.embedding_model
            .encode(texts, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
            .astype("float32")
            .tolist()
        )

    def _build_chroma_index(self) -> None:
        sid = self.session_id
        ids = [f"{sid}_chunk_{i:05d}" for i in range(len(self.chunks))]
        metas = [
            {"session_id": sid, "source": self.source_name,
             "file_type": self.file_type, "chunk_index": i}
            for i in range(len(self.chunks))
        ]
        self.collection.add(ids=ids, documents=self.chunks,
                            metadatas=metas, embeddings=self._encode(self.chunks))

    def _build_tfidf_index(self) -> None:
        self.tfidf_vectorizer = TfidfVectorizer(analyzer="char", ngram_range=(2, 4), max_features=3000)
        self.tfidf_matrix = self.tfidf_vectorizer.fit_transform(self.chunks)

    def _build_child_index(self) -> None:
        sid = self.session_id
        child_docs, child_ids, child_metas = [], [], []
        for pidx, parent in enumerate(self.chunks):
            for cidx, child in enumerate(self._split(parent, chunk_size=300, overlap=50)):
                child_docs.append(child)
                child_ids.append(f"{sid}_parent_{pidx:05d}_child_{cidx:03d}")
                child_metas.append({"session_id": sid, "source": self.source_name,
                                    "file_type": self.file_type,
                                    "parent_index": pidx, "child_index": cidx})
        self.child_chunks = child_docs
        if child_docs:
            self.child_collection.add(ids=child_ids, documents=child_docs,
                                       metadatas=child_metas, embeddings=self._encode(child_docs))

    # ── 工具函式 ─────────────────────────────────────────
    def _where(self) -> dict[str, str]:
        return {"session_id": self.session_id or ""}

    def _chroma_search(self, query: str, k: int, child: bool = False) -> list[dict[str, Any]]:
        if not self.session_id:
            return []
        col = self.child_collection if child else self.collection
        results = col.query(
            query_embeddings=self._encode([query]),
            n_results=max(1, k),
            where=self._where(),
            include=["documents", "metadatas", "distances"],
        )
        docs = results.get("documents", [[]])[0] or []
        metas = results.get("metadatas", [[]])[0] or []
        dists = results.get("distances", [[]])[0] or []
        return [{"text": d, "metadata": m or {}, "distance": dist}
                for d, m, dist in zip(docs, metas, dists)]

    def _dedupe(self, chunks: list[str], k: int) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for c in chunks:
            key = c[:120]
            if key not in seen:
                seen.add(key)
                out.append(c)
            if len(out) >= k:
                break
        return out

    def _llm(self, prompt: str, max_tokens: int = 300, temperature: float = 0.3) -> str | None:
        if not self.client:
            return None
        try:
            r = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return r.choices[0].message.content
        except Exception:
            return None

    # ── 8 種策略 ──────────────────────────────────────────
    def s_semantic(self, query: str, k: int = 3) -> list[str]:
        return [r["text"] for r in self._chroma_search(query, k)]

    def s_tfidf(self, query: str, k: int = 3) -> list[str]:
        if self.tfidf_vectorizer is None or self.tfidf_matrix is None:
            return []
        qv = self.tfidf_vectorizer.transform([query])
        scores = (self.tfidf_matrix * qv.T).toarray().flatten()
        return [self.chunks[i] for i in scores.argsort()[-k:][::-1]]

    def s_hybrid(self, query: str, k: int = 3) -> list[str]:
        return self._dedupe(
            self.s_semantic(query, k * 2) + self.s_tfidf(query, k * 2), k
        )

    def s_rerank(self, query: str, k: int = 3) -> list[str]:
        candidates = self.s_semantic(query, k * 2)
        if not self.client:
            return candidates[:k]
        scored: list[tuple[str, float]] = []
        for chunk in candidates:
            prompt = (f"問題：{query}\n\n文本：{chunk[:500]}\n\n"
                      f"請只輸出 0 到 10 的相關度分數（僅數字）：")
            resp = self._llm(prompt, max_tokens=10, temperature=0)
            nums = re.findall(r"\d+(?:\.\d+)?", resp or "")
            scored.append((chunk, float(nums[0]) if nums else 0.0))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:k]]

    def s_multi_query(self, query: str, k: int = 3) -> list[str]:
        queries = [query]
        prompt = f"將以下問題改寫成 3 個角度不同的繁體中文問題，每行一題，不加編號：\n{query}"
        resp = self._llm(prompt, max_tokens=200, temperature=0.7)
        if resp:
            extras = [ln.strip("-• 1234567890.、 ") for ln in resp.splitlines() if ln.strip()]
            queries += extras[:3]
        chunks: list[str] = []
        for q in queries:
            chunks.extend(self.s_semantic(q, 2))
        return self._dedupe(chunks, k)

    def s_compress(self, query: str, k: int = 3) -> list[str]:
        chunks = self.s_semantic(query, k)
        if not self.client:
            return chunks
        compressed = []
        for chunk in chunks:
            prompt = (f"從以下文本中，提取與問題「{query}」最相關的 1-2 句，"
                      f"保留繁體中文，不要添加任何解釋：\n\n{chunk}")
            resp = self._llm(prompt, max_tokens=180, temperature=0)
            compressed.append((resp or "").strip() or chunk[:350])
        return compressed

    def s_parent_child(self, query: str, k: int = 3) -> list[str]:
        hits = self._chroma_search(query, k * 3, child=True)
        seen_parents: list[int] = []
        for h in hits:
            pidx = h.get("metadata", {}).get("parent_index")
            if isinstance(pidx, int) and pidx not in seen_parents:
                seen_parents.append(pidx)
            if len(seen_parents) >= k:
                break
        return [self.chunks[i] for i in seen_parents if 0 <= i < len(self.chunks)]

    def s_hyde(self, query: str, k: int = 3) -> list[str]:
        prompt = f"請對以下問題給出一段假設性簡短答案（繁體中文）：\n{query}"
        hypo = self._llm(prompt, max_tokens=250, temperature=0.7) or query
        return self.s_semantic(hypo, k)

    # ── 策略路由 ──────────────────────────────────────────
    _FN = {
        "semantic":     s_semantic,
        "tfidf":        s_tfidf,
        "hybrid":       s_hybrid,
        "rerank":       s_rerank,
        "multi_query":  s_multi_query,
        "compress":     s_compress,
        "parent_child": s_parent_child,
        "hyde":         s_hyde,
    }

    def generate_answer(self, query: str, strategy_key: str, top_k: int):
        if not self.chunks:
            return "請先上傳並載入 PDF 或 DOCX 文件。", ""
        if not query.strip():
            return "請輸入問題。", ""

        fn = self._FN.get(strategy_key, self.s_semantic)
        chunks = fn(self, query, int(top_k))
        context = "\n\n—\n\n".join(chunks)

        strategy_label = self.STRATEGY_MAP.get(strategy_key, strategy_key)
        source_preview = (
            f"文件：{self.source_name}\n"
            f"策略：{strategy_label} · 片段數：{len(chunks)}\n"
            f"ChromaDB Session：{self.session_id}\n\n"
            f"{'─' * 56}\n\n{context}"
        )

        if not self.client:
            return (
                "⚠ 尚未設定 Groq API Key。\n"
                "請在左欄「Step 00」輸入您的 Groq API Key 並點擊「套用」後再提問。\n\n"
                "（檢索已完成，可在下方「查看檢索到的文本片段」確認結果）",
                source_preview,
            )

        prompt = f"""請根據以下上下文回答問題。若上下文無相關資訊，請明確說明無法從文件回答，不要自行編造。

上下文：
{context}

問題：{query}

請用繁體中文詳細回答，並以條列方式整理重點："""

        try:
            r = self.client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": "你是專業的文件分析與 RAG 問答助手。"},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1024,
                temperature=0.3,
            )
            return r.choices[0].message.content, source_preview
        except Exception as exc:
            return f"生成失敗：{type(exc).__name__}: {exc}", source_preview


# ══════════════════════════════════════════════════════════
#  Gradio UI
# ══════════════════════════════════════════════════════════
STRATEGY_INFO = [
    ("semantic",     "語意搜尋",   "ChromaDB 向量相似度，最通用",         "🔍"),
    ("tfidf",        "TF-IDF",     "字元 n-gram 關鍵詞統計",              "📊"),
    ("hybrid",       "混合搜尋",   "語意 + TF-IDF 結果合併去重",          "⚡"),
    ("rerank",       "重新排序",   "LLM 對候選片段二次評分",              "🎯"),
    ("multi_query",  "多查詢擴展", "自動生成多角度問題聯合搜尋",          "🔄"),
    ("compress",     "上下文壓縮", "LLM 提取最相關句子精簡上下文",        "✂️"),
    ("parent_child", "父子文檔",   "小片段定位 → 回傳對應大片段",        "📂"),
    ("hyde",         "HyDE",       "先生成假設答案再語意搜尋",            "💡"),
]

CSS = """
body, .gradio-container { background:#f5f4f1 !important; }

#hdr {
    background:#fff;
    border:1px solid #e5e0d8;
    border-radius:14px;
    padding:28px 36px;
    margin-bottom:20px;
    border-top: 4px solid #2d6a4f;
}
.hdr-eyebrow { font-size:11px; letter-spacing:2.5px; color:#2d6a4f; text-transform:uppercase; margin-bottom:6px; }
.hdr-title   { font-size:26px; font-weight:700; color:#1a1714; margin:0 0 6px; }
.hdr-sub     { font-size:14px; color:#6b5e56; }
.pill { display:inline-block; margin:10px 5px 0 0; padding:3px 10px; border-radius:16px;
        font-size:11px; background:#e8f4f0; color:#2d6a4f; border:1px solid rgba(45,106,79,.2); }
.pill-amber { background:#fdf4e3; color:#b87a1a; border-color:rgba(184,122,26,.25); }

/* API Key 區塊 */
#apikey-box {
    background: #fffbf2;
    border: 1.5px solid #f0c96a;
    border-radius: 10px;
    padding: 12px 14px;
    margin-bottom: 8px;
}

.strat-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:10px 0 16px; }
.strat-card {
    background:#fff;
    border:1.5px solid #e5e0d8;
    border-radius:10px;
    padding:10px 12px;
    cursor:pointer;
    transition:border-color .15s, box-shadow .15s;
    text-align:left;
    width:100%;
}
.strat-card:hover { border-color:#2d6a4f; box-shadow:0 2px 8px rgba(45,106,79,.12); }
.strat-card.active { border-color:#2d6a4f; background:#f0f9f5; box-shadow:0 2px 10px rgba(45,106,79,.18); }
.strat-icon  { font-size:20px; margin-bottom:4px; }
.strat-name  { font-size:13px; font-weight:700; color:#1a1714; margin:0 0 2px; }
.strat-desc  { font-size:11px; color:#7a6e67; line-height:1.4; }

.sec-label { font-size:11px; letter-spacing:1.5px; text-transform:uppercase;
             color:#7a6e67; font-weight:700; margin:16px 0 8px; }
.card-box { background:#fff !important; border:1px solid #e5e0d8 !important;
            border-radius:12px !important; padding:16px !important; }
#ask-btn { background:#2d6a4f !important; color:#fff !important; border:0 !important; border-radius:8px !important; }
#apply-key-btn { background:#b87a1a !important; color:#fff !important; border:0 !important; border-radius:8px !important; }
"""

HEADER_HTML = """
<div id="hdr">
  <div class="hdr-eyebrow">Intelligent Document Analysis · v2</div>
  <div class="hdr-title">多策略 RAG 文件問答系統</div>
  <div class="hdr-sub">支援 PDF / DOCX 上傳，採用 ChromaDB 持久化向量資料庫與 8 種 RAG 檢索策略</div>
  <div>
    <span class="pill">▸ Groq API</span>
    <span class="pill">▸ llama-3.1-8b-instant</span>
    <span class="pill pill-amber">▸ ChromaDB</span>
    <span class="pill pill-amber">▸ PDF / DOCX</span>
    <span class="pill">▸ SentenceTransformers</span>
  </div>
</div>
"""


def build_strategy_menu(selected: str = "semantic") -> str:
    cards = []
    for key, name, desc, icon in STRATEGY_INFO:
        active_cls = "active" if key == selected else ""
        cards.append(
            f"""<button class="strat-card {active_cls}" onclick="selectStrategy('{key}', this)" type="button">
  <div class="strat-icon">{icon}</div>
  <div class="strat-name">{name}</div>
  <div class="strat-desc">{desc}</div>
</button>"""
        )
    return f'<div class="strat-grid">{"".join(cards)}</div>'


STRATEGY_MENU_JS = """
<script>
function selectStrategy(key, el) {
    document.querySelectorAll('.strat-card').forEach(c => c.classList.remove('active'));
    el.classList.add('active');
    const inp = document.getElementById('strategy-hidden');
    if (inp) { inp.value = key; inp.dispatchEvent(new Event('input')); }
}
</script>
"""

EXAMPLE_QS = [
    ["這份文件的主要內容是什麼？"],
    ["文件中提到哪些重要概念或定義？"],
    ["有哪些關鍵數據、統計資料或案例？"],
    ["文件的結論或建議是什麼？"],
    ["文件提及哪些潛在風險或挑戰？"],
]


def create_interface():
    env_key = os.getenv("GROQ_API_KEY", "").strip()
    rag = MultiStrategyRAG(chroma_path="./chroma_db")
    if env_key:
        rag.set_api_key(env_key)

    current_strategy = {"key": "semantic"}

    def apply_api_key(api_key: str):
        key = (api_key or "").strip()
        rag.set_api_key(key)
        if key:
            masked = key[:8] + "****" + key[-4:] if len(key) > 12 else "****"
            return f"✓ API Key 已套用（{masked}）"
        return "⚠ API Key 已清除，無法呼叫 LLM"

    def upload_document(file):
        if file is None:
            return "⚠ 請選擇 PDF 或 DOCX 檔案"
        return rag.load_document(file.name)

    def set_strategy(key: str):
        current_strategy["key"] = key
        return f"✓ 已選擇策略：{dict((k, n) for k, n, *_ in STRATEGY_INFO).get(key, key)}"

    def ask(query, top_k):
        return rag.generate_answer(query, current_strategy["key"], int(top_k))

    with gr.Blocks(
        title="多策略 RAG 文件問答 v2",
        css=CSS,
        theme=gr.themes.Base(
            primary_hue=gr.themes.colors.green,
            neutral_hue=gr.themes.colors.stone,
        ),
    ) as demo:
        gr.HTML(HEADER_HTML)

        with gr.Row(equal_height=False):
            # ── 左欄 ──────────────────────────────────
            with gr.Column(scale=1, min_width=320, elem_classes="card-box"):

                gr.HTML("<div class='sec-label'>Step 00 · Groq API Key</div>")
                with gr.Group(elem_id="apikey-box"):
                    api_key_input = gr.Textbox(
                        label="",
                        placeholder="gsk_xxxxxxxxxxxxxxxxxxxxxxxx",
                        value=env_key,
                        type="password",
                        lines=1,
                        show_label=False,
                    )
                    apply_key_btn = gr.Button(
                        "套用 API Key", size="sm", elem_id="apply-key-btn"
                    )
                    api_key_status = gr.Textbox(
                        value="✓ API Key 已從環境變數載入" if env_key else "⚠ 尚未設定 API Key",
                        interactive=False,
                        lines=1,
                        label="",
                        show_label=False,
                    )

                gr.HTML("<div class='sec-label'>Step 01 · 上傳文件</div>")
                file_input = gr.File(label="PDF / DOCX", file_types=[".pdf", ".docx"])
                load_btn = gr.Button("↑ 載入文件")
                status = gr.Textbox(label="狀態", interactive=False, lines=3)

                gr.HTML("<div class='sec-label'>Step 02 · 選擇 RAG 策略</div>")
                gr.HTML(build_strategy_menu("semantic"))
                strategy_input = gr.Textbox(
                    value="semantic",
                    elem_id="strategy-hidden",
                    label="",
                    visible=False,
                )
                strategy_status = gr.Textbox(
                    value="✓ 已選擇策略：語意搜尋",
                    interactive=False,
                    lines=1,
                    label="目前策略",
                )
                gr.HTML(STRATEGY_MENU_JS)

                gr.HTML("<div class='sec-label'>Step 03 · 搜尋參數</div>")
                topk = gr.Slider(minimum=1, maximum=10, value=3, step=1, label="Top-K 片段數量")

            # ── 右欄：問答 ────────────────────────────
            with gr.Column(scale=2, elem_classes="card-box"):
                gr.HTML("<div class='sec-label'>Step 04 · 輸入問題</div>")
                qin = gr.Textbox(
                    label="",
                    placeholder="例如：這份文件的核心論點是什麼？",
                    lines=4,
                )
                ask_btn = gr.Button("提問", variant="primary", size="lg", elem_id="ask-btn")

                gr.HTML("<div class='sec-label'>AI 回答</div>")
                ans = gr.Textbox(label="", lines=12, interactive=False)

                with gr.Accordion("▸ 查看檢索到的文本片段", open=False):
                    src = gr.Textbox(label="", lines=10, interactive=False)

                gr.Examples(examples=EXAMPLE_QS, inputs=qin, label="範例問題")

        apply_key_btn.click(fn=apply_api_key, inputs=[api_key_input], outputs=[api_key_status])
        api_key_input.submit(fn=apply_api_key, inputs=[api_key_input], outputs=[api_key_status])
        load_btn.click(fn=upload_document, inputs=[file_input], outputs=[status])
        strategy_input.change(fn=set_strategy, inputs=[strategy_input], outputs=[strategy_status])
        ask_btn.click(fn=ask, inputs=[qin, topk], outputs=[ans, src])
        qin.submit(fn=ask, inputs=[qin, topk], outputs=[ans, src])

    return demo


if __name__ == "__main__":
    demo = create_interface()
    demo.launch(share=False, server_name="0.0.0.0")
