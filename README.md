# AI 代码审查系统

基于多智能体的代码审查系统，使用静态分析工具和 LLM 智能体分析 Git PR 差异。系统采用多智能体工作流模式，通过意图分析、任务管理和专家执行等节点，生成全面的代码审查报告。

## 架构

系统采用模块化、分层架构，具有可扩展的 DAO（数据访问对象）层：

```
dao/             # DAO 层：可扩展的存储后端（文件、SQL、NoSQL 就绪）
assets/          # 资产层：代码分析和索引（AST、RepoMap、CPG）
tools/           # 工具层：MCP 兼容工具，封装资产查询
core/            # 核心层：配置、LLM 客户端和共享状态
agents/          # 智能体层：基于 LangGraph 的多智能体工作流
external_tools/  # 外部工具：语法检查器（pylint、ruff、eslint）
main.py          # 入口点
log/             # 日志目录：智能体观察和工具调用日志
```

### 核心组件

- **DAO 层**：可扩展的存储抽象，支持基于文件的存储（MVP），接口已就绪，可迁移到 SQL/NoSQL 后端
- **资产**：代码分析产物（RepoMap、AST、CPG），通过 DAO 持久化
- **工具**：MCP 兼容工具（FetchRepoMapTool、ReadFileTool），智能体可使用
- **多智能体工作流**：包含 4 个节点的流水线工作流
  - **Intent Analysis**：并行分析变更文件的意图
  - **Manager**：生成任务列表并按风险类型分组
  - **Expert Execution**：并行执行专家组任务（并发控制）
  - **Reporter**：生成最终审查报告
- **语法检查器**：支持 Python（pylint、ruff）和 TypeScript（eslint）的静态分析

## 技术栈

- **Python 3.10+**
- **LangGraph**：使用 StateGraph 和 Nodes 进行智能体编排
- **Pydantic v2**：所有智能体 I/O 和资产模式的数据验证
- **LangChain Core**：用于提示模板、工具和消息处理
- **unidiff**：Git diff 解析和行号映射
- **Tree-sitter**：代码解析（计划在后续版本中实现）

## 安装

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 对于 OpenAI 或 DeepSeek 提供商（可选）：
```bash
pip install openai
```

3. 对于 YAML 配置文件支持（可选）：
```bash
pip install pyyaml
```

4. 安装外部语法检查工具（可选，用于静态分析）：
   
   **Python 检查器：**
   - Ruff（推荐，快速）：
     ```bash
     pip install ruff
     ```
   - 或 Pylint：
     ```bash
     pip install pylint
     ```
   
   **TypeScript/JavaScript 检查器：**
   - 安装 ESLint（全局或本地）：
     ```bash
     # 全局安装
     npm install -g eslint
     
     # 或本地安装（推荐）
     npm install eslint
     ```
   - 对于 TypeScript 支持（可选，如果审查 TypeScript 代码）：
     ```bash
     npm install @typescript-eslint/parser @typescript-eslint/eslint-plugin typescript
     ```
     注意：如果项目已有 `package.json`，这些依赖可能已包含在 `devDependencies` 中。

5. 配置 DeepSeek（如使用）：
   - 在 `~/.zshrc` 中设置 `DEEPSEEK_API_KEY` 环境变量：
     ```bash
     export DEEPSEEK_API_KEY="your-deepseek-api-key"
     ```
   - 或在 `config.yaml` 中配置：
     ```yaml
     llm:
       provider: "deepseek"
       model: "deepseek-chat"
       api_key: "your-api-key"
       base_url: "https://api.deepseek.com"
     ```

## 使用方法

### 命令行接口

使用 Git 分支比较模式：

```bash
# 比较 feature-x 分支与 main
python main.py --repo ./project --base main --head feature-x

# 比较当前 HEAD 与 main
python main.py --repo ./project --base main --head HEAD
```

### 命令行选项

- `--repo`（必需）：要审查的仓库路径
- `--base`（必需）：Git diff 的目标分支（如 'main', 'master'）
- `--head`（必需）：Git diff 的源分支或提交（如 'feature-x', 'HEAD'）
- `--output`：保存审查结果 JSON 的路径（默认：review_results.json）

