# 主/子 Agent LLM 解耦 + Token 可视化

> **状态（2026-09-12）**：已实现。后端（token_usage/llm/graph/jina/memory/init_SQLite）+ 前端（MessageItem/ChatWindow/ChatPage/ConfigModal）改完，leaf 导入、token_usage 单元逻辑、DB 迁移+回填、前端 build、jina 纯逻辑测试均通过。待用户跑 live 问题确认 usage 出现 + 子 LLM 解耦生效。

## Context

两个新需求：

1. **解耦主/子 agent 的 LLM**。现状：子 agent（`build_subagent`，`graph.py:847`）的 `call_llm` 节点用的是构建时传入的 `main_llm`，`final_answer` 节点用的是模块级全局 `llm`（= `main_llm`，`graph.py:72`）。已有的 `sub_llm`（`llm.py:25`，config key `sub_llm.*`，逐字段 fallback 到 `main_llm.*`）目前只服务于 jina 切片打分 + extractor 元信息提取，与子 agent 沫有接线。目标：子 agent 接入 `sub_llm.*` 配置（未填则 fallback 到主 LLM，已由 config 层 `or MAIN_LLM_*` 兜底）。

2. **Token 可视化**（BYOK，只计不计价）。现状：全仓无任何代码读 `usage_metadata`；`done` SSE 帧只带 message id + warning + answer。目标：每条回答呈现主/子 agent（含 jina 打分）各自的 input/output token，并随消息落库，刷新后仍可见。

**已确认决策**：①复用 `sub_llm.*` 配置项驱动子 agent（一组"副 LLM"服务子 agent + jina + extractor，子 agent 实例与 jina 实例按各自用途用不同参数分别构建）；②token 持久化到 `messages` 表（新增 `usage_json` 列）。

---

## Feature 1 — 子 agent 接入子 LLM

### 1.1 新增通用工厂 `build_sub_llm(**overrides)`（`src/llm.py`）

`sub_llm` 单例 `max_tokens=1024`（为 jina/extractor 短结构化任务设），子 agent 跑多轮检索推理会被截断——不复用单例。新增**一处真值源**的通用工厂：固定连接三元组（来自 `sub_llm.*` config，已逐字段 fallback 到 `main_llm.*`）+ `extra_body` + `max_retries`，其余参数由调用方按用途覆盖：

```python
def build_sub_llm(**overrides) -> ChatOpenAI:
    """副 LLM 实例工厂。固定 model/api_key/base_url（来自 SUB_LLM_* config）+ extra_body + max_retries；
    temperature/max_tokens/streaming 等由调用方覆盖。单例 sub_llm 与子 agent 实例都经此构建。"""
    defaults = dict(temperature=0.15, max_tokens=1024, streaming=False)
    defaults.update(overrides)
    return ChatOpenAI(
        model=SUB_LLM_MODEL, api_key=SUB_LLM_API_KEY, base_url=SUB_LLM_BASE_URL,
        extra_body=DEEPSEEK_EXTRA_BODY, max_retries=5, **defaults,
    )

# 兼容薄壳：jina/extractor 仍 import sub_llm，行为字节级不变（temp 0、max_tokens 1024、非流式）。
# 注意 0.0 是 jina/extractor 结构化任务的设定，不是子 agent 的。
sub_llm = build_sub_llm(temperature=0.0)
```

**子 agent 温度 = 0.15（非 0.0）**：子 agent 现状走 `main_llm`（temp 0.15），改成 0.0 是未经验证的行为回归。0.0 只是 jina/extractor 那个单例的设定（结构化短任务），子 agent 是 agentic 检索推理（判缺口/选工具/写摘要），应保持与当前一致的 0.15。子 agent 调用：`build_sub_llm(max_tokens=None, streaming=True)`（不传 temperature → 取默认 0.15）。

### 1.2 `build_subagent` 默认走工厂（`graph.py:860-861`）

