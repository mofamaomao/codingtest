# ACCEPTANCE.md — On-Call Assistant 验收标准

> 开发原则：先写这个文件，再让 AI 实现功能，再跑测试验收。
> 每个 [ ] 都是一个可执行的测试用例。

---

## Phase 1：关键词检索 `/v1/search`

### 正常场景
- [ ] 输入 `restart service` → 返回 200，results 包含 sop-001.html，snippet 包含匹配上下文
- [ ] 输入 `replication` → 返回 200，results 包含 sop-002.html
- [ ] 大小写混合 `Restart Service` → 与小写结果一致（大小写不敏感）
- [ ] 结果按出现频次（score）降序排列

### 边界场景
- [ ] 输入空字符串 → 返回 400，`{"detail": "Query cannot be empty"}`
- [ ] 输入超过 500 字符 → 返回 400
- [ ] 没有匹配结果 → 返回 200，`{"total": 0, "results": []}`，不是 404

### 文档上传 `/v1/documents`
- [ ] 上传合法 .html 文件 → 返回 200，`{"total": N}`，N 递增
- [ ] 上传非 .html 文件 → 返回 400

---

## Phase 2：语义检索 `/v2/search`

### 正常场景
- [ ] 输入 `memory issue` → 返回 200，sop-001.html 在结果列表中
- [ ] 结果按 score 降序排列
- [ ] score < 0.01 的结果被过滤掉，不出现在列表中

### 边界场景
- [ ] 输入空字符串 → 返回 400
- [ ] 没有文档时调用 → 返回 503，`{"detail": "No documents indexed yet"}`

---

## Phase 3：Agent 对话 `/v3/chat`

### 正常场景（有 API Key）
- [ ] 发送 `"服务 OOM 怎么处理？"` → 返回 200，`reply` 是非空字符串，不是 `undefined`
- [ ] `reply` 内容提到了 SOP 中的步骤（说明 readFile 工具被调用了）
- [ ] `tool_calls` 列表非空，包含 `readFile` 的调用记录
- [ ] 连续对话 3 轮 → 上下文正确保留（第 3 轮能引用第 1 轮的内容）

### 降级场景（无 API Key / 余额不足）
- [ ] 没有 API Key 时服务能正常启动（不崩溃）
- [ ] 发送消息 → 返回 200，`reply` 是可读的 Mock 回答，不是空字符串
- [ ] `mode` 字段为 `"mock"`，前端可以展示提示

### 边界场景
- [ ] 发送空 `message` → 返回 400，`{"detail": "message cannot be empty"}`
- [ ] 缺少 `message` 字段 → 返回 400

### 安全场景（readFile 工具）
- [ ] readFile 传入 `"../etc/passwd"` → 工具返回错误字符串，Agent 告知用户无法读取，不暴露系统文件
- [ ] readFile 传入 `"*.html"` → 工具返回错误字符串，拒绝通配符
- [ ] readFile 传入 `"secret.html"` → 工具返回错误字符串，不在白名单内
- [ ] readFile 只能读 `sop-001.html` 到 `sop-010.html`

---

## 验收流程

1. 运行测试：`pytest tests/test_main.py -v`
2. 所有 PASSED → 对应的 [ ] 打勾
3. 如有 FAILED → 把报错反馈给 AI 修复，再跑一遍
4. 截图测试结果存到 `screenshots/` 目录
