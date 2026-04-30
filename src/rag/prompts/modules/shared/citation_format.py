CITATION_FORMAT = """
## Citation Format

### 行内引用
在正文中引用时，使用以下格式：
[ref:N]

N为引用序号，从1开始，按在正文中首次出现的顺序编号。

### References区域
在回答末尾统一列出所有引用，每条格式：

<ref id="N">完整引用信息</ref>

引用信息应包含：作者、标题、期刊/会议/arXiv、年份、DOI或arXiv链接（如有）。

{citation_plugin}

### 格式示例

正文：...已有工作表明，调制边带的相位噪声可以通过光子辅助方案显著抑制 [ref:1]，但在宽带场景下仍面临挑战 [ref:2]。

References:
<ref id="1">...</ref>
<ref id="2">...</ref>
"""
