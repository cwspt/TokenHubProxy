# Responses -> 腾讯 TokenHub Chat Completions 本地代理

这是一个用于验证 **Codex Desktop App 接入公司 GLM-5.1 模型** 的本地 FastAPI 代理。

Codex Desktop App 当前自定义模型提供方使用 `wire_api = "responses"`，也就是会请求 OpenAI Responses API 形态的接口；公司提供的腾讯 TokenHub 地址是 Chat Completions 形态：

```text
https://tokenhub.tencentmaas.com/plan/v3/chat/completions
```

本项目的作用是做一层本地协议适配：

```text
Codex Desktop App
  -> http://127.0.0.1:8787/v1/responses
    -> https://tokenhub.tencentmaas.com/plan/v3/chat/completions
      -> glm-5.1
```

## 当前状态

已实现：

- `GET /health`
- `GET /v1/models`
- `POST /v1/responses`
- Responses `input` / `instructions` / `tools` 到 Chat Completions `messages` / `tools` 的核心转换
- 非流式 Responses JSON 返回
- 流式 SSE 返回，映射为 Responses 风格事件
- Chat Completions `tool_calls` 到 Responses `function_call` 的核心转换
- `previous_response_id` 的 1 小时内存兼容
- 只记录元数据的日志策略，不记录 prompt、代码、终端输出、模型正文
- TokenHub 能力探测脚本
- 基础转换单元测试

尚未完成真实上游验证：

- 还没有使用真实 `TOKENHUB_API_KEY` 跑 TokenHub 探测。
- `glm-5.1` 是否原生支持 Chat Completions `tool_calls` 需要用 `scripts/probe_tokenhub.py` 确认。
- 如果工具调用探测失败，普通聊天/文本流式仍可验证，但不能认为已经能可靠支撑 Codex 改代码。

## 项目结构

```text
tokenhub_responses_proxy/
  proxy_app/
    main.py                 # FastAPI 代理主体
  scripts/
    probe_tokenhub.py        # TokenHub 文本/流式/tool_calls 探测脚本
  tests/
    test_transform.py        # 协议转换单元测试
  docs/
    HANDOFF.md               # 给下一轮 AI/维护者的交接说明
  AGENTS.md                  # 给 Codex/AI 维护者的项目规则
  requirements.txt
  README.md
```

## 安装

建议移动到正式项目目录后重新创建虚拟环境，不要依赖旧目录里的 `.venv`。

```powershell
cd <你的正式项目目录>\tokenhub_responses_proxy
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

## 一键启动

新电脑或新目录可以直接运行启动脚本。脚本会创建 `.venv`、安装依赖、交互配置环境变量、探测 TokenHub 能力，并在探测通过后启动本地代理：

```powershell
cd <你的正式项目目录>\tokenhub_responses_proxy
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\start_proxy.ps1
```

默认行为：

- `TOKENHUB_API_KEY` 只写入当前 PowerShell 进程，不落盘。
- `CODEX_GLM_PROXY_KEY` 会写入当前 PowerShell 进程，并持久化到 Windows 用户环境，方便 Codex Desktop App 读取。
- 如果 TokenHub 的非流式和流式 `tool_calls` 探测都通过，脚本会自动设置 `ENABLE_TOOL_CALLS=true`。
- 脚本最后会启动 `uvicorn`，保持该 PowerShell 窗口打开即可使用代理。

如果不希望持久化 `CODEX_GLM_PROXY_KEY`，可以使用：

```powershell
.\scripts\start_proxy.ps1 -NoPersistCodexKey
```

如果需要重新安装依赖：

```powershell
.\scripts\start_proxy.ps1 -ForceInstall
```

## WPF 启动器

如果希望用图形界面操作，可以运行 WPF 启动器：

```powershell
dotnet run --project .\tools\TokenHubProxyLauncher\TokenHubProxyLauncher.csproj
```

启动器提供：

- 创建 `.venv` / 安装依赖
- 输入 `TOKENHUB_API_KEY`
- 生成并持久化 `CODEX_GLM_PROXY_KEY`
- 探测 TokenHub 文本、流式和 `tool_calls`
- 根据探测结果启用工具调用
- 启动、停止本地代理
- 健康检查
- 打开 `%USERPROFILE%\.codex\config.toml`
- 使用独立 `CODEX_HOME` 启动 Codex Desktop 或 VS Code

WPF 启动器不会把 `TOKENHUB_API_KEY` 保存到文件或用户环境变量；它只传给当前启动的探测进程和代理进程。`CODEX_GLM_PROXY_KEY` 默认会写入 Windows 用户环境，方便 Codex Desktop App 读取。

发布可拷贝到新电脑的目录：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\publish_launcher.ps1
```

