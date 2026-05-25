# ai-log/phase3-agent.md

## 时间
2024-01-15

## 目标
修复 Phase 3 Agent 对话返回 undefined 的问题，补充错误处理和 mock fallback

## 问题背景
原代码中：
1. `client = _make_anthropic_client()` 在模块加载时执行，没有 API Key 时直接抛 RuntimeError，服务无法启动
2. `/v3/chat` 中 `client is None` 检查永远触发不了（因为上面已经崩了）
3. `_extract_text()` 在某些边界情况下返回空字符串，前端渲染为 undefined
4. `do_read_file` 用了 `Path(fname).name` 做部分保护，但没有明确拒绝并返回错误信息

## Prompt 原文（发给 AI 的）

```
帮我修复 FastAPI + Anthropic SDK 的 On-Call Agent 代码，要求：

验收标准（必须满足）：
1. 没有 ANTHROPIC_API_KEY 时，服务能正常启动，不崩溃
2. 没有 API Key 时，POST /v3/chat 返回 200 + mock 回答，不返回空字符串或 undefined
3. API Key 无效/余额不足时，返回 mock 回答而不是 500 错误
4. readFile 工具必须拒绝：路径含 ".."、含 "/"、含通配符 "*"/"?"、不在白名单的文件名
5. 白名单：sop-001.html 到 sop-010.html
6. reply 字段永远是非空字符串

同时帮我写对应的 pytest 测试用例，覆盖上面所有场景。

当前代码：[粘贴了 main.py 的 Phase 3 部分]
```

## AI 输出摘要
- 修改了 `_make_anthropic_client()` 返回 None 而不是 raise RuntimeError ✓
- 用 try/except 包裹模块级 client 初始化 ✓
- 添加了 `MOCK_REPLY` 常量和 mock 返回逻辑 ✓
- 在 `/v3/chat` 中分别处理 `AuthenticationError` 和 `RateLimitError` ✓
- `do_read_file` 加了白名单 `ALLOWED_FNAMES` 和 4 种拒绝条件 ✓
- 生成了 `TestDoReadFile` 测试类（6个测试用例）✓
- 生成了 `TestAgentChat` 测试类（3个测试用例）✓

## 验收结果
```
pytest tests/test_main.py::TestDoReadFile -v

tests/test_main.py::TestDoReadFile::test_path_traversal_double_dot      PASSED ✓
tests/test_main.py::TestDoReadFile::test_path_traversal_with_slash       PASSED ✓
tests/test_main.py::TestDoReadFile::test_wildcard_rejected               PASSED ✓
tests/test_main.py::TestDoReadFile::test_question_mark_wildcard_rejected  PASSED ✓
tests/test_main.py::TestDoReadFile::test_non_allowlisted_file_rejected   PASSED ✓
tests/test_main.py::TestDoReadFile::test_allowed_file_but_not_exist      PASSED ✓

6 passed in 0.09s
```

截图：screenshots/phase3-tests-pass.png ✓

## 遗留问题
- `TestAgentChat` 的集成测试（需要 mock Anthropic client）还没完成
- 连续对话上下文保留未测试
- Phase 3 前端的 mock 模式提示 UI 样式待优化

## 下一步
下个 session 补充 `TestAgentChat` 的集成测试，需要用 `unittest.mock.patch` mock 掉 anthropic.Anthropic
