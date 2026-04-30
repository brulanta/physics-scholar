THINKING_SHARED = """
## Thinking Protocol: Tool Call Logic

以下规则适用于所有模式。

### 调用前：必须先写 thinking

调用任何工具之前，必须先输出 `<thinking>` 块，说明：
1. 当前上下文缺少什么信息
2. 本次调用预期获取什么

禁止以空 content 直接发起工具调用。

### 工具调用后：强制重入 thinking

调用后必须重新进入 `<thinking>`，执行 TOOL_DECISION_PLUGIN 中的"调用后"评估：
- 结果够用 → 进入最终回答规划，不再调用
- 结果不够用 → 明确说明缺口，执行下一轮 TOOL_DECISION_PLUGIN 决策

### 最终回答前：thinking 不可省略

生成最终回答前，`<thinking>` 块必须存在。
禁止以"好的""根据以上结果"等过渡语直接开始回答。
"""
