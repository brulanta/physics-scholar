# T0 执行 plan:想法 1 的 A 半 + 死代码清理

> 来源:`plan/top-level-progress-log.md` 【六】节 T0(#1 + #2)。纯收益低风险基础设施改造,想法 3 的地基。

## Context

PhysicsScholar 的 prompt 已是声明式拼装引擎(`builder.py` 的 `PromptBuilder` + `build_prompt`),但「YAML 真成可写配置」卡在 4 个硬边界 + 1 个漂移 bug:

- **边界 1**:模块发现是 `modules/__init__.py:13-32` 的 20 行手动 import 登记表,新增模块必须改 .py。
- **边界 2**:mode 是 `Literal["normal","discuss"]` 代码枚举(`builder.py:149`/`modules/__init__.py:72`/`routes.py:361,389`),新增 mode 动 4 处。
- **边界 3**:动态变量注入写死模块名(`builder.py:189-190` `inject("CONTEXT_BLOCK",…)`/`inject("CITATION_FORMAT",…)`),新增动态模块改 builder。
- **边界 4**:`TOOL_DECISION_PLUGIN` 用 f-string 在 `thinking.py(normal/discuss)` 模块顶层 import 期焊死,游离于 YAML 可控范围外。
- **漂移 bug**:`debug.yaml:25` 引用注册表里不存在的 `CITATION_PLUGIN_SLOT`,debug 模式 `apply_config` 抛 KeyError。

A 半修这 5 项,做完 YAML 真正成可写配置,「自由度不够」痛点消大半。**硬约束:零行为变化**——改造后 normal/discuss 的 system prompt 字节级不变(地基改造不是内容改造)。这是想法 3 重写主 agent prompt 的前提:想法 3 要删 `tool_usage.py`/`thinking.py` 里的工具编排细节、`[TOOL_LOOP]` 改名,应能纯 YAML/数据完成,不再动框架代码。

T0#2 顺手清死代码:`retriever.py`(5 行,全仓零外部 import)+ `prompt.py`(兼容层,实测全仓零 .py import)。`chain.py` 保留(决策 §三.13:被信任边界外漂移脚本 import)。

## 事实核对(已逐条实测,2026-07-13)

- `builder.py:113-122` `build()` = 过滤 enabled → 按 order 升序 → join,无硬编码 if/else。`apply_config`(`builder.py:87-109`)按 name 匹配已注册模块,未注册名 → KeyError(`builder.py:98-99`)。`inject`(`builder.py:78-83`)存 `_injections`,build 时 `content.format(**kwargs)`(`builder.py:119-120`)。
- `PromptModule` = dataclass `name, content, enabled=False, order=0`(`builder.py:25-30`)。
- 模块文件导出形态:多数是 `MODULE_NAME = """..."""` 裸字符串,经 `wrap_module(name, module)`(`modules/__init__.py:35-38`)包成 PromptModule。`thinking.py(normal/discuss)` 例外——模块顶层 f-string `{TOOL_DECISION_PLUGIN}` 求值后赋常量,import 期固化。
- `inject()`/`register()`/`apply_config()`/`wrap_module` **全仓仅内部自用**,零外部消费者 → 破坏性 API 变更(`inject`→`set_vars`、删 `wrap_module`)安全,无外部代码会断。
- 占位符现状:模块 content 里只有 `{history}`(`context.py:4`)、`{citation_plugin}`(`citation_format.py:27`)。`TOOL_DECISION_PLUGIN`(`plugins.py:18-44`)含 `[TOOL_LOOP: BEGIN/PENDING/DONE]` 方括号标记,**无任何花括号** → 下沉成 `{tool_decision_plugin}` 占位符后 `.format()` 不会二次解析,字节安全。
- `prompt.py:23-24` 是唯二的 `build_prompt(mode=...)` 无参调用点(`graph.py:552` 带参)。`build_prompt` 的 `tool_decision_plugin` 参数默认值设为 `TOOL_DECISION_PLUGIN` 常量,保 `prompt.py` 无参调用零变化——**这是零行为变化的关键设计点**。
- 三个子包 `__init__.py`(shared/normal/discuss)当前全空 → 目录扫描方案干净落脚。
- spec `physics_scholar.spec:44` 把 src 作 **datas 落盘**(`_MEIPASS/src/rag/prompts/modules/...` 真有 .py),`hiddenimports:168-171` 已声明 4 个子包 → pkgutil 在 frozen 下能用。但隐式依赖 datas 落盘,若未来 spec 改打 PYZ 会静默失效(见风险)。
- 死代码:`retriever.py`(5 行,自身唯一引用是内部拼错的 `restriever`,零外部 import)、`prompt.py`(兼容层,全仓零 .py import,仅在自身 docstring/`__all__`)。spec `:163` `'src.rag.prompt'`、`:164` `'src.rag.retriever'` hiddenimport 是残留。`chain.py` 被 `scripts/verify_rag_chain.py:8`+`tests/test_rag_chain.py:3` import(信任边界外),保留。

## 实现方案

### 边界 1:模块发现改 pkgutil 目录扫描

每个模块文件导出 `module = PromptModule(name=..., content=..., order=NN)`(保留原大写常量,文本字节不变,加一行 `module = ...`)。order 默认值沿用现状 normal.yaml 语义(ROLE_BASE=10, ROLE_*_EXT=15, CONTEXT_BLOCK=20, TOOL_USAGE=30, CITATION_FORMAT=40, THINKING_SHARED=45, CODE_RULES=50, CONSTRAINTS_SHARED=60, CONSTRAINTS_*=65, BOUNDARY_RULES=70, LANGUAGE_POLICY=80, OUTPUT_FORMAT_SHARED=90, THINKING_*=100)——YAML 会覆盖,默认值只管「未在 YAML 出现的模块」落位可预测。

`modules/__init__.py` 改用 pkgutil 遍历子包:

```python
import pkgutil, importlib
from ..builder import PromptModule
from . import shared, normal, discuss

def _scan(pkg, pkg_name: str) -> list[PromptModule]:
    out = []
    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        m = importlib.import_module(f".{pkg_name}.{name}", __name__)
        mod = getattr(m, "module", None)
        if isinstance(mod, PromptModule):
            out.append(mod)
    return out

SHARED_MODULES = _scan(shared, "shared")
_MODE_MODULES = {"normal": _scan(normal, "normal"), "discuss": _scan(discuss, "discuss")}
MODES = tuple(_MODE_MODULES.keys())

def get_shared_modules() -> list[PromptModule]: return SHARED_MODULES
def get_mode_modules(mode: str) -> list[PromptModule]:
    if mode not in _MODE_MODULES:
        raise ValueError(f"Unknown mode: '{mode}'. Must be one of {MODES}")
    return _MODE_MODULES[mode]
```

删 `wrap_module`、删 20 行顶部 import、`_SHARED_MODULES` 改名 `SHARED_MODULES`。新增模块 = 丢个 .py 进子包(导出 `module`),完事。

### 边界 2:mode 从枚举改数据

`_MODE_MODULES` dict 成唯一真相源。`builder.py:149` `build_prompt(mode: str,...)`、`modules/__init__.py:72` `get_mode_modules(mode: str)` 的 `Literal` 改 `str`(校验逻辑保留)。`routes.py:361,389` 保留 `Literal`(FastAPI schema 校验需要动态 Literal 不被良好支持),加注释指向真相源 `_MODE_MODULES.keys()`。`graph.py:552` 的 `mode="normal" if mode=="normal" else "discuss"` 归一化不动(防御性兜底,与解耦无关)。

### 边界 3 + 4:统一占位符协议

**边界 4**:`thinking.py(normal/discuss)` 删 f-string 前缀 `f`、删 `from ...plugins import TOOL_DECISION_PLUGIN`、`{TOOL_DECISION_PLUGIN}` 改 `{tool_decision_plugin}` 占位符,加 `module = PromptModule(name="THINKING_NORMAL/DISCUSS", content=..., order=100)`。`plugins.py` 的 `TOOL_DECISION_PLUGIN` 常量保留(作注入值来源)。`thinking_shared.py:16,18` 文本里对 `TOOL_DECISION_PLUGIN` 的纯文本引用不动。

**边界 3**:`PromptBuilder` 把 `_injections` + `inject(name,**kwargs)` 改成统一变量池 `_vars` + `set_vars(**kwargs)`;`build()` 用 `string.Formatter().parse(content)` 自动探测每个模块 content 里的占位符,从变量池取 subset 做 `content.format(**subset)`。无占位符的模块不调 format(零副作用)。`_extract_placeholders` 对 `ValueError` 降级返回空 set(防御字面 `{`)。`build_prompt` 签名加 `tool_decision_plugin: str = TOOL_DECISION_PLUGIN`(默认值=常量,保 `prompt.py` 无参调用零变化),内部 `inject` 两行改 `set_vars(history=..., citation_plugin=..., tool_decision_plugin=...)` 一行。`graph.py:552-556` 补 `tool_decision_plugin=TOOL_DECISION_PLUGIN` 参数 + 顶部 import。

### 漂移 bug

`debug.yaml:25-27` 整条删 `CITATION_PLUGIN_SLOT` 三行。理由:`CITATION_FORMAT` 已在 `debug.yaml:21` 声明(enabled=true),其内部 `{citation_plugin}` 占位符在 debug 模式注入时被填,删 `CITATION_PLUGIN_SLOT` 不丢任何引用功能;改名会对齐成重复声明。`debug.yaml:17-19` 的 `THINKING_SHARED`(enabled=false)合法不动。scope 仅修 KeyError,不扩展「让 build_prompt 支持 debug mode」(那是更大设计)。

### T0#2 死代码清理

- 删 `src/rag/retriever.py`(零外部 import)、`src/rag/prompt.py`(兼容层,零 .py import)。
- 删 `physics_scholar.spec` 的 `'src.rag.prompt'`(`:163`)、`'src.rag.retriever'`(`:164`)两行 hiddenimport。
- **保留** `src/rag/chain.py` + spec `'src.rag.chain'`(`:160`)(决策 §三.13:被信任边界外脚本 import)。

## 关键文件

- `src/rag/prompts/builder.py` — inject→set_vars + 占位符自动探测 + build_prompt 签名加 tool_decision_plugin(默认=常量)+ mode 类型 str
- `src/rag/prompts/modules/__init__.py` — pkgutil 扫描 + MODES 导出 + get_mode_modules 类型 str + 删 wrap_module
- `src/rag/prompts/modules/{shared,normal,discuss}/*.py`(16 个模块文件)— 加 `module = PromptModule(...)` 行,文本不动;thinking 两个改占位符
- `src/rag/graph.py:552-556` — build_prompt 调用补 tool_decision_plugin 参数 + 顶部 import TOOL_DECISION_PLUGIN
- `src/rag/prompts/profiles/debug.yaml` — 删 CITATION_PLUGIN_SLOT 三行
- `src/api/routes.py:361,389` — 两处 Literal 加注释指向真相源
- 删 `src/rag/retriever.py`、`src/rag/prompt.py`;`physics_scholar.spec` 删两行 hiddenimport
- 新增 `tests/test_prompt_byte_equivalence.py` + `tests/fixtures/prompt_baseline_{normal,discuss}.txt`(改造前生成)

**不改**:`plugins.py`、`thinking_shared.py`、`graph.py` 的 mode 归一化、`chain.py`、spec 的 datas/其余 hiddenimports。

## 开工顺序

1. **生成基线 fixtures**(改造前,干净 git):跑脚本 dump normal/discuss 的 `build_prompt` 输出(固定 history + CITATION_DEFAULT + TOOL_DECISION_PLUGIN)存 `tests/fixtures/prompt_baseline_*.txt`。零行为变化断言的黄金基线,必须在改造前生成。
2. **修漂移 bug**:删 `debug.yaml:25-27`。验证 `apply_config(debug.yaml)` 不再 KeyError。
3. **边界 3+4(合并,最关键)**:builder inject→set_vars + 占位符自动探测 + build_prompt 加 tool_decision_plugin(默认=常量);同步改 thinking(normal/discuss)占位符;`graph.py:552` 补参数。验证 normal/discuss 字节相等。
4. **边界 1**:16 个模块文件加 `module = PromptModule(...)`;`modules/__init__.py` 改 pkgutil 扫描。验证字节相等仍通过 + import 烟雾测试。
5. **边界 2**:`builder.py:149`/`modules/__init__.py:72` Literal→str;`routes.py` 加注释。验证 MODES 数据化断言 + 字节相等仍通过。
6. **T0#2 死代码**:删 retriever.py/prompt.py + spec 两行 hiddenimport。验证 `pytest` 无新断 import 报错(信任边界外脚本可能坏,属可接受漂移)。
7. 跑全套验证。

## 验证

- **零行为变化(核心)**:`test_prompt_byte_equivalence.py` 断言 `build_prompt(mode="normal"/"discuss", history="测试历史", citation_plugin=CITATION_DEFAULT, tool_decision_plugin=TOOL_DECISION_PLUGIN)` == 基线 fixtures 字节级相等。
- **debug.yaml 修复**:`PromptBuilder().apply_config("debug.yaml")` 不抛 KeyError。
- **mode 数据化**:`set(MODES) == set(_MODE_MODULES.keys())` 且含 normal/discuss。
- **frozen 兼容烟雾**:`importlib.import_module` 三个子包都有非空 `MODULES`(pkgutil 扫描成功)。
- **死代码清理**:删后 `pytest` 无新断 import 报错(`chain.py` 保留,其消费者脚本不波及)。
- **端到端**:dev 跑 `uvicorn src.main:app`,发一轮 normal + discuss 提问,确认回答正常、引用格式不变(行为零变化的人眼兜底)。

## 风险

- **frozen 兼容(pkgutil)**:spec 把 src 作 datas 落盘,pkgutil 现在能用。但隐式依赖落盘——若未来 spec 改打 PYZ,pkgutil 静默返回空列表、模块全不注册、prompt 变空。兜底:frozen 烟雾测试(上面) + 在 `modules/__init__.py` 顶部加注释「src 必须作 datas 落盘,pkgutil 才能扫到」。是否再加「扫到数 ≥ 预期下限」的断言,开工时定(非阻塞,纯防御)。
- **order 排序稳定性**:pkgutil `iter_modules` 不保证顺序。未在 YAML 出现的模块靠默认 order 落位,多个 order 相同则按 dict 插入序(不稳定)。现状生产路径必走 apply_config(YAML 覆盖 order),实际产出不变;但纯 `PromptBuilder()` 不 apply_config 的场景(如 debug 测试)排序可能漂移。默认 order 给语义性唯一值(见方案)规避。
- **`tool_decision_plugin` 默认值=常量**:是保 `prompt.py:23-24` 零变化的关键。但 T0#2 要删 `prompt.py`——删后该默认值仅服务于「未来无参调用」的兼容性。删 prompt.py 与设默认值不冲突(默认值仍合理,保未来无参调用得到完整 thinking)。

## 状态

- 2026-07-13:plan 落地,开工。
- 2026-07-13:**T0 全部完成**。零行为变化字节级验证通过(normal/discuss == 基线 fixtures);debug.yaml 漂移 bug 修复;mode 数据化(MODES from `_MODE_MODULES`);pkgutil 目录扫描落地(16 模块文件导出 `module`);死代码清理(`retriever.py`/`prompt.py` 删除 + spec 两行 hiddenimport 删除,`chain.py` 保留)。pytest 132 passed,4 failed/errors 全是 pre-existing 漂移(test_backend 起服务环境问题、test_s2_tool `_build_search_query` 断言漂移——均不在 T0 改动面)。frozen 端到端验证通过:打包后 16 模块 .py + 3 yaml 全部落盘 `_MEIPASS/src/rag/prompts/modules/`,exe 启动 discuss 提问回答含完整 ROLE_BASE+ROLE_DISCUSS_EXT 身份信息,pkgutil 扫描+import+拼装链路 frozen 下全程跑通。
- 遗留设计点:`build_prompt` 的 `tool_decision_plugin` 参数默认值=`TOOL_DECISION_PLUGIN` 常量。当初为保 `prompt.py:23-24` 无参调用零变化而设,现 `prompt.py` 已删,理由消失,但默认值保留(更安全的 API:未来无参调用仍得完整 thinking,不会因漏传得到空协议)。无 bug,仅设计理由变更。
