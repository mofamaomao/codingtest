# On-Call Assistant

一个帮助运维工程师快速检索 SOP 文档并获取 AI 辅助建议的工具，分三个阶段实现：关键词检索、语义检索、Agent 对话。

---

## 快速启动

### 环境要求

- Python 3.10+
- pip

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置环境变量

```bash
# 必须：Anthropic API Key（Phase 3 Agent 需要）
# 如果没有 API Key，Phase 3 会自动进入 Mock 模式，仍可演示
export ANTHROPIC_API_KEY=sk-ant-xxxxx
```

### 启动服务

```bash
uvicorn main:app --reload --port 8000
```

服务启动后访问：
- Phase 1 关键词检索：http://localhost:8000/v1
- Phase 2 语义检索：http://localhost:8000/v2
- Phase 3 Agent 对话：http://localhost:8000/v3

---

## 上传 SOP 文档

```bash
# 上传单个文档
curl -X POST http://localhost:8000/v1/documents \
  -F "file=@data/sop-001.html"

# 批量上传（把所有 html 文件放到 data/ 目录下即可，服务启动时自动加载）
```

---

## 运行测试

```bash
# 安装测试依赖
pip install pytest pytest-asyncio httpx pytest-cov

# 运行所有测试
pytest tests/test_main.py -v

# 只运行安全校验测试（不需要启动服务，最快）
pytest tests/test_main.py::TestDoReadFile -v

# 查看测试覆盖率
pytest tests/test_main.py --cov=main --cov-report=term-missing
```

### 期望测试结果

```
tests/test_main.py::TestDoReadFile::test_path_traversal_double_dot     PASSED
tests/test_main.py::TestDoReadFile::test_path_traversal_with_slash      PASSED
tests/test_main.py::TestDoReadFile::test_wildcard_rejected              PASSED
tests/test_main.py::TestDoReadFile::test_question_mark_wildcard_rejected PASSED
tests/test_main.py::TestDoReadFile::test_non_allowlisted_file_rejected  PASSED
tests/test_main.py::TestDoReadFile::test_allowed_file_but_not_exist     PASSED
tests/test_main.py::TestKeywordSearch::test_normal_search_returns_results PASSED
tests/test_main.py::TestKeywordSearch::test_empty_query_returns_400     PASSED
tests/test_main.py::TestKeywordSearch::test_query_too_long_returns_400  PASSED
tests/test_main.py::TestSemanticSearch::test_semantic_search_returns_results PASSED
tests/test_main.py::TestAgentChat::test_no_api_key_returns_mock_not_undefined PASSED
tests/test_main.py::TestAgentChat::test_empty_message_returns_400       PASSED
```

---

## 项目结构

```
.
├── main.py                  # FastAPI 后端，三个阶段的接口
├── requirements.txt         # Python 依赖
├── README.md                # 本文件
├── ACCEPTANCE.md            # 验收标准（开发前先写）
├── data/                    # SOP 文档存储目录（sop-001.html ~ sop-010.html）
├── static/
│   ├── v1.html              # Phase 1 前端
│   ├── v2.html              # Phase 2 前端
│   └── v3.html              # Phase 3 前端
├── tests/
│   └── test_main.py         # pytest 测试套件
└── ai-log/                  # AI 协作记录（prompt 归档）
    ├── phase1-keyword-search.md
    ├── phase2-semantic-search.md
    └── phase3-agent.md
```

---

## 技术决策说明

| 决策 | 选择 | 原因 |
|------|------|------|
| 语义检索算法 | TF-IDF（char ngram） | 只有 10 份 SOP 文档，引入向量数据库过度设计；char ngram 对中英文都有效 |
| Agent 框架 | 直接调用 Anthropic SDK | 文档数量少，不需要 LangChain/LlamaIndex 的额外复杂度 |
| 文件安全 | 白名单 + 路径校验 | readFile 工具只允许读 sop-001 到 sop-010，防止路径穿越 |
| API 不可用 | Mock 模式降级 | 没有 API Key 或余额不足时，返回示例回答，demo 仍可演示 |

---

## 已知限制

- Phase 2 语义搜索使用 TF-IDF，不是真正的语义向量检索（扩展可接入 embedding API）
- 文档仅支持 `.html` 格式
- Phase 3 Agent 最多进行 5 轮工具调用，避免无限循环
- 没有用户认证，接口完全开放

---

## 环境变量

| 变量名 | 必须 | 说明 |
|--------|------|------|
| `ANTHROPIC_API_KEY` | 否 | Phase 3 Agent API Key；缺失时自动进入 Mock 模式 |
| `CLAUDE_SESSION_INGRESS_TOKEN_FILE` | 否 | 备用认证方式（token 文件路径） |