```python
if llm is None:
    from src.llm import build_sub_llm
    llm = build_sub_llm(max_tokens=None, streaming=True)   # temp 0.15 默认、不截断、流式
```

`make_retrieve_tool`（`graph.py:1144`）调 `build_subagent(user_id)` 不传 llm → 自动吃到子 LLM。`build_agent`（主 agent）仍 `import main_llm`，不动。cross-model probe（`scripts/harness_probe.py` 经 `_build_override_llm` 显式传 llm）不受影响。

### 1.3 修 `final_answer` 漏接（关键点）

`final_answer`（`graph.py:295`）是模块级函数，用模块全局 `llm`（= main_llm）。子 agent 走 `final_answer` 节点时会回落到 main_llm——解耦不彻底。修法：

- `final_answer(state, profile, llm=None)`：加 `llm` 参数，`None` 时回落模块全局（保持主 agent 行为字节级不变）。
- `_final` 闭包（`graph.py:583-584`）注入 `_build_graph` 的 `llm`：`return await final_answer(state, profile, llm)`。

子 agent 的 `_final` 闭包取到的是 build_subagent 传入的子 LLM → `final_answer` 也走子 LLM。

### 1.4 配置/前端文案

- `config/user_config.yaml.example`：`sub_llm` 段注释更新为"副 LLM（选填，留空则复用主 LLM；驱动 检索子 Agent / Jina 全文打分 / 论文元信息提取）"。
- 前端 `frontend/src/components/Settings/ConfigModal.vue`：sub_llm 字段的 label/help text 同步更新（字段本身不动）。

---

## Feature 2 — Token 计量与呈现

### 2.1 机制选型：`contextvars` 隐式通道

`jina_tool` 是模块级 `@tool`（`jina_tool.py:414`），非工厂，无法用闭包传 per-request 累加器。用 `contextvars.ContextVar` 携带 per-request usage dict，`astream_events` 同一 async task 内的节点/工具都能读到（asyncio 子任务继承上下文）。bucket 标签（main/sub/jina）在各调用点显式传入。

### 2.2 新增 `src/rag/token_usage.py`

```python
import contextvars
_ctx = contextvars.ContextVar("token_usage", default=None)

def new_usage() -> dict:
    return {"main": {"input": 0, "output": 0},
            "sub":  {"input": 0, "output": 0},
            "jina": {"input": 0, "output": 0}}

def set_usage(u): return _ctx.set(u)
def reset_usage(token): _ctx.reset(token)

def acc(bucket: str, response):
    u = _ctx.get()
    if u is None: return                       # 测试/未设置上下文 → no-op
    um = getattr(response, "usage_metadata", None)
    if not um: return
    u[bucket]["input"]  += um.get("input_tokens", 0) or 0
    u[bucket]["output"] += um.get("output_tokens", 0) or 0
```

### 2.3 累加点（三处）

- **`call_llm` 闭包**（`graph.py:533` 之后）：`acc(usage_bucket, response)`。`usage_bucket` 在 `_build_graph` 内由 `finalize_fn is not None` 派生（子 agent 标记），无需新参数穿透工厂链。
- **`final_answer`**（`graph.py:309` 之后）：加 `usage_bucket="main"` 参数；`acc(usage_bucket, response)`。`_final` 闭包注入派生出的 `usage_bucket`（与 1.3 的 llm 注入同路径）。
- **jina `_score_chunk`**（`jina_tool.py:289` 之后）：`acc("jina", response)`。模块级函数直接 import `acc`，靠 ContextVar 拿 dict。

### 2.4 per-request 上下文设置（`chat_stream` / `regenerate_stream` / `chat` / `regenerate`）

在 `_prepare` 之前建 usage、设进 ContextVar，`finally` reset。`_consume_events` 已持有 per-request `result` dict（`graph.py:1726/1794`），把 usage 挂到 `result["usage"]`，`done` 帧带出：

