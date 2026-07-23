CITATION_FORMAT = """
## Citation Format（bind-by-id：你只抄 source_id，来源元信息由系统填）

### 行内引用
在正文中引用时，使用以下格式：
[ref:N]

N为引用序号，从1开始，按在正文中首次出现的顺序编号。

### References区域
在回答末尾统一列出所有引用，每条格式：

<ref id="N">
[source_id] | 支撑片段
</ref>

**你只写 `[source_id]` 和 `支持片段` 两部分，绝不自己写作者、标题、venue、年份、链接等来源元信息**——系统会按 source_id 自动查工具结果填充完整引用。你手写元信息反而会抄错（漏作者、拼错、编 venue），所以只抄短而稳的 id。

### source_id 拼写规则（带类型前缀，逐字符照抄检索结果）

retrieve 返回的检索结果里已带可直接抄的 id 字段，照抄 + 加前缀：

| 来源 | source_id 格式 | id 字段出处（检索结果里） |
|---|---|---|
| 本地 RAG 检索 | `rag:<doc_id>` | 每段开头 `[rag:xxx | ...]` 里的 doc_id |
| Semantic Scholar | `s2:<s2_paper_id>` | papers[].s2_paper_id |
| arXiv | `arxiv:<arxiv_id>` | papers[].arxiv_id |
| OpenAlex | `openalex:<openalex_id>` | papers[].openalex_id |

- id 短且可逐字符抄，转写低错；前缀固定，不要自创。
- **严禁编造 id**：只抄本轮检索结果真实返回的 id。编造的 id 系统查无匹配，会留下幻觉信号。
- 本地文档若无 doc_id（检索结果段头无 `rag:` 前缀），该来源不参与 bind-by-id，按下方“无 source_id 时的退路”处理。

### 支撑片段
引用该来源时所依据的具体内容，直接截取原文，不改写，不概括。
- RAG检索结果：截取检索返回的相关文段，保持原文
- 论文摘要：截取摘要中的相关句子，标注“摘要片段”
- 无可靠原文时：省略支撑片段，格式退化为 `<ref id="N">[source_id]</ref>`（只有 id，无 `| 摘抄`）

### 无 source_id 时的退路
仅当来源确实没有稳定 id（如检索结果里的网页全文、用户口述的来源）时，才允许在 `[source_id]` 位置直接写来源描述（标题/域名等）。这是例外不是常规，优先用检索结果里的结构化 id。

{citation_plugin}

### 格式示例

正文：...已有工作表明，调制边带的相位噪声可以通过光子辅助方案显著抑制 [ref:1]，但在宽带场景下仍面临挑战 [ref:2]。

References:
<ref id="1">
[s2:abc123def] | 摘要片段：“We demonstrate a 15 dB reduction in phase noise across a 10 GHz bandwidth using...”
</ref>
<ref id="2">
[rag:paper_2024_mwp_001] | “The major functions of microwave photonics systems include photonic generation, processing, control and distribution of microwave and millimeter-wave (mm-wave) signals”
</ref>

<!--如启用翻译模式-->
<ref id="1">
[s2:abc123def] | 摘要片段：“reducing training time from hours to milliseconds...”
<zh>标题译文。支撑片段译文（如有）。</zh>
</ref>
"""

from ...builder import PromptModule

module = PromptModule(name="CITATION_FORMAT", content=CITATION_FORMAT, order=40)
