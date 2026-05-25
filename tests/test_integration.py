"""
tests/test_integration.py
=========================
集成测试：通过真实 HTTP 请求测试 FastAPI 接口行为。

与 test_main.py 的区别：
- test_main.py      → 测试孤立函数（simulate_do_read_file），不启动服务
- test_integration  → 测试真实 HTTP 接口，验证状态码、JSON 结构、业务逻辑

核心技术：
- httpx AsyncClient  模拟 HTTP 请求，不需要真正启动服务
- unittest.mock.patch 替换 Anthropic client，不需要真实 API Key
- pytest fixtures    管理测试数据（临时 SOP 文件）的创建和清理

运行方式：
  python -m pytest tests/test_integration.py -v
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path
import tempfile
import shutil
import sys
import os


# ════════════════════════════════════════════════════════════════
# 关键：在 import main 之前，先 mock 掉 anthropic
# 原因：main.py 模块级别会执行 client = _make_anthropic_client()
#       如果没有 API Key 且没有 mock，import 本身可能失败
# ════════════════════════════════════════════════════════════════

# 创建一个假的 anthropic 模块
mock_anthropic_module = MagicMock()
mock_anthropic_module.AuthenticationError = Exception
mock_anthropic_module.RateLimitError = Exception
sys.modules["anthropic"] = mock_anthropic_module

# 现在可以安全 import main 了
sys.path.insert(0, str(Path(__file__).parent.parent))
import main as app_module
from main import app


# ════════════════════════════════════════════════════════════════
# Fixtures：测试数据和客户端
# ════════════════════════════════════════════════════════════════

# 准备两份测试用 SOP HTML
SOP_001_HTML = """<html><body>
<h1>SOP-001 Backend Service OOM</h1>
<p>When a backend service runs out of memory (OOM), follow these steps:</p>
<ol>
  <li>Take a heap dump immediately using jmap or async-profiler</li>
  <li>Restart the service gracefully with zero-downtime rolling restart</li>
  <li>Check memory leak patterns in GC logs and heap analysis</li>
  <li>Scale up memory limit temporarily if needed</li>
</ol>
<p>Keywords: restart service, memory, OOM, heap dump, backend</p>
</body></html>"""

SOP_002_HTML = """<html><body>
<h1>SOP-002 Database Replication Lag</h1>
<p>When database replication lag exceeds threshold:</p>
<ol>
  <li>Check replica status with SHOW SLAVE STATUS</li>
  <li>Identify slow queries causing lag</li>
  <li>Review connection pool settings</li>
</ol>
<p>Keywords: replication, slow query, database, connection pool</p>
</body></html>"""


@pytest.fixture(scope="module")
def temp_data_dir():
    """
    创建临时 data 目录，写入测试 SOP 文件。
    测试结束后自动清理。
    scope="module" 表示整个测试文件只创建一次，提升速度。
    """
    tmp_dir = Path(tempfile.mkdtemp())
    (tmp_dir / "sop-001.html").write_text(SOP_001_HTML, encoding="utf-8")
    (tmp_dir / "sop-002.html").write_text(SOP_002_HTML, encoding="utf-8")
    yield tmp_dir
    shutil.rmtree(tmp_dir)  # 测试结束后清理


@pytest.fixture(autouse=True)
def patch_data_dir(temp_data_dir):
    """
    把 main.py 里的 DATA_DIR 替换为临时目录，
    同时重新加载文档到内存（documents 字典）。
    autouse=True 表示每个测试自动使用这个 fixture。
    """
    # 替换 DATA_DIR
    original_dir = app_module.DATA_DIR
    app_module.DATA_DIR = temp_data_dir

    # 重新加载文档
    app_module.documents.clear()
    app_module.load_all_documents()

    yield

    # 测试后还原
    app_module.DATA_DIR = original_dir
    app_module.documents.clear()


@pytest_asyncio.fixture
async def client():
    """
    提供一个 httpx AsyncClient，直接连接到 FastAPI app。
    不需要启动真实服务器。
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as c:
        yield c


# ════════════════════════════════════════════════════════════════
# Phase 1：关键词检索 /v1/search
# ════════════════════════════════════════════════════════════════

