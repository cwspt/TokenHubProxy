# 项目交接说明

## 背景

这个项目是为了验证 Codex Desktop App 能否通过本地代理接入公司提供的腾讯 TokenHub `glm-5.1`。

已知约束：

- Codex 自定义 provider 当前按 Responses API 请求。
- 公司提供的 TokenHub 地址是 Chat Completions：
  `https://tokenhub.tencentmaas.com/plan/v3/chat/completions`
- 因此不能把 TokenHub 地址直接填到 Codex，需要本项目做协议转换。

## 当前实现

入口文件是 `proxy_app/main.py`。

服务提供：

```text
GET  /health
GET  /v1/models
POST /v1/responses
```

核心行为：

- 校验 Codex -> 代理的 `Authorization: Bearer $CODEX_GLM_PROXY_KEY`
- 使用 `TOKENHUB_API_KEY` 调 TokenHub
- 将 Responses `instructions` 转为 Chat Completions `system` message
- 将 Responses `input` 转为 Chat Completions `messages`
- 将 Responses `tools` 转为 Chat Completions `tools`
- 将 Chat Completions `content` 转回 Responses `message`
- 将 Chat Completions `tool_calls` 转回 Responses `function_call`
- 流式请求会把上游 SSE 转成 Responses 风格 SSE 事件
- `previous_response_id` 用内存保存 1 小时，重启后丢失

## 重要设计决策

### 不记录正文日志

Codex 会把源码、终端输出、文件路径、错误日志等发给模型。代理日志必须只记录元数据，不能记录请求正文或响应正文。
如需定位复杂的上游问题，可以写 `logs\proxy-diagnostics-YYYYMMDD.log` 这种后台诊断日志，但也只能记录结构化摘要，不能记录正文或密钥。

### 工具调用默认关闭

`ENABLE_TOOL_CALLS=false` 是默认值。原因是 TokenHub/GLM-5.1 是否支持 Chat Completions 原生 `tool_calls` 尚未验证。

如果工具调用未验证就直接让 Codex 使用，可能出现模型把工具调用写成普通文本，导致 Codex 无法可靠执行读文件、写文件、命令运行等动作。

### 不做提示词模拟工具调用

不要用“请按 JSON 格式输出工具调用”来替代原生 `tool_calls`。这种方式对 Codex 代码代理不够稳定，失败时还容易产生不可控文本。

### 暂不做生产化

首版只用于本机验证：

- 不做公网暴露
- 不做团队共享
- 不做数据库持久化
- 不做完整限流
- 不做完整审计
- 不做 Docker 强制部署

## 下一步推进顺序

1. 在正式项目目录重新创建 `.venv` 并安装依赖。
2. 设置真实 `TOKENHUB_API_KEY`。
3. 运行：

   ```powershell
   .\.venv\Scripts\python scripts\probe_tokenhub.py
   ```

4. 判断探测结果：

   - 文本和流式都失败：先修 TokenHub 鉴权、模型名或 base URL。
   - 文本成功、工具调用失败：只能验证聊天，不能认为 Codex 可用。
   - 文本、流式、工具调用都成功：设置 `ENABLE_TOOL_CALLS=true`，进入 Codex 验证。

5. 启动代理：

   ```powershell
   .\.venv\Scripts\python -m uvicorn proxy_app.main:app --host 127.0.0.1 --port 8787
   ```

6. 配置 Codex provider。
7. 用 Codex 做三层验证：

   - 普通问答
   - 只读文件任务
   - 临时项目中的小范围代码修改任务

## 已知风险

- Responses API 的完整事件形态很复杂，当前实现覆盖的是 Codex 首版验证所需的核心路径，不是完整 OpenAI Responses API 兼容实现。
- 如果 Codex 发送图片、多模态输入、复杂 reasoning item、内置工具等，目前可能返回 400 或退化为文本描述。
- 如果上游流式 `tool_calls` 的增量格式和 OpenAI Chat Completions 不一致，需要按真实返回调整 `stream_response` 中的解析逻辑。
- 当前 `previous_response_id` 只存在内存里，重启后不可用。
- 当前 `usage` 在流式响应中未从上游最终 chunk 聚合。
- `model_context_window = 64000` 是保守默认，应按公司侧 GLM-5.1 实际上下文窗口调整。

## 维护建议

- 修改协议转换逻辑后，先补 `tests/test_transform.py`。
- 不要把真实 Key 写进仓库。
- 不要打开正文日志作为默认行为。
- 如果需要 DEBUG 正文日志，必须做显式环境变量开关，并默认关闭。
- 如果项目要入 git，保留 `.gitignore`，不要提交 `.venv`、`.env`、`__pycache__`。
- 如果要给团队共用，应先补鉴权、请求大小限制、限流、日志脱敏策略和部署说明。

## 当前验收状态

已经通过：

```text
python -m unittest discover -s tests -v
python -m compileall proxy_app scripts tests
```

尚未通过：

```text
真实 TokenHub 文本探测
真实 TokenHub 流式探测
真实 TokenHub tool_calls 探测
Codex Desktop App 真实接入验证
```
