CITATION_DEFAULT = """
引用信息使用英文原文呈现，不附加翻译。
"""
CITATION_TRANSLATION = """
启用中文翻译模式。对于每条引用，格式如下：

<ref id="N">
来源信息 | 支撑片段
<zh>标题译文。支撑片段译文（如有）。</zh>
</ref>

翻译规则：
- <zh>标签必须存在，紧跟在来源信息行之后
- 翻译论文标题和支撑片段
- 不翻译作者姓名、期刊名、会议名、arXiv ID
"""

TOOL_DECISION_PLUGIN = """
// ─── 状态读取 ─────────────────────────────────
State {
  current_budget: Int = SYSTEM.Remaining_Tool_Calls  // 只读，禁止修改
  missing_evidence: List[str]                        // 来自上一 Phase 的缺口识别
  decision: Enum[CONTINUE, STOP]
}

// ─── 首次进入：全局规划（仅第一次执行 <thinking> 时触发）──
if call_history == []:
    Global_Plan {
        Estimated_Steps: 预估本次回复需要的工具调用总次数（不超过current_budget）
        Call_Sequence: 按优先级排列的调用顺序和理由
        // 示例："1. search_paper_tool定位论文→2. rag_tool检索内容，预计2次"
        // 若无需工具：写"无需工具调用，直接回答"并 goto NEXT_PHASE
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
  （若调用 arxiv_tool，须额外说明 max_results 的取值理由）
- Change Log：与历史调用的参数差异（首次写"新调用"）

→ 执行工具调用，等待返回

// ─── 调用后更新（工具返回后立即执行）────────────
if tool_result.status == "FAILED" or tool_result.is_empty:
    Observation: 工具返回了异常状态 [FAILED / EMPTY]
    Reflection: 分析原因（参数过窄 / 服务不可用 / 目标本身不存在）
    if tool_result.error_type == "rate_limited" and retry_count >= 2:
        decision = STOP
        reason = "Rate limit hit ≥2 times — abort"
        goto NEXT_PHASE
    if has_concrete_parameter_improvement and current_budget > 0:
        decision = CONTINUE  // 仅在有明确改进方向时重试，需在 Change Log 中说明变化
    else:
        decision = STOP
        reason = "Tool Failed — No viable retry strategy"
        goto NEXT_PHASE
else:
    Observation: 工具返回了 [X]
    Reflection: 此结果[有效填补 / 未填补]缺口 [Y]，原因是 [Z]
    更新 missing_evidence：移除已填补项，保留或新增未解决项
    重新进入 CURRENT_PHASE 入口，以更新后的 missing_evidence 重新评估 decision
"""