```python
usage = new_usage(); tok = set_usage(usage)
result = {"tool_results": [], "usage": usage}
try:
    agent, initial_state = _prepare(...)
    async for frame in _consume_events(agent, initial_state, request, result): yield frame
    ...
    yield _format_sse("done", ..., usage=usage, answer=...)
finally:
    reset_usage(tok)
```

`regenerate_stream` 的 `done` 帧（`graph.py:1826+`）同样加 `usage`。非流式 `chat()`/`regenerate()`（`graph.py:1251+`）也设 ContextVar，usage 进返回 dict。

### 2.5 `usage_metadata` 可用性验证（实施第一步，去风险）

langchain `ChatOpenAI` 在 `ainvoke` 后 `response.usage_metadata` 应含 `input_tokens`/`output_tokens`（DeepSeek 等 OpenAI 兼容 API 默认在响应体带 `usage`）。`main_llm`/子 agent LLM 均 `streaming=True`，langchain 聚合末 chunk 的 usage。**实施首步**：在 `call_llm` 临时 `logger.info` 打印 `response.usage_metadata`，跑一个 live 问题确认。若个别 provider 不返回，则在该 LLM 实例的 `extra_body` 加 `stream_options={"include_usage": True}`（OpenAI 兼容标准，流式末 chunk 必带 usage）。jina 的 `sub_llm` 非流式，usage 直接在响应体，无此风险。

### 2.6 持久化（`messages` 表 + memory 层）

- **迁移**（`src/core/init_SQLite.py` `init_db`）：`messages` 表 CREATE 之后加幂等迁移——`PRAGMA table_info(messages)` 查无 `usage_json` 列则 `ALTER TABLE messages ADD COLUMN usage_json TEXT`。新库由 CREATE 带上该列（CREATE 语句同步加 `usage_json TEXT`）。
- **INSERT**（`src/rag/memory.py:8-14` `INSERT_SQL`）：加 `usage_json` 列与占位符。
- **`_repo.add` / `ConversationMemory.add`**（`memory.py:116-121`、`:230`）：`add()` 加 `usage: dict | None = None` 参数，`json.dumps` 后入库。
- **历史回填**：`SELECT_MESSAGES_FULL_SQL` 是 `SELECT *`（`:21`），自动带新列；行→message 转换处把 `usage_json` 解析后挂到 `message.additional_kwargs["usage"]`（BaseMessage 无原生 usage 字段）。LLM 上下文历史路径（`memory.get` → `format_history`）不读 usage，不受影响。
- **前端历史 API**（`src/api/routes.py` 消息列表端点）：序列化时带上 `usage`（从 `additional_kwargs` 取）。

### 2.7 落库点把 usage 传入

`chat_stream`/`regenerate_stream` 落库时 `memory.add(AIMessage(...), parent_id=..., usage=usage)`；`chat()`/`regenerate()` 同理。`usage` 在 try 块内、`_consume_events` 跑完后取（已累加完毕）。

### 2.8 前端呈现

**结论：token 只放 MessageItem，不动 ThinkingTimeline。**

ThinkingTimeline 是纯流式期组件（`v-if="phase !== 'idle'"`，`ChatWindow.vue:12` 只在流式块渲染），reload 后不渲染——其摘要行天然不持久；且 usage 在 `done` 帧才 final，而摘要行在 answer 阶段已显示，有 timing 错配。给它加 token 既脆弱又会造成"流式期可见/reload 消失"的不一致。Timeline 的步骤/工具数/耗时从未持久化（思考被 `trim_thinking` 剥离，工具 trace 是 SSE-only），持久化摘要行需新建结构化 trace 列 + 后端捕获 + 前端渲染，非顺手活，**本次不做**。

MessageItem 是流式（`ChatWindow.vue:13`）与历史列表（`ChatWindow.vue:5-9`）共用的气泡，一处改两处生效：

