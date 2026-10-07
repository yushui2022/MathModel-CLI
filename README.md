# MathModel CLI

把数学建模赛题和附件交给固定版本的 Skill，在本地 Linux Docker 容器中运行 Codex，再独立检查计算证据、正式论文和 Word/PDF。YAML 描述任务，CLI 管理运行、恢复与导出。

**当前为 `0.1.0a1` 开发预览版，仅支持 Standard + Codex CLI。** 已实现的自动检查不等于科学结论正确、竞赛获奖或稳定生成优秀论文；尚未完成付费模型端到端赛题验收。Pro / Lite / Flash / LaTeX 以及其他 Agent 引擎未接入。

## 快速开始

需要 Python 3.11+、Git、运行中的 **Linux Docker**。Windows 使用 Docker Desktop 的 Linux containers，并先将 Docker 虚拟磁盘放到空间充足的盘上。主机不需要安装 Codex、LibreOffice 或数学计算依赖，它们在专用镜像内。

```powershell
git clone https://github.com/yushui2022/MathModel-CLI.git G:\Projects\MathModel-CLI
cd G:\Projects\MathModel-CLI
$env:PIP_CACHE_DIR = 'G:\DevCache\pip'
$env:MATHMODEL_HOME = 'G:\Data\MathModel-CLI'
python -m venv .venv
.\.venv\Scripts\python -m pip install -e .
.\.venv\Scripts\mathmodel image build
.\.venv\Scripts\mathmodel doctor
```

Linux/macOS 对应使用 `python3 -m venv .venv`、`.venv/bin/python`、`.venv/bin/mathmodel`；数据目录由 `MATHMODEL_HOME` 指定，默认遵循 XDG。请以普通用户运行，勿使用 root。下文 `mathmodel` 指上述虚拟环境内的可执行文件。

首次构建会下载数 GB 镜像与依赖，不调用模型。`doctor` 也不检查 API Key 或消耗模型额度。

### 1. 创建与检查任务

```powershell
mathmodel init G:\Projects\contest-demo --model YOUR_MODEL_ID
# 将题目 PDF、附件、模板等放入 contest-demo\problem_files\
mathmodel validate G:\Projects\contest-demo\job.yaml
mathmodel prepare G:\Projects\contest-demo\job.yaml
```

把 `YOUR_MODEL_ID` 换成接入服务实际支持的模型 ID。CLI 不依据营销名称猜测 API ID，也不自动切换模型。`engine.effort` 需由该模型与服务支持。

`prepare` 只下载、校验固定 Skill 并制作快照，**不启动 Agent、不读取 API Key**。完全离线时可传入对应固定版本的本地 ZIP：

```powershell
mathmodel prepare G:\Projects\contest-demo\job.yaml --offline --skill-archive G:\Downloads\MathModel-Skill-Codex.zip
```

其他版本 ZIP 会被拒绝，不会偷偷跟随 Skill 分支更新。

### 2. 显式启动模型

先通过本机终端或密钥管理器设置 `OPENAI_API_KEY`，不要把密钥写入 YAML、Git、聊天或截图。首版使用 API Key 与 Responses API，不复用桌面 ChatGPT 登录。

```powershell
mathmodel resume TASK_ID
# 或直接创建新任务并开始（会消耗额度）：
mathmodel run G:\Projects\contest-demo\job.yaml
```

原任务有可恢复的 Codex session 时继续该 session；`--fresh-session` 才会显式新开上下文。API 报错和超时不会自动重试。验收失败最多执行 YAML 指定的修复轮数，每轮都会消耗模型额度。

### 3. 查看、停止与导出

```powershell
mathmodel status
mathmodel status TASK_ID --json
mathmodel logs TASK_ID --lines 30
mathmodel stop TASK_ID
mathmodel verify TASK_ID
mathmodel export TASK_ID --output G:\Archive\contest-result
```

`verify` 不调用模型，但需要 Docker 镜像，会重新检查证据与渲染 PDF。只有 `SUCCEEDED` 且哈希仍匹配的快照可以导出；输出目录必须不存在。导出包含 `paper_output/` 内的 Markdown、DOCX、渲染 PDF、脚本、计算结果及验收报告，另有数据溯源目录与哈希清单。不会导出 API Key、Codex 会话或聊天日志。

## YAML 契约

`init` 会生成完整配置，[示例](examples/job.yaml)与 [JSON Schema](src/mathmodel_cli/resources/job.schema.json)可供查看。

