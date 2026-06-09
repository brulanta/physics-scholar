[English](README_en.md) | 中文

# PhysicsScholar

> 面向微波光子学研究者的本地学术 Agent —— 论文问答 · 在线检索 · 学术讨论 · 离线运行

> 📖 非技术用户请直接查看 [用户手册](docs/用户手册.md)

[![Python](https://img.shields.io/badge/Python-3.10+-blue)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110+-green)](https://fastapi.tiangolo.com/)
[![Vue3](https://img.shields.io/badge/Vue-3-brightgreen)](https://vuejs.org/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Agent-orange)](https://github.com/langchain-ai/langgraph)

---

## 项目简介

PhysicsScholar 是一个运行在本地的学术研究辅助 Agent，面向微波光子学方向的研究者设计，也可通过替换 Prompt 模块适配其他研究领域。

**核心思路：** 研究者上传自己的论文构建私有 RAG 知识库，结合 arXiv / Semantic Scholar / OpenAlex 在线检索与 Jina 全文阅读工具，由 LangGraph Agent 驱动，实现文献问答、引用溯源、学术讨论、代码辅助等能力。整个程序打包为 Windows exe，解压即用，无需配置 Python 环境。

**定位差异：**

- 不是通用聊天机器人，是嵌入研究工作流的专用工具
- 不依赖云端知识库，私有论文数据保留在本地
- 无需联网部署，exe 双击启动，浏览器访问

---

## 功能概览

### 核心层

| 功能                    | 说明                                                                                   |
| ----------------------- | -------------------------------------------------------------------------------------- |
| **PDF 上传与解析**      | 拖拽或点击上传，自动切片、向量化入库，支持多文件并发                                   |
| **RAG 问答 + 引用溯源** | 检索相关段落拼入 Prompt，回答标注来源标题与原文位置                                    |
| **双语对照引用**        | 引用论文原文时同时展示英文原文与中文译文（可在前端开关）                               |
| **多轮对话记忆**        | 会话内上下文感知，支持连续追问                                                         |
| **结构化学术 Prompt**   | 严格 CoT + 角色定位 + 强制引用机制，用有据可查的回答替代凭空发挥，从根本上降低幻觉风险 |
| **代码生成能力**        | Prompt 层面强化：在适当时机主动生成 MATLAB / Python 代码，保证输出可执行性             |

### Agent 工具

Agent 根据每次 CoT 判断信息缺口，自动决策调用以下工具（每轮最多调用 6 次）：

| 工具                 | 定位                | 说明                                                                          |
| -------------------- | ------------------- | ----------------------------------------------------------------------------- |
| **本地 RAG 检索**    | 主力                | 从用户入库的论文中向量检索相关段落，作为回答依据；支持全库检索与定向单篇检索  |
| **本地论文 ID 查找** | 前置                | 将模糊描述（部分标题、作者、年份）转换为精确 doc_id，供定向 RAG 使用          |
| **Semantic Scholar** | 主力                | 外部检索首选，提供引用数、venue、tldr 等丰富元数据                            |
| **OpenAlex**         | 摘要补全 / 一级备用 | S2 摘要缺失时批量补全；S2 不可用时作为一级备用检索（需配置免费的 API Key）    |
| **arXiv**            | 二级备用 / 预印本   | 最终托底；查最新预印本的专用渠道（S2/OpenAlex 对新预印本有数周收录延迟）      |
| **Jina 全文阅读**    | 深度阅读            | 摘要不足时读取 PDF 全文；支持「读头部截断」与「全文分片打分定向召回」两种模式 |

### 工程层

| 功能               | 说明                                                                          |
| ------------------ | ----------------------------------------------------------------------------- |
| **前端系统配置**   | 在配置页填入 Key 和 Base URL，点击「获取模型」后选择确认，重启后生效          |
| **论文库管理**     | 已入库论文支持关键词搜索、排序、入库状态可视化、删除（磁盘 + 向量库同步清理） |
| **系统托盘**       | 最小化到托盘，右键可重启或退出                                                |
| **多会话管理**     | 新建、切换、重命名、删除会话，数据持久化到 SQLite                             |
| **消息树与分支**   | 重新生成与编辑消息均保留历史分支，可在版本间切换（`‹ 1/2 ›` 控件）            |
| **主题与字体**     | 支持日间 / 夜间配色切换，多套字体可选                                         |
| **后端不可用遮罩** | 后端断开时前端显示全局遮罩并提示用户操作                                      |

---

## 技术架构

```
┌─────────────────────────────────────────────┐
│                  Vue 3 前端                   │
│  对话框 · 论文管理 · 系统配置 · 会话管理          │
│  KaTeX 公式渲染 · Markdown · 代码块高亮          │
│  日间/夜间主题 · 字体切换 · 双语引用开关           │
└──────────────────┬──────────────────────────┘
                   │ HTTP（同端口，相对路径 API）
┌──────────────────▼──────────────────────────┐
│              FastAPI 后端                     │
│  路由层 · 会话管理 · 文件处理 · 配置管理          │
│  托管 Vue3 构建产物（dist/）                    │
└──────────────────┬──────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           LangGraph Agent                    │
│                                              │
│  ┌─────────┐   ┌──────────────────────────┐ │
│  │  主 LLM  │   │  工具集（CoT 驱动调用）    │ │
│  │(OpenAI  │   │  RAG · S2 · OpenAlex     │ │
│  │ 兼容格式) │   │  arXiv · Jina            │ │
│  └─────────┘   └──────────────────────────┘ │
│                                              │
│  ┌──────────────┐   ┌──────────────────────┐ │
│  │   ChromaDB   │   │       SQLite          │ │
│  │  (向量检索)   │   │ 会话·消息·论文注册表   │ │
│  └──────────────┘   └──────────────────────┘ │
└─────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────┐
│           外部 API                            │
│  主 LLM（DeepSeek / Gemini / 任意 OpenAI 兼容）│
│  副 LLM（Jina 全文打分专用，建议选低价大上下文）   │
│  硅基流动 Embedding（BAAI/bge-m3，免费）        │
│  Jina Reader · arXiv · S2 · OpenAlex        │
└─────────────────────────────────────────────┘
```

### Agent 工作流程

```
用户消息
  → LangGraph：进入 LLM 节点，CoT 分析意图 + 判断信息缺口
  → 若需要工具：跳转 Tool 节点执行，结果返回 LLM 节点
  → LLM 节点再次 CoT 判断：信息是否足够？是否继续调用工具？
  → 循环（LLM ↔ Tool），最多 6 次工具调用后强制跳转回答
  → 每次工具调用必须附 CoT，无 thinking 则拒绝调用
  → LLM 生成回答：凡依赖外部依据均添加行内角标
  → 前端渲染角标 + 页脚 Reference 区，打字机效果逐字展示

注：工具调用结果仅在本轮 graph 循环内可见。
    用户下一条消息触发新一轮循环，工具结果若未被 Agent 写入回答则不会延续。
```

---

## 项目结构

```
physics-scholar/
├── app.py                        # 打包入口，启动 FastAPI + 系统托盘
├── physics_scholar.spec          # PyInstaller 打包配置
├── requirements.txt
├── .env.example                  # 环境变量模板（开发用，优先级高于 yaml）
│
├── src/                          # 后端核心代码
│   ├── main.py                   # FastAPI 应用入口
│   ├── config.py                 # 配置读取（.env 优先 > user_config.yaml）
│   ├── llm.py                    # LLM 实例管理
│   ├── api/
│   │   └── routes.py             # 所有 API 路由
│   ├── core/                     # 文档处理核心
│   │   ├── parser.py             # PDF 解析（pymupdf）
│   │   ├── chunker.py            # 文本分块
│   │   ├── ingestor.py           # 入库（embedding + ChromaDB 写入）
│   │   ├── extractor.py          # 元数据提取
│   │   ├── registry.py           # 论文注册表（SQLite）
│   │   ├── hash_file.py          # 文件去重
│   │   ├── init_SQLite.py        # 数据库初始化
│   │   └── trim_thinking.py      # 裁剪 LLM 思维链输出
│   ├── rag/                      # RAG 与 Agent 核心
│   │   ├── graph.py              # LangGraph Agent 图定义
│   │   ├── chain.py              # 对话链
│   │   ├── retriever.py          # 向量检索
│   │   ├── memory.py             # 多轮对话记忆
│   │   ├── prompt.py             # Prompt 入口
│   │   ├── prompts/              # 模块化 Prompt 系统
│   │   │   ├── builder.py        # Prompt 组装器
│   │   │   ├── plugins.py        # 插件注册
│   │   │   ├── profiles/         # 模式配置（normal / discuss / debug）
│   │   │   └── modules/          # Prompt 模块
│   │   │       ├── shared/       # 通用模块（角色、约束、引用格式等）
│   │   │       ├── normal/       # 标准问答模式
│   │   │       └── discuss/      # 学术讨论模式
│   │   └── tools/                # Agent 工具
│   │       ├── rag_tool.py                # 本地 RAG 检索
│   │       ├── arxiv_tool.py              # arXiv 论文检索
│   │       ├── s2_tool.py                 # Semantic Scholar 检索
│   │       ├── openalex_tool.py           # OpenAlex 检索
│   │       ├── jina_tool.py               # Jina 全文阅读
│   │       └── lookup_local_paper_id.py   # 本地论文 ID 查找
│   └── utils/
│       └── logger.py             # 日志配置
│
├── frontend/                     # Vue 3 + Vite 前端
│   ├── src/
│   │   ├── api/                  # 后端请求封装
│   │   ├── components/
│   │   │   ├── Chat/             # 对话界面组件
│   │   │   ├── Paper/            # 论文管理组件
│   │   │   ├── Settings/         # 设置页组件
│   │   │   └── Sidebar/          # 侧边栏组件
│   │   ├── store/                # 全局状态管理
│   │   ├── views/                # 页面视图
│   │   └── utils/                # 工具函数
│   └── public/
│       └── favicon.svg
│
├── config/
│   └── user_config.yaml.example  # 用户配置模板（生产/打包用）
│
├── data/                         # 运行时数据（不入版本库）
│   ├── pdfs/                     # 用户上传的论文原文
│   ├── chroma_db/                # 向量数据库
│   └── SQLite/                   # 论文注册表数据库
│
├── seed_builder/                 # 种子库构建脚本（开发用）
├── eval_framework/               # 评测框架（开发用）
└── tests/                        # 自动化测试
```

---

## 快速开始（开发环境）

### 环境要求

- Python 3.10+
- Node.js 18+
- API Key：任意 OpenAI 兼容格式的 LLM 服务商；硅基流动（[注册地址](https://siliconflow.cn)，BAAI/bge-m3 免费）

### 后端启动

```bash
# 克隆仓库
git clone https://gitee.com/a-leaf-boat-is-light/physics-scholar.git
cd physics-scholar

# 安装依赖
pip install -r requirements.txt

# 配置（二选一）
# 方式一：.env 文件（开发推荐，优先级更高）
cp .env.example .env          # 编辑 .env 填入 Key

# 方式二：yaml 配置（用户部署 / 打包分发用）
cp config/user_config.yaml.example config/user_config.yaml

# 启动（默认端口 8000）
uvicorn src.main:app --reload
```

### 前端启动（开发模式）

```bash
cd frontend
npm install
npm run dev
# vite.config.js 中 proxy 已配置指向 :8000，无需额外设置
```

### 前端构建（生产模式）

```bash
cd frontend
npm run build
# 构建完成后重启后端，dist/ 产物由 FastAPI 统一托管
# 访问 http://localhost:8000 即可，无需单独启动前端
```

---

## 配置说明

配置项在前端**系统配置**页中填写，保存并**重启程序**后生效。

| 配置项         | 说明                                                 | 是否必填 |
| -------------- | ---------------------------------------------------- | -------- |
| LLM API Key    | 主模型 Key，支持任意 OpenAI 兼容服务                 | ✅       |
| LLM Base URL   | 服务商 API 地址，如 `https://api.deepseek.com`       | ✅       |
| LLM 模型       | 点击「获取模型」后从下拉框选择                       | ✅       |
| 副 LLM（可选） | 专用于 Jina 全文分片打分，建议选低价、大上下文的模型 | ❌       |
| 硅基流动 Key   | 用于 Embedding，注册即得，bge-m3 免费                | ✅       |
| Jina Key       | 提高 Jina 读取的 RPM 限额（不填可工作，20 RPM）      | ❌       |
| S2 Key         | 提高 Semantic Scholar 调用频率（不填走公共池）       | ❌       |
| OpenAlex Key   | OpenAlex 免费 API Key，新版官方推荐配置              | 💡推荐   |
| OpenAlex 邮箱  | 旧版用于加入礼貌池提升速度，不填也可正常使用         | ❌       |

> ⚠️ **更换 Embedding 模型会导致已入库的向量全部失效**，需重新上传所有论文。当前版本固定使用 BAAI/bge-m3，不开放模型选择，以避免此问题。

---

## 打包分发

使用 PyInstaller onedir 模式打包，用户解压即用：

```bash
pyinstaller physics_scholar.spec
```

分发包结构：

```
PhysicsScholar/
├── PhysicsScholar.exe    # 双击启动
└── _internal/
    ├── ...               # Python 运行时与依赖
    └── data/             # 数据目录（ChromaDB + SQLite + PDFs）
```

启动后系统托盘出现图标，默认浏览器自动打开 `http://localhost:57321`。
首次使用请参阅 [用户手册](docs/用户手册.md) 完成配置。

---

## 设计说明

**从 LangChain 迭代到 LangGraph**

项目初期使用 LangChain Chain 实现对话逻辑，数据存储用 JSON。随着工具数量增加、需要串行多工具并在每步判断是否继续调用，Chain 的线性结构开始显得力不从心，随即迁移至 LangGraph，数据存储也同步迁移到 SQLite。图结构带来的核心优势是：LLM 节点与 Tool 节点之间的循环关系在图定义中一目了然，工具调用次数的硬限制、强制 CoT 校验、超限后跳转回答节点——这些控制逻辑都能在图层面显式表达，而非散落在回调函数里。

**外部检索工具的分级设计**

三个检索工具不是对等的备选，而是有明确分工的分级链路：S2 为主力（引用数、venue、tldr 等元数据最丰富）；OpenAlex 专注摘要补全（覆盖率高于 S2，摘要缺失时批量补全），S2 不可用时升级为检索入口；arXiv 作为最终托底，同时是查询最新预印本的专用渠道（S2/OpenAlex 对新预印本均有数周收录延迟）。降级链路在 Prompt 中显式定义，Agent 遵循由粗到精、信息足够即停止的原则。

**本地论文 ID 工具的作用**

`lookup_local_paper_id` 允许 Agent 将用户对论文的模糊描述（部分标题、作者、年份等）转换为精确 doc_id，再传给 RAG 工具做定向单篇检索。这使得「专注讨论某一篇论文」成为可能：Agent 先锁定目标，再在其范围内检索，避免多论文场景下的向量检索噪音。

**模式切换的实现方式**

Normal 模式与 Discuss 模式由前端开关驱动，后端根据前端传入的模式参数选择对应的 Prompt 模块组装系统提示词。双语引用的开关同理。这样的设计使模式差异完全由 Prompt 层承载，图结构无需因此分叉。

**Embedding 模型为何不开放选择**

向量库与 Embedding 模型强绑定，换模型必须重新入库。为避免用户误操作导致数据失效，当前版本固定使用 BAAI/bge-m3，不开放选择，后续版本可考虑加入迁移机制。

**从本地 sentence-transformers 换成硅基流动 API**

本地 torch 依赖导致打包体积数 GB，启动慢，对非技术用户体验极差。BAAI/bge-m3 通过硅基流动 API 调用效果相当，包体积大幅缩减，且注册免费。

---

## 路线图

- [x] PDF 解析入库（M2）
- [x] RAG 问答 + 双语引用（M3）
- [x] LangGraph Agent + 多工具分级调用（M6）
- [x] 学术讨论模式（M8）
- [x] Vue3 前端 + 多会话管理（M9）
- [x] 错误处理与鲁棒性：入库原子回滚、指数退避、429 熔断、LLM 自动重试（M10）
- [x] PyInstaller exe 打包（M11）
- [ ] 种子论文库（微波光子学领域精选，构建中）

---

## 扩展性说明

Prompt 体系采用模块化设计，`src/rag/prompts/modules/` 下各模块（角色定位、约束、引用格式、领域知识框架等）独立维护，与工具逻辑解耦。如需适配其他研究领域，替换领域知识模块即可，无需改动工具链。（注：少量 prefill 逻辑散落在 `graph.py` 中，如调整思维链结构需一并检查。）

欢迎 Fork 后二次开发。

---

## 说明

本项目为个人独立开发的工程作品。  
仓库公开供学习、研究与作品集展示使用。