默认输出目录：

```text
dist\TokenHubResponsesProxyLauncher
```

把这个目录整体复制到新电脑后，运行：

```text
RunLauncher.cmd
```

目标机器需要：

- Windows
- Python 3.11+，且 `python` 在 PATH 中
- .NET 8 Desktop Runtime；如果使用 `-SelfContained` 发布则不需要预装 .NET 运行时
- 能访问 pip 依赖源和 TokenHub

如果希望把 .NET 运行时也打进发布目录：

```powershell
.\scripts\publish_launcher.ps1 -SelfContained
```

## 环境变量

不要把真实 Key 写入代码、README、`.env` 或提交到仓库。建议在启动代理的 PowerShell 窗口里临时设置：

```powershell
$env:TOKENHUB_API_KEY = "公司提供的 TokenHub Key"
$env:TOKENHUB_BASE_URL = "https://tokenhub.tencentmaas.com/plan/v3/chat/completions"
$env:TOKENHUB_MODEL = "glm-5.1"
$env:CODEX_GLM_PROXY_KEY = "本机代理访问密钥"
$env:PROXY_HOST = "127.0.0.1"
$env:PROXY_PORT = "8787"
$env:PROXY_REQUEST_TIMEOUT_SECONDS = "600"
$env:PROXY_MAX_CONTEXT_CHARS = "200000"
$env:PROXY_MAX_CONTEXT_MESSAGES = "600"
$env:PROXY_MAX_CONTEXT_TOOL_CALLS = "250"
$env:PROXY_CONTEXT_REPAIR_HARD_LIMIT_MULTIPLIER = "4"
```

工具调用默认关闭，必须先探测：

```powershell
$env:ENABLE_TOOL_CALLS = "false"
```

也可以使用脚本交互式新增或更新这些环境变量：

```powershell
.\scripts\configure_env.ps1
```

默认只配置当前 PowerShell 进程，关闭窗口后失效。这个模式适合放置 `TOKENHUB_API_KEY`，避免把真实上游 Key 长期写入 Windows 用户环境。

如果需要让 Codex Desktop App 从桌面启动后也能读取本地代理 Key，可以只额外持久化 `CODEX_GLM_PROXY_KEY`：

```powershell
.\scripts\configure_env.ps1 -PersistCodexKey
```

当脚本自动生成新的 `CODEX_GLM_PROXY_KEY` 时，会在 PowerShell 里输出该值，方便复制或核对 Codex 配置。`TOKENHUB_API_KEY` 不会被输出。

脚本会自动清理输入 Key 两端的空白和包裹引号；如果 `TOKENHUB_API_KEY` 误填了 `Bearer ` 前缀，也会自动去掉。输入公司 TokenHub Key 时仍建议只粘贴 Key 本体。

如果明确要把所有变量写入当前 Windows 用户环境，可以使用：

```powershell
.\scripts\configure_env.ps1 -Scope User
```

注意：`-Scope User` 也会持久化 `TOKENHUB_API_KEY`，只建议在你接受这个本机安全取舍时使用。

## 探测 TokenHub 能力

先确认文本、流式、工具调用是否可用：

```powershell
.\.venv\Scripts\python scripts\probe_tokenhub.py
```

探测输出会包含：

```text
non_stream_text
stream_text
non_stream_tool_calls
stream_tool_calls
```

如果 `non_stream_tool_calls` 和 `stream_tool_calls` 都是 `PASS`，再启用工具调用：

```powershell
$env:ENABLE_TOOL_CALLS = "true"
```

如果工具调用探测失败，保持：

```powershell
$env:ENABLE_TOOL_CALLS = "false"
```

这时代理仍可做文本验证，但 Codex 代码代理能力不可靠。

上下文保护说明：

- 当请求历史轻微超过 `PROXY_MAX_CONTEXT_*` 时，代理会优先裁掉最旧的工具调用/工具输出块，并尽量保留最近的工具结果，减少模型重复读取同一批文件的概率。
- 当请求历史超过 `PROXY_MAX_CONTEXT_*` 的 `PROXY_CONTEXT_REPAIR_HARD_LIMIT_MULTIPLIER` 倍时，代理会直接返回 `context_length_exceeded`，让 Codex 触发压缩上下文或提示新开会话。
- 如果确实要关闭这个硬刹车，可以把 `PROXY_CONTEXT_REPAIR_HARD_LIMIT_MULTIPLIER` 设为 `0`，但不建议长期这样做。

