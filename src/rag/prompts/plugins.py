CITATION_DEFAULT = """
引用信息使用英文原文呈现，不附加翻译。
来源元信息（作者/标题/venue/年份/链接）由系统按 source_id 自动填充，你只写 [source_id] | 支撑片段。
"""

CITATION_TRANSLATION = """
启用中文翻译模式。对于每条引用，格式如下：

<ref id="N">
[source_id] | 支撑片段
<zh>标题译文。支撑片段译文（如有）。</zh>
</ref>

翻译规则：
- <zh>标签必须存在，紧跟在支撑片段行之后
- 翻译论文标题和支撑片段
- 不翻译作者姓名、期刊名、会议名、论文 ID（source_id、arXiv ID、DOI 等）
- 来源元信息（作者/标题/venue/年份/链接）由系统按 source_id 自动填充，你只写 [source_id] | 支撑片段 + <zh>译文，不要自己写元信息
"""

TOOL_DECISION_PLUGIN = """
⚠️ Phase 3 检索决策：决定是否调用 retrieve（你唯一可用的工具）。不写此段=自动驳回。

→ [TOOL_LOOP: BEGIN]  // retrieve 返回后从此处重新进入

判断：本问题是否需要文献证据（外部论文 / 本地文献内容 / 最新进展 / 具体数据）？

- 需要 → 调用 retrieve(query=<检索需求>)，输出 [TOOL_LOOP: PENDING] 闭合 </thinking> 等待结果。
- 不需要（凭背景知识可答）→ [TOOL_LOOP: DONE]，保持 thinking 进入 Phase 4。

⚠️ 你只有**一次** retrieve 机会。retrieve 内部已跑完整检索循环（多轮检索 + 降级链
s2→openalex→arxiv→jina + 本地 RAG），返回的即本轮最终检索结果。调用后无论结果是否理想，
都基于它组织回答——**不再发起第二次 retrieve**（检索不够理想是 retrieve 内部子 agent 的事，
主 agent 这一层接受结果）。retrieve 返回后输出 [TOOL_LOOP: DONE] 进入正文。

> [TOOL_LOOP: PENDING] = 将调用 retrieve，闭合 thinking 等待；
> [TOOL_LOOP: DONE] = 进入正文（不调 retrieve，或 retrieve 已返回）。
"""