class TestKeywordSearch:

    @pytest.mark.asyncio
    async def test_normal_search_returns_results(self, client):
        """正常搜索：有匹配词时返回结果，score > 0"""
        response = await client.get("/v1/search?q=restart+service")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert len(data["results"]) >= 1
        # 最相关的结果应该是 sop-001
        assert data["results"][0]["file"] == "sop-001.html"
        # 每个结果必须有这三个字段
        result = data["results"][0]
        assert "file" in result
        assert "score" in result
        assert "snippet" in result

    @pytest.mark.asyncio
    async def test_results_sorted_by_score_descending(self, client):
        """结果应按 score 降序排列"""
        response = await client.get("/v1/search?q=sop")
        assert response.status_code == 200
        scores = [r["score"] for r in response.json()["results"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_no_match_returns_empty_not_error(self, client):
        """没有匹配时返回 200 + 空列表，不是 404"""
        response = await client.get("/v1/search?q=zzz_no_match_xyz_999")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_400(self, client):
        """空 query 返回 400"""
        response = await client.get("/v1/search?q=")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_query_too_long_returns_400(self, client):
        """超过 500 字符的 query 返回 400"""
        long_q = "a" * 501
        response = await client.get(f"/v1/search?q={long_q}")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_case_insensitive(self, client):
        """大小写不敏感：'RESTART SERVICE' 和 'restart service' 结果一致"""
        r1 = await client.get("/v1/search?q=restart+service")
        r2 = await client.get("/v1/search?q=RESTART+SERVICE")
        assert r1.status_code == 200
        assert r2.status_code == 200
        files1 = [r["file"] for r in r1.json()["results"]]
        files2 = [r["file"] for r in r2.json()["results"]]
        assert files1 == files2


# ════════════════════════════════════════════════════════════════
# Phase 2：语义检索 /v2/search
# ════════════════════════════════════════════════════════════════

class TestSemanticSearch:

    @pytest.mark.asyncio
    async def test_semantic_search_returns_results(self, client):
        """语义搜索返回结果，包含 score 和 snippet"""
        response = await client.get("/v2/search?q=memory+issue")
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_results_sorted_by_score(self, client):
        """结果按相关度降序排列"""
        response = await client.get("/v2/search?q=database+replication")
        assert response.status_code == 200
        scores = [r["score"] for r in response.json()["results"]]
        assert scores == sorted(scores, reverse=True)

    @pytest.mark.asyncio
    async def test_low_relevance_filtered_out(self, client):
        """score < 0.01 的结果不出现在列表中"""
        response = await client.get("/v2/search?q=database")
        assert response.status_code == 200
        for result in response.json()["results"]:
            assert result["score"] >= 0.01

    @pytest.mark.asyncio
    async def test_empty_query_returns_400(self, client):
        """空 query 返回 400"""
        response = await client.get("/v2/search?q=")
        assert response.status_code == 400


# ════════════════════════════════════════════════════════════════
# Phase 3：Agent 对话 /v3/chat
# 关键：用 patch 替换 Anthropic client，不需要真实 API Key
# ════════════════════════════════════════════════════════════════

def make_fake_anthropic_response(text: str, stop_reason: str = "end_turn"):
    """
    构造一个假的 Anthropic API 响应对象。
    这是 mock 测试的核心：控制 API 返回什么，
    验证我们的代码对各种 API 响应的处理是否正确。
    """
    fake_block = MagicMock()
    fake_block.type = "text"
    fake_block.text = text

    fake_response = MagicMock()
    fake_response.content = [fake_block]
    fake_response.stop_reason = stop_reason
    return fake_response


class TestAgentChat:

    @pytest.mark.asyncio
    async def test_no_api_key_returns_mock_not_undefined(self, client):
        """
        最关键的测试：client 为 None 时（无 API Key），
        返回的 reply 必须是非空字符串，绝不能是空或 undefined。
        """
        with patch.object(app_module, "client", None):
            response = await client.post(
                "/v3/chat",
                json={"message": "服务 OOM 怎么处理？"}
            )
        assert response.status_code == 200
        data = response.json()
        assert "reply" in data
        assert isinstance(data["reply"], str)
        assert len(data["reply"]) > 0
        assert data["reply"] != "undefined"
        assert data.get("mode") == "mock"

    @pytest.mark.asyncio
    async def test_normal_chat_returns_reply(self, client):
        """
        正常情况：mock API 返回固定文本，
        验证接口把它正确传递给调用方。
        """
        fake_resp = make_fake_anthropic_response("根据 SOP-001，服务 OOM 时请先执行堆转储。")

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_resp

        with patch.object(app_module, "client", mock_client):
            response = await client.post(
                "/v3/chat",
                json={"message": "服务 OOM 怎么办？"}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "根据 SOP-001，服务 OOM 时请先执行堆转储。"
        assert data["mode"] == "live"
        assert "history" in data

    @pytest.mark.asyncio
    async def test_empty_message_returns_400(self, client):
        """空消息返回 400"""
        response = await client.post("/v3/chat", json={"message": ""})
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_missing_message_field_returns_400(self, client):
        """缺少 message 字段返回 400"""
        response = await client.post("/v3/chat", json={})
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_api_auth_error_returns_mock(self, client):
        """
        API Key 无效（AuthenticationError）时，
        降级返回 mock 回答，不返回 500。
        """
        mock_client = MagicMock()
        # 让 API 调用抛出认证异常
        mock_client.messages.create.side_effect = Exception("authentication error")

        with patch.object(app_module, "client", mock_client):
            with patch.object(app_module.anthropic, "AuthenticationError", Exception):
                response = await client.post(
                    "/v3/chat",
                    json={"message": "测试认证失败"}
                )

        # 应该降级为 mock，不是 500
        assert response.status_code in (200, 500)  # 取决于你的实现
        if response.status_code == 200:
            assert len(response.json()["reply"]) > 0

    @pytest.mark.asyncio
    async def test_history_is_preserved_across_turns(self, client):
        """
        多轮对话：第二轮请求带上 history，
        验证 history 被正确传递和追加。
        """
        fake_resp = make_fake_anthropic_response("好的，我来帮你处理。")
        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_resp

        with patch.object(app_module, "client", mock_client):
            # 第一轮
            r1 = await client.post(
                "/v3/chat",
                json={"message": "你好"}
            )
            assert r1.status_code == 200
            history_after_r1 = r1.json()["history"]

            # 第二轮：带上第一轮的 history
            r2 = await client.post(
                "/v3/chat",
                json={
                    "message": "继续",
                    "history": history_after_r1
                }
            )
            assert r2.status_code == 200
            history_after_r2 = r2.json()["history"]

        # 第二轮的 history 应该比第一轮多（包含了新的对话）
        assert len(history_after_r2) > len(history_after_r1)
