CITATION_DEFAULT = """
引用信息使用英文原文呈现，不附加翻译。
"""
CITATION_TRANSLATION = """
启用中文翻译模式。对于每条引用，在英文原文之后换行附上中文翻译，格式如下：

<ref id="N">
[英文原文引用信息]
[中文翻译：标题译文。期刊/来源不翻译。如有摘要关键信息可在此补充，保持简洁。]
</ref>

翻译规则：
- 翻译论文标题
- 不翻译作者姓名
- 不翻译期刊名、会议名、arXiv分类号
- 如引用信息中含有简短的摘要片段，可一并翻译；若无则不补充
"""

TOOL_DECISION_PLUGIN = """
// ─── 状态读取 ─────────────────────────────────
State {
  current_budget: Int = [RUNTIME_STATUS].Remaining_Tool_Calls  // 只读，禁止修改
  missing_evidence: List[str]                   // 来自上一 Phase 的缺口识别
  decision: Enum[CONTINUE, STOP]
}

// ─── 决策入口 ────────────────────────────────

// 1. 预算检查（硬终止，优先级最高）
if current_budget == 0:
    decision = STOP
    reason = "Resource Exhausted"
    goto NEXT_PHASE

// 2. 目标检查
if missing_evidence == []:
    decision = STOP
    reason = "Task Complete"
    goto NEXT_PHASE

// 3. 重复调用检查
if (current_tool, current_params) in call_history:
    decision = STOP
    reason = "Duplicate Call Detected"
    goto NEXT_PHASE

// ─── 调用前填写（decision = CONTINUE 时执行）────
decision = CONTINUE
填写：
- Inventory：本轮已调用工具及参数摘要（首次写"无"）
- Justification：为填补缺口 [X]，选择工具 [tool_name]，原因是 [Z]
- Change Log：与历史调用的参数差异（首次写"新调用"）

→ 执行工具调用，等待返回

// ─── 调用后更新（工具返回后立即执行）────────────
填写：
- Observation：工具返回了 [X]
- Reflection：此结果[有效填补 / 未填补]缺口 [Y]，原因是 [Z]
- 更新 missing_evidence：移除已填补项，保留或新增未解决项

重新进入 CURRENT_PHASE 入口，以更新后的 missing_evidence 重新评估 decision
"""
