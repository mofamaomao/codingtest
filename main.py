import os
import json
import re
from pathlib import Path
from typing import Optional

import anthropic
import numpy as np
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

app = FastAPI(title="On-Call Assistant")


@app.get("/")
async def root():
    return RedirectResponse(url="/v1")

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# ──────────────────────────────────────────────
# Document store
# ──────────────────────────────────────────────
documents: dict[str, dict] = {}  # fname -> {text, html}
tfidf_vectorizer: Optional[TfidfVectorizer] = None
tfidf_matrix = None
tfidf_fnames: list[str] = []


def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def rebuild_tfidf():
    global tfidf_vectorizer, tfidf_matrix, tfidf_fnames
    if not documents:
        return
    tfidf_fnames = list(documents.keys())
    corpus = [documents[f]["text"] for f in tfidf_fnames]
    # character n-grams work well for Chinese without a tokenizer
    tfidf_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    tfidf_matrix = tfidf_vectorizer.fit_transform(corpus)


def load_all_documents():
    for path in sorted(DATA_DIR.glob("*.html")):
        html = path.read_text(encoding="utf-8")
        documents[path.name] = {"text": extract_text(html), "html": html}
    rebuild_tfidf()


load_all_documents()


def get_snippet(text: str, query: str, window: int = 120) -> str:
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[:window]
    start = max(0, idx - 40)
    return "…" + text[start : start + window] + "…"


# ──────────────────────────────────────────────
# Phase 1 – Keyword Search
# ──────────────────────────────────────────────
@app.post("/v1/documents")
async def upload_document(file: UploadFile = File(...)):
    if not file.filename.endswith(".html"):
        raise HTTPException(400, "Only .html files are supported")
    html = (await file.read()).decode("utf-8")
    fname = file.filename
    documents[fname] = {"text": extract_text(html), "html": html}
    (DATA_DIR / fname).write_text(html, encoding="utf-8")
    rebuild_tfidf()
    return {"message": f"Indexed {fname}", "total": len(documents)}


@app.get("/v1/search")
async def keyword_search(q: str):
    if not q:
        raise HTTPException(400, "Query cannot be empty")
 
    # 新增：防止超长输入
    if len(q) > 500:
        raise HTTPException(400, "Query too long (max 500 characters)")
 
    results = []
    q_lower = q.lower()
    for fname, doc in documents.items():
        count = doc["text"].lower().count(q_lower)
        if count > 0:
            results.append({
                "file": fname,
                "score": count,
                "snippet": get_snippet(doc["text"], q),
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return {"query": q, "total": len(results), "results": results}
 
 
# ============================================================
# 辅助函数（保持不变，仅供参考）
# ============================================================
 
def extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)
 
def get_snippet(text: str, query: str, window: int = 120) -> str:
    idx = text.lower().find(query.lower())
    if idx == -1:
        return text[:window]
    start = max(0, idx - 40)
    return "…" + text[start: start + window] + "…"
 
def rebuild_tfidf():
    global tfidf_vectorizer, tfidf_matrix, tfidf_fnames
    if not documents:
        return
    tfidf_fnames = list(documents.keys())
    corpus = [documents[f]["text"] for f in tfidf_fnames]
    tfidf_vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4))
    tfidf_matrix = tfidf_vectorizer.fit_transform(corpus)
 
def load_all_documents():
    for path in sorted(DATA_DIR.glob("*.html")):
        html = path.read_text(encoding="utf-8")
        documents[path.name] = {"text": extract_text(html), "html": html}
    rebuild_tfidf()
 
def _serialise_messages(messages: list) -> list:
    result = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            serialised = []
            for block in content:
                if hasattr(block, "type"):
                    d: dict = {"type": block.type}
                    if block.type == "text":
                        d["text"] = block.text
                    elif block.type == "tool_use":
                        d["id"] = block.id
                        d["name"] = block.name
                        d["input"] = dict(block.input)
                    serialised.append(d)
                elif isinstance(block, dict):
                    serialised.append(block)
            result.append({"role": msg["role"], "content": serialised})
        else:
            result.append({"role": msg["role"], "content": content})
    return result
 
def _extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content) if content else ""
    return "".join(
        block.text if hasattr(block, "text") else block.get("text", "")
        for block in content
        if (hasattr(block, "type") and block.type == "text")
        or (isinstance(block, dict) and block.get("type") == "text")
    )
 