### 示例：使用 Sentry 仓库测试

```bash
python main.py \
  --repo /Users/wangyue/Code/CodeReviewData/ReviewDataset/sentry-greptile \
  --base performance-optimization-baseline \
  --head performance-enhancement-complete
```

### 编程式使用

```python
import asyncio
from core.config import Config
from agents.workflow import run_multi_agent_workflow

async def review_code():
    config = Config.load_default()
    
    pr_diff = """your git diff here"""
    changed_files = ["file1.py", "file2.py"]
    
    results = await run_multi_agent_workflow(
        diff_context=pr_diff,
        changed_files=changed_files,
        config=config
    )
    
    print(f"发现 {len(results['confirmed_issues'])} 个问题")
    for issue in results['confirmed_issues']:
        print(f"{issue['file_path']}:{issue['line_number']} - {issue['description']}")

asyncio.run(review_code())
```

## 工作流

系统使用多智能体工作流，遵循以下流程：

1. **初始化存储**：初始化 DAO 层（MVP 使用基于文件的存储）
2. **构建资产**：如需要，构建并持久化仓库地图
3. **语法检查**：对变更文件执行静态分析（pylint、ruff、eslint）
4. **意图分析**：并行分析所有变更文件的意图（Map-Reduce 模式）
5. **任务管理**：Manager 节点生成任务列表并按风险类型分组
6. **专家执行**：并行执行专家组任务，使用并发控制
7. **报告生成**：Reporter 节点生成最终审查报告
8. **日志记录**：所有观察和工具调用自动记录到日志文件

### 风险类型

系统识别以下 6 种风险类型：

- **NULL_SAFETY**：空值陷阱与边界防御
- **CONCURRENCY**：并发竞争与异步时序
- **SECURITY**：安全漏洞与敏感数据
- **BUSINESS_INTENT**：业务意图与功能对齐
- **LIFECYCLE**：生命周期与状态副作用
- **SYNTAX**：语法与静态分析

### 智能体自主性

智能体具有完全自主性：
- 如不需要可跳过工具调用
- 重试失败的工具调用（带失败跟踪）
- 接近迭代限制时提供回退审查
- 基于上下文和先前观察做出决策

## 配置

编辑 `config.yaml` 或通过环境变量配置：

```yaml
llm:
  provider: "deepseek"  # 选项: "openai", "deepseek", "mock"
  model: "deepseek-chat"
  api_key: null  # 可通过 DEEPSEEK_API_KEY 或 LLM_API_KEY 环境变量设置
  base_url: "https://api.deepseek.com"
  temperature: 0.7

system:
  workspace_root: "."
  assets_dir: "assets_cache"
  timeout_seconds: 600  # 10 分钟
  max_concurrent_llm_requests: 10
```

环境变量优先级：`LLM_API_KEY` > `DEEPSEEK_API_KEY`（当 provider 为 "deepseek" 时）

## 功能特性

### 核心功能

- ✅ **多智能体工作流**：4 节点流水线（意图分析、管理、专家执行、报告）
- ✅ **可扩展 DAO 层**：基于文件的存储（MVP），接口已就绪，可迁移到 SQL/NoSQL/GraphDB 后端
- ✅ **资产管理**：RepoMap 构建器，自动 DAO 持久化（幂等构建）
- ✅ **MCP 兼容工具**：标准化工具接口（FetchRepoMapTool、ReadFileTool）
- ✅ **语法检查器**：支持 Python（pylint、ruff）和 TypeScript（eslint）
- ✅ **全面日志记录**：自动记录智能体观察和工具调用到结构化日志文件
- ✅ **多 LLM 提供商**：支持 OpenAI、DeepSeek 和 mock 提供商（用于测试）
- ✅ **并发控制**：使用 Semaphore 限制并发 LLM 请求数量
- ✅ **错误处理**：优雅降级，详细错误报告
- ✅ **类型安全**：完整的类型提示和 Pydantic v2 验证

### 日志记录

智能体观察和工具调用自动保存到：
```
log/
  └── {repo_name}/
      └── {model_name}/
          └── {timestamp}/
              ├── observations.log    # 智能体观察和工具调用
              └── expert_analyses.log # 专家分析结果
```