| 字段 | 作用 |
| --- | --- |
| `edition` | 首版固定 `standard`，拒绝未实现版本 |
| `inputs` | 相对 YAML 的附件目录，拒绝绝对路径、`..`、软链接和 junction |
| `engine.model / effort` | 用户明确选择模型与推理档位 |
| `engine.base_url` | HTTPS Responses API 服务，仅支持根路径或 `/v1`，无自定义端口 |
| `engine.api_key_env` | 密钥环境变量名，不是密钥内容 |
| `delivery.target_pages` | 实际 PDF 总页数范围，默认 18–24；赛题页数规则优先，冲突需修改任务 |
| `runtime` | CPU、内存、进程数、单轮超时、每次启动的最大修复轮数 |

YAML 不支持任意命令、宿主目录挂载、自定义验收器、内联密钥。修改已有任务的配置/输入会使其失效，需要创建新任务。默认没有硬性 Token/金额上限；超时与轮次上限不能保证费用上限，另在服务商处设置消费限额。

## 如何工作

```text
job.yaml + problem_files
  -> schema / 路径 / Skill SHA-256 校验
  -> 锁定输入、Skill、配置和容器镜像 ID
  -> Codex 容器执行 Standard S0-S8
  -> 停止 Agent，制作验收快照
  -> 无密钥、无网络的独立容器检查证据 / 写作 / Word / PDF
  -> SUCCEEDED 后导出带哈希的交付物
```

模型只能写结果目录、研究记录目录和自己的会话目录；Skill、原附件、入口指令只读。任务状态、验收快照和宿主日志不挂载给 Agent。证据门禁会重新计算，不直接采信 Agent 的 PASS 报告。页数按实际渲染结果计算，不能靠声明达到“20 页”。

Skill 固定为 [Standard 2.3.0 / 7712876](https://github.com/yushui2022/MathModel-Skill/tree/77128764d65e59f6673ea65e5a8dc2ed85eb9885)，归档 SHA-256 `5d2ac59be5d9473c82b80cb84e99c929dbe9b7485e34ac05587ddffcececa731`。容器固定 Codex CLI `0.160.1`，首次运行记录镜像 ID；镜像变更后旧任务拒绝恢复。依赖范围和基础镜像并非完整供应链锁文件，首次构建目前不保证字节级可复现。

## 安全与边界

- API 密钥只通过 stdin 交给可信网关，不写入 Docker 参数、环境变量、任务文件或 Agent 容器。Agent 持有的是本次任务专用代理令牌。
- 网关只代理配置中固定服务的 Responses 路由和固定模型，拒绝重定向、私网解析与托管网络/代码工具。使用第三方中转前，请自行确认数据处理与计费政策。
- Agent 使用内部 Docker 网络，不开放宿主端口、不挂载 Docker socket；仍需注意 Docker 内部网络并非完整出站防火墙，可能访问宿主网关上的服务。
- 普通 Docker 是进程级隔离，不是恶意多租户专用虚拟机。模型请求会把赛题和相关上下文发送给所选服务；不要放入无权上传的数据。详见 [安全说明](docs/SECURITY.md)。
- 首版不提供公开联网研究，也不支持运行中安装依赖。缺数据、授权或依赖时应阻塞，不得编造。可在开跑前把获授权的资料和来源记录放入附件目录。
- 自动验收不能证明模型最优、统计设计充分、引文真实或论文达到竞赛水平；必须人工复核。不得用重复正文、空图或改变字号凑页数。

## 开发与测试

```powershell
.\.venv\Scripts\python -m pip install -e '.[dev]'
.\.venv\Scripts\python -m pytest -m 'not docker'
.\.venv\Scripts\python -m ruff check .
.\.venv\Scripts\python -m pip check
```

离线测试使用本地 fixture 与假的执行后端，不接入模型、不读取你的密钥。Docker 集成测试需要提前构建镜像，并显式设置 `MATHMODEL_TEST_DOCKER=1`；只做容器权限、Codex 版本及 LibreOffice 渲染检查，不请求模型。CI 覆盖 Windows/Ubuntu × Python 3.11/3.12，并在 Ubuntu 单独执行 Docker 集成测试。

项目借鉴声明式 Agent 工作流与评测思路，但不依赖 Skill-Up，也不将评测框架当作生产安全边界。完整边界见 [架构](docs/ARCHITECTURE.md)。

## 许可证

[MIT](LICENSE) · Copyright (c) 2026 yushui2022