SYSTEM_PROMPT = """You are an on-call assistant..."""  # 保持原样
 
READ_FILE_TOOL = {
    "name": "readFile",
    "description": "Read an SOP document from the data directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fname": {
                "type": "string",
                "description": "Filename to read, e.g. sop-001.html. No path prefixes or wildcards.",
            }
        },
        "required": ["fname"],
    },
}


@app.get("/v1", response_class=HTMLResponse)
async def v1_ui():
    return (Path("static/v1.html")).read_text(encoding="utf-8")


# ──────────────────────────────────────────────
# Phase 2 – Semantic Search (char-ngram TF-IDF)
# ──────────────────────────────────────────────
@app.get("/v2/search")
async def semantic_search(q: str):
    if not q:
        raise HTTPException(400, "Query cannot be empty")
    if tfidf_vectorizer is None:
        raise HTTPException(503, "No documents indexed yet")
    q_vec = tfidf_vectorizer.transform([q])
    sims = cosine_similarity(q_vec, tfidf_matrix)[0]
    top_idx = np.argsort(sims)[::-1][:10]
    results = []
    for idx in top_idx:
        score = float(sims[idx])
        if score < 0.01:
            break
        fname = tfidf_fnames[idx]
        results.append({
            "file": fname,
            "score": round(score, 4),
            "snippet": documents[fname]["text"][:150] + "…",
        })
    return {"query": q, "total": len(results), "results": results}


@app.get("/v2", response_class=HTMLResponse)
async def v2_ui():
    return (Path("static/v2.html")).read_text(encoding="utf-8")


# ──────────────────────────────────────────────
# Phase 3 – On-Call Agent
# ──────────────────────────────────────────────
def _make_anthropic_client() -> Optional[anthropic.Anthropic]:
    """
    返回 None 而不是抛出异常。
    调用方检查 None 并给用户友好提示，服务本身能正常启动。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        return anthropic.Anthropic(api_key=api_key)
 
    token_file = os.environ.get("CLAUDE_SESSION_INGRESS_TOKEN_FILE")
    if token_file and os.path.exists(token_file):
        auth_token = Path(token_file).read_text().strip()
        return anthropic.Anthropic(auth_token=auth_token)
 
    return None  # ← 改动：返回 None 而不是 raise RuntimeError
 
 
# 模块级初始化：捕获异常，降级为 None
try:
    client = _make_anthropic_client()
except Exception:
    client = None

READ_FILE_TOOL = {
    "name": "readFile",
    "description": "Read an SOP document from the data directory. Use this to look up on-call procedures.",
    "input_schema": {
        "type": "object",
        "properties": {
            "fname": {
                "type": "string",
                "description": "Filename to read, e.g. sop-001.html. No path prefixes or wildcards.",
            }
        },
        "required": ["fname"],
    },
}

SYSTEM_PROMPT = """You are an on-call assistant. You have access to a readFile tool that can read SOP documents from the data/ directory.

Available files: sop-001.html through sop-010.html, covering:
- sop-001: Backend services (OOM, timeouts, degradation)
- sop-002: Database DBA (replication, slow queries, connections)
- sop-003: Frontend (white screens, CDN, compatibility)
- sop-004: SRE (Kubernetes, monitoring, capacity)
- sop-005: Security (incident classification, intrusion)
- sop-006: Data platform (pipeline, ETL, Spark)
- sop-007: Mobile (crashes, hot fixes, push)
- sop-008: AI/ML (inference, recommendations, GPU)
- sop-009: QA (environment, automation, releases)
- sop-010: Network/CDN (node failures, DNS, DDoS)

When answering a question, always read the relevant SOP file(s) first. Show your tool calls clearly. Answer in the same language as the user's question."""


ALLOWED_FNAMES = {f"sop-{str(i).zfill(3)}.html" for i in range(1, 11)}
 