每个日志文件包含：
- 所有智能体观察（推理步骤）
- 所有工具调用（输入参数和结果）
- 专家分析结果
- 元数据（仓库、模型、时间戳）

## 项目结构

```
CodeReview/
├── dao/                    # 数据访问对象层
│   ├── base.py            # BaseStorageBackend 接口
│   ├── factory.py          # 存储工厂（单例模式）
│   └── backends/
│       └── local_file.py   # 基于文件的存储实现
├── assets/                 # 资产构建器
│   ├── base.py             # BaseAssetBuilder 接口
│   ├── registry.py         # 资产注册表
│   └── implementations/
│       └── repo_map.py     # RepoMap 构建器
├── tools/                  # MCP 兼容工具
│   ├── base.py             # BaseTool 接口
│   ├── repo_tools.py       # FetchRepoMapTool
│   ├── file_tools.py       # ReadFileTool
│   └── langchain_tools.py  # LangChain 标准工具
├── agents/                 # 智能体实现
│   ├── workflow.py         # 多智能体工作流
│   ├── prompts/            # 提示词模板
│   │   ├── intent_analysis.txt
│   │   ├── manager.txt
│   │   ├── reporter.txt
│   │   └── expert_*.txt    # 各专家提示词
│   └── nodes/              # 工作流节点
│       ├── intent_analysis.py  # 意图分析节点
│       ├── manager.py           # Manager 节点
│       ├── expert_execution.py  # 专家执行节点
│       └── reporter.py          # 报告生成节点
├── core/                   # 核心工具
│   ├── config.py           # 配置管理
│   ├── llm.py              # LLM 提供商抽象
│   ├── langchain_llm.py    # LangChain LLM 适配器
│   └── state.py            # LangGraph 状态定义
├── external_tools/         # 外部工具
│   └── syntax_checker/     # 语法检查器
│       ├── base.py         # 检查器基类
│       ├── factory.py      # 检查器工厂
│       ├── config_loader.py # 配置加载器
│       └── implementations/
│           ├── python_pylint.py
│           ├── python_ruff.py
│           └── typescript_eslint.py
├── util/                   # 工具函数
│   ├── arg_utils.py        # 参数验证
│   ├── git_utils.py        # Git 操作
│   ├── diff_utils.py       # Diff 解析
│   ├── file_utils.py       # 文件操作
│   ├── pr_utils.py         # PR 处理
│   └── logger.py           # 日志记录
├── log/                    # 智能体日志（自动生成）
├── .storage/               # DAO 存储目录（自动生成）
├── main.py                 # 入口点
├── config.yaml             # 配置文件示例
└── requirements.txt        # 依赖列表
```

## 未来增强

- [ ] Tree-sitter 集成用于 AST 分析
- [ ] 控制流图（CPG）生成
- [ ] SQL/NoSQL/GraphDB 存储后端
- [ ] 真实 GitHub API 集成
- [ ] 代码嵌入的向量存储
- [ ] 高级查询功能
- [ ] 审查结果的 Web UI
- [ ] CI/CD 集成
- [ ] 更多语言支持（Go、Java、Rust 等）

## 开发

### 编码标准

项目遵循严格的编码标准：

- **类型提示**：所有函数和方法必须使用
- **文档字符串**：所有类和公共方法使用 Google 风格（中文）
- **异步 IO**：所有 IO 绑定操作使用 async/await
- **错误处理**：智能体永不崩溃；错误在结果中返回
- **依赖注入**：不硬编码依赖；使用 DI 模式
- **抽象接口**：所有主要组件使用 ABC 接口

### 设计原则

- **高内聚、低耦合**：模块化架构，清晰的边界
- **可扩展性**：易于添加新的存储后端、工具和智能体
- **幂等性**：资产构建和操作是幂等的
- **可观测性**：全面的日志记录用于调试和监控

### 测试

对于无需 API 密钥的测试，使用 mock LLM 提供商：

```python
config = Config(
    llm=LLMConfig(provider="mock")
)
```

## 许可证

[添加您的许可证]