- **MessageItem.vue**：加可选 `usage` prop；assistant 气泡 action-bar/时间戳区（约 `:351-358` `.timestamp` 样式）加一行小字 "输入 N · 输出 M tokens"（主/子分列或合计，零值不显示）。`v-if="usage"` 守卫空值。
- **ChatWindow.vue:5-9**：历史列表 MessageItem 传 `:usage="msg.usage"`。
- **ChatPage.vue:651-660**（done 帧到时 push agent 消息）：把 `done.usage` 挂到 message 对象的 `usage` 字段——usage 与消息同生，无 timing 问题。
- **历史 API**（`src/api/routes.py` 消息列表端点）：每条 assistant 消息带 `usage`（从 `usage_json` 列解析）；ChatPage 历史加载映射到 `msg.usage`，使 reload 后 MessageItem 仍显示。

---

## 改动文件清单

**后端**
- `src/llm.py` — 新增通用工厂 `build_sub_llm(**overrides)`；`sub_llm` 单例改为 `build_sub_llm(temperature=0.0)` 兼容薄壳
- `src/rag/graph.py` — `build_subagent` 默认走工厂；`final_answer` 加 `llm`+`usage_bucket` 参数；`_final`/`call_llm` 注入累加；`_build_graph` 派生 `usage_bucket`；`chat_stream`/`regenerate_stream`/`chat`/`regenerate` 设 ContextVar + `done` 帧带 usage + 落库传 usage
- `src/rag/token_usage.py` — 新文件（ContextVar + acc 辅助）
- `src/rag/tools/jina_tool.py` — `_score_chunk` 调 `acc("jina", response)`
- `src/rag/memory.py` — INSERT 加列；`add()` 加 `usage` 参数；行→message 回填 usage
- `src/core/init_SQLite.py` — `messages` 表加 `usage_json` 列 + 幂等迁移
- `src/api/routes.py` — 消息列表端点序列化带 `usage`
- `config/user_config.yaml.example` — sub_llm 段注释

**前端**
- `frontend/src/api/chat.js` — done handler 存 usage
- `frontend/src/components/Chat/MessageItem.vue` — 加 `usage` prop + token 展示
- `frontend/src/components/Chat/ChatWindow.vue` — 历史列表 MessageItem 传 `:usage`
- `frontend/src/views/ChatPage.vue` — done 帧挂 `usage` 到 push 的 agent 消息
- `frontend/src/components/Settings/ConfigModal.vue` — sub_llm 文案
- `frontend/src/components/Settings/ConfigModal.vue` — sub_llm 文案

---

## 验证

1. **usage 可用性 spike**：临时日志跑一个 live 问题，确认 `call_llm`/`final_answer`/jina 三处 `response.usage_metadata` 有值；缺失则加 `stream_options`。
2. **子 LLM 解耦**：配不同的 `sub_llm.*`（不同 model/base_url），问一个触发 retrieve 的问题，确认子 agent 的 `call_llm` + `final_answer` 都打到子 LLM（看 base_url 访问日志或 LangSmith trace，project=`physics-scholar-dev`）；留空 `sub_llm.*` 确认回落主 LLM。
3. **token 呈现**：问一个问题，确认 `done` 帧含 `usage`、MessageItem 气泡显示 token；刷新页面确认 token 仍从历史 API 回填、MessageItem 一致显示。
4. **回归**：`pytest tests/test_rag_chain.py tests/test_jina_tool.py`（jina 测试 patch `sub_llm`，不受子 agent 改动影响）；跑一个 normal + 一个 discuss 模式问题确认主 agent 行为不变。
5. **非流式兜底**：临时禁用流式走 `chat()`，确认 usage 仍进返回 dict。

## 不做的事

- 不改 `main_llm` 单例的热重载（`reload_config` 不重建实例是既有问题，README 已声明需重启；子 agent 因每次 retrieve 重建实例反而天然支持热刷新，是 bonus 不是目标）。
- 不给 jina/extractor 单独开配置项（按决策复用 `sub_llm.*`）。
- 不计 extractor 的 token（extractor 是 ingestion 期成本，非"本条回答"成本，超出本次范围）。