## 启动代理

```powershell
.\.venv\Scripts\python -m uvicorn proxy_app.main:app --host 127.0.0.1 --port 8787
```

健康检查：

```powershell
curl.exe http://127.0.0.1:8787/health
```

带本地代理 Key 查看模型列表：

```powershell
curl.exe http://127.0.0.1:8787/v1/models `
  -H "Authorization: Bearer $env:CODEX_GLM_PROXY_KEY"
```

非流式 Responses 测试：

```powershell
curl.exe -s http://127.0.0.1:8787/v1/responses `
  -H "Authorization: Bearer $env:CODEX_GLM_PROXY_KEY" `
  -H "Content-Type: application/json" `
  -d "{\"model\":\"glm-5.1\",\"input\":\"ping\",\"stream\":false}"
```

## Codex Desktop App 配置

确认代理可用后，把下面配置加入 `C:\Users\Oliver\.codex\config.toml`。如需保留现有 provider，先备份原配置。

```toml
model_provider = "glm_tokenhub_proxy"
model = "glm-5.1"
model_reasoning_effort = "medium"
model_verbosity = "medium"
model_context_window = 64000
model_auto_compact_token_limit = 48000
model_max_output_tokens = 8192

[model_providers.glm_tokenhub_proxy]
name = "GLM 5.1 via Tencent TokenHub Proxy"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
env_key = "CODEX_GLM_PROXY_KEY"
stream_idle_timeout_ms = 300000
stream_max_retries = 3
request_max_retries = 2
```

不要配置：

```toml
requires_openai_auth = true
```

本项目使用 `env_key = "CODEX_GLM_PROXY_KEY"` 做 Codex -> 本地代理鉴权；代理再用 `TOKENHUB_API_KEY` 调 TokenHub。

## 分离 Codex 配置目录实验

Codex Desktop App 和 VS Code 插件通常会读取同一个用户级 `.codex` 配置目录。如果需要测试两边使用不同 provider，可以通过启动脚本设置不同的 `CODEX_HOME`。

启动 Codex Desktop，使用 `%USERPROFILE%\.codex-desktop`：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\launch_codex_desktop_with_home.ps1
```

启动 VS Code，使用 `%USERPROFILE%\.codex-vscode`：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
.\scripts\launch_vscode_with_codex_home.ps1
```

脚本会自动创建对应目录，并在缺少 `config.toml` 时生成一份指向本地代理的 `glm_tokenhub_proxy` 配置。之后分别编辑：

```text
%USERPROFILE%\.codex-desktop\config.toml
%USERPROFILE%\.codex-vscode\config.toml
```

让两个文件选择不同的 `model_provider` 即可测试是否隔离生效。

如果无法自动找到程序路径，可以显式传入：

```powershell
.\scripts\launch_codex_desktop_with_home.ps1 -CodexDesktopPath "C:\Path\To\Codex.exe"
.\scripts\launch_vscode_with_codex_home.ps1 -CodePath "C:\Path\To\Code.exe"
```

注意：启动前应完全退出已有的 Codex Desktop 或 VS Code。VS Code 尤其容易复用后台进程，如果复用了旧进程，新设置的 `CODEX_HOME` 可能不会生效。

## 本地测试

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m compileall proxy_app scripts tests
```

当前已验证通过：

```text
5 个转换单元测试通过
compileall 通过
FastAPI app 可导入
/health 可返回 ok
/v1/models 本地鉴权正常
```

## 日志与安全

代理只记录这些元数据：

- request id
- model
- stream 开关
- 状态
- HTTP 状态码
- 耗时

代理不会记录：

- prompt 正文
- 源码正文
- 终端输出正文
- tool output 正文
- 模型响应正文
- TokenHub Key
- Codex 本地代理 Key

如果需要排查复杂的上游错误，可以看 `logs\proxy-diagnostics-YYYYMMDD.log`。这个文件只写结构化元数据，比如消息数量、工具调用数量、状态码、耗时和修复结果，不写正文。

## 给下一轮维护者

新的 AI 会话或维护者应先读：

1. `README.md`
2. `docs/HANDOFF.md`
3. `AGENTS.md`
4. `proxy_app/main.py`
5. `tests/test_transform.py`

下一步最关键的工作不是继续堆功能，而是用真实公司 Key 跑 `scripts/probe_tokenhub.py`，确认 TokenHub 的 `glm-5.1` 是否支持原生 `tool_calls`。这个结果决定 Codex Desktop App 能否真正用于读文件、改文件、运行命令这类代码代理任务。
