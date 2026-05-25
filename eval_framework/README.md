# PhysicsScholar 评测框架

## 目录结构

```
eval_framework/
├── evaluator.py        # 主脚本
├── test_cases.json     # 测试用例（你填写answer和tool_log）
├── check_models.py     # 测试模型是否连接
├── .env                # 环境变量填写
├── results/
│   ├── raw/            # 原始评分存档（每次运行生成一个JSON）
│   └── report/         # 汇总报告（每次运行生成一个JSON）
└── README.md
```

## 环境变量

运行前.env内设置API密钥：

```.env
DEEPSEEK_API_KEY=sk-xxx
GEMINI_API_KEY=xxx
```

## 依赖安装

```bash
pip install openai python-dotenv
```

## 填写测试用例

在 `test_cases.json` 中，每道题需要你填写：

```json
{
  "id": "Q01",
  "question": "问题原文（已填好）",
  "intents": ["I3"],
  "reference_points": "参考要点（已填好，I2类题为空字符串）",
  "answer": "← 粘贴PhysicsScholar的回答",
  "tool_log": "← 粘贴整理好的工具调用简报，无调用填空字符串"
}
```

## 运行

```bash
# 跑全部题目
python evaluator.py

# 只跑指定题目
python evaluator.py Q01 Q02 Q05
```

## 工具调用简报格式建议

```
[第1次调用]
工具：rag_tool
查询：光注入锁定相噪
结果摘要：召回3条相关片段，最相关内容涉及OEO边模问题

[第2次调用]
工具：s2_search_tool
查询：OEO phase noise 2023
结果摘要：返回5篇论文，选取2篇作为依据
```

## 评委配置

| 评委              | 模型              | 用途     |
| ----------------- | ----------------- | -------- |
| deepseek-v3       | deepseek-chat     | 打分评委 |
| deepseek-v4-flash | deepseek-v4-flash | 打分评委 |
| gemini-2.5-flash  | gemini-2.5-flash  | 打分评委 |
| gemini-2.5-pro    | gemini-2.5-pro    | 打分评委 |
| gemini-3.1-pro    | gemini-3.1-pro    | 汇总评委 |

## 输出说明

- `results/raw/raw_TIMESTAMP.json`：所有评委对所有题的原始输出，完整存档
- `results/report/report_TIMESTAMP.json`：结构化汇总报告，含综合评分和争议标记

高争议case（`anomaly_flagged: true`）建议人工复查。
