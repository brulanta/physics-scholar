CITATION_FORMAT = """
## Citation Format

### 行内引用
在正文中引用时，使用以下格式：
[ref:N]

N为引用序号，从1开始，按在正文中首次出现的顺序编号。

### References区域
在回答末尾统一列出所有引用，每条格式：

<ref id="N">
来源信息 | 支撑片段
</ref>

来源信息根据实际来源类型填写：
- 论文（arXiv / 期刊 / 会议）：作者、标题、来源、年份、链接（如有）
- 本地文档：文档标题、页码（如有）
- 网页 / 其他：标题、来源域名、访问时间或发布时间（如有）

支撑片段：引用该来源时所依据的具体内容，直接截取原文，不改写，不概括。
- RAG检索结果：截取检索返回的相关文段，保持原文
- 论文摘要：截取摘要中的相关句子，标注"摘要片段"
- 无可靠原文时：省略支撑片段，不补充概括性描述

{citation_plugin}

### 格式示例

正文：...已有工作表明，调制边带的相位噪声可以通过光子辅助方案显著抑制 [ref:1]，但在宽带场景下仍面临挑战 [ref:2]。

References:
<!--默认原文呈现-->
<ref id="1">
Zhang et al. "Photonic-assisted phase noise suppression..." arXiv:xxxx.xxxxx (2024) | 摘要片段："We demonstrate a 15 dB reduction in phase noise across a 10 GHz bandwidth using..."
</ref>
<ref id="2">
Microwave Photonics | "The major functions of microwave photonics systems include photonic generation, processing, control and distribution of microwave and millimeter-wave (mm-wave) signals"
</ref>
<!--如启用翻译模式-->
<ref id="1">
Ma et al. "Deep Photonic Reservoir Computer Meets UAV Control" arXiv:2604.10262 (2026) | 摘要片段："reducing training time from hours to milliseconds and slashing inference latency to nanoseconds...delivers residual-force prediction accuracy comparable to or exceeding TCN/MLP baselines"
<zh>深度光子储存计算机与无人机控制 | 摘要片段：“将训练时间从数小时减少到毫秒，并将推理延迟降低到纳秒……提供的残差力预测精度可与或超过 TCN/MLP 基准”</zh>
</ref>
"""
