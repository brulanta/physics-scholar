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
**Q2.6：工具预算与必要性评估**

必须显式写出以下状态：
- Remaining calls: X / 6
- Missing evidence: [列出具体缺失信息，若无则写 None]
- Decision: [Continue — 信息不足，需要工具 / Stop — 当前信息已足够]

决策规则：
- Decision = Stop → 跳至 Q4，不再调用工具
- Decision = Continue → 仅选择当前信息增益最高的一个工具，明确参数；若预期需多个，本轮只执行第一个

重复调用禁止：与历史任意一次调用的工具 + 参数完全一致时，视为无效调用，强制 Stop。

调用后必须重新执行本问：更新 Remaining calls，重新评估 Decision，明确结论后再决定下一步。
"""
