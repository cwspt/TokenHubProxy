# AI 维护者说明

本项目是本地协议适配代理：把 Codex Desktop App 的 Responses API 请求转换为腾讯 TokenHub Chat Completions 请求。

## 开始前必须阅读

1. `README.md`
2. `docs/HANDOFF.md`
3. `proxy_app/main.py`
4. `tests/test_transform.py`

## 工作规则

- 不要把真实 `TOKENHUB_API_KEY`、`CODEX_GLM_PROXY_KEY` 或任何 Bearer token 写入文件。
- 不要默认记录 prompt、源码、终端输出、tool output、模型响应正文。
- 不要用提示词模拟 tool calls 替代原生 Chat Completions `tool_calls`。
- 修改转换逻辑时，同步补充或更新 `tests/test_transform.py`。
- 优先保持本机验证目标，不要提前引入数据库、队列、复杂部署或团队共享能力。
- 如果需要新增依赖，更新 `requirements.txt` 并说明原因。

## 验证命令

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m compileall proxy_app scripts tests
```

真实上游验证必须由用户在设置好公司 Key 后运行：

```powershell
.\.venv\Scripts\python scripts\probe_tokenhub.py
```