def do_read_file(fname: str) -> str:
    """
    只允许读取白名单内的 SOP 文件。
    拒绝路径穿越、通配符、不在白名单的文件名。
    返回字符串（成功内容 or 错误描述），永远不抛异常。
    """
    # 拒绝路径穿越
    if ".." in fname or "/" in fname or "\\" in fname:
        return "Error: path traversal not allowed"
 
    # 拒绝通配符
    if "*" in fname or "?" in fname:
        return "Error: wildcards not allowed"
 
    # 只取文件名部分（防御性）
    safe_fname = Path(fname).name
 
    # 白名单校验
    if safe_fname not in ALLOWED_FNAMES:
        return f"Error: '{safe_fname}' is not an allowed SOP file"
 
    path = DATA_DIR / safe_fname
    if not path.exists():
        return f"Error: {safe_fname} not found in data directory"
 
    try:
        html = path.read_text(encoding="utf-8")
        return extract_text(html)
    except Exception as e:
        return f"Error reading file: {str(e)}"


def _serialise_messages(messages: list) -> list:
    result = []
    for msg in messages:
        content = msg["content"]
        if isinstance(content, list):
            serialised = []
            for block in content:
                if hasattr(block, "type"):
                    d = {"type": block.type}
                    if block.type == "text":
                        d["text"] = block.text
                    elif block.type == "tool_use":
                        d["id"] = block.id
                        d["name"] = block.name
                        d["input"] = dict(block.input)
                    serialised.append(d)
                elif isinstance(block, dict):
                    serialised.append(block)
            result.append({"role": msg["role"], "content": serialised})
        else:
            result.append({"role": msg["role"], "content": content})
    return result


def _extract_text(content) -> str:
    if not isinstance(content, list):
        return str(content) if content else ""
    return "".join(
        block.text if hasattr(block, "text") else block.get("text", "")
        for block in content
        if (hasattr(block, "type") and block.type == "text")
        or (isinstance(block, dict) and block.get("type") == "text")
    )


MOCK_REPLY = (
    "⚠️ [Mock Mode] Anthropic API 不可用（无 API Key 或余额不足）。\n\n"
    "正常情况下我会读取 SOP 文件并给出处理建议。\n"
    "示例回答：当服务出现 OOM 时，请参考 sop-001 第3节，"
    "先执行内存快照，再重启服务，最后排查内存泄漏根因。"
)
 
 
@app.post("/v3/chat")
async def agent_chat(body: dict):
    user_message = body.get("message", "").strip()
    history = body.get("history", [])
 
    if not user_message:
        raise HTTPException(400, "message cannot be empty")
 
    # 修复：client 为 None 时返回 Mock，而不是 503 崩掉
    if client is None:
        return {
            "reply": MOCK_REPLY,
            "tool_calls": [],
            "history": history + [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": MOCK_REPLY},
            ],
            "mode": "mock",  # 让前端知道当前是 mock 模式
        }
 
    messages = list(history) + [{"role": "user", "content": user_message}]
    tool_calls_log = []
    last_assistant_content = None
 
    try:
        for _ in range(5):
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=[READ_FILE_TOOL],
                messages=messages,
            )
 
            assistant_content = response.content
            last_assistant_content = assistant_content
            messages.append({"role": "assistant", "content": assistant_content})
 
            if response.stop_reason == "end_turn":
                break
 
            if response.stop_reason == "tool_use":
                tool_results = []
                for block in assistant_content:
                    if block.type == "tool_use":
                        result = do_read_file(block.input.get("fname", ""))
                        tool_calls_log.append({
                            "tool": block.name,
                            "input": dict(block.input),
                            "result_preview": result[:200],
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })
                messages.append({"role": "user", "content": tool_results})
            else:
                break
 
    except anthropic.AuthenticationError:
        # API Key 无效时，优雅降级到 mock
        return {
            "reply": MOCK_REPLY,
            "tool_calls": [],
            "history": _serialise_messages(messages),
            "mode": "mock",
        }
    except anthropic.RateLimitError:
        raise HTTPException(429, "API rate limit exceeded, please retry later")
    except Exception as e:
        raise HTTPException(500, f"Agent error: {str(e)}")
 
    # 修复：final_text 为空时给出明确提示，不让前端拿到空字符串
    final_text = _extract_text(last_assistant_content)
    if not final_text:
        final_text = "抱歉，未能生成有效回答，请重试。"
 
    return {
        "reply": final_text,
        "tool_calls": tool_calls_log,
        "history": _serialise_messages(messages),
        "mode": "live",
    }

@app.get("/v3", response_class=HTMLResponse)
async def v3_ui():
    return (Path("static/v3.html")).read_text(encoding="utf-8")
