using System.Windows;

namespace TokenHubProxyLauncher;

public partial class HelpWindow : Window
{
    private readonly string _language;

    public HelpWindow(string language = "zh")
    {
        _language = language;
        InitializeComponent();
        ApplyLanguage();
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e)
    {
        Close();
    }

    private void ApplyLanguage()
    {
        var zh = _language != "en";
        Title = zh ? "帮助" : "Help";
        TitleTextBlock.Text = zh ? "如何使用启动器" : "How to use this launcher";
        SubtitleTextBlock.Text = zh
            ? "本启动器用于配置、探测并运行 Codex 使用的本地 Responses 到 TokenHub 代理。"
            : "This launcher configures and runs the local Responses-to-TokenHub proxy used by Codex.";
        FirstTimeHeadingTextBlock.Text = zh ? "首次使用" : "First-time setup";
        FirstTimeTextBlock.Text = zh
            ? "1. 确认“项目根目录”指向 TokenHubResponsesProxy 文件夹。\n2. 输入 TOKENHUB_API_KEY。只粘贴 Key 本体，不要带 Bearer 前缀；该 Key 不会保存到磁盘。\n3. 如果 CODEX_GLM_PROXY_KEY 为空，点击“生成”。建议保持“写入 Windows 用户环境”勾选，方便 Codex 读取。\n4. 点击“创建 .venv / 安装依赖”。\n5. 点击“探测 TokenHub”。如果全部检查通过，工具调用会自动启用。\n6. 点击“启动代理”，并保持本窗口打开。"
            : "1. Confirm Project root points to the TokenHubResponsesProxy folder.\n2. Enter TOKENHUB_API_KEY. Paste only the key body, not the Bearer prefix. The key is not saved to disk.\n3. Click Generate if CODEX_GLM_PROXY_KEY is empty. Keep Persist CODEX_GLM_PROXY_KEY checked so Codex can read it.\n4. Click Create .venv / Install dependencies.\n5. Click Probe TokenHub. If all checks pass, tool calls will be enabled automatically.\n6. Click Start proxy and keep this window open.";
        ConfigHeadingTextBlock.Text = zh ? "Codex 配置" : "Codex configuration";
        ConfigIntroTextBlock.Text = zh
            ? "Codex 配置应把本地代理作为 Responses provider。点击“打开配置”可编辑 %USERPROFILE%\\.codex\\config.toml。"
            : "Your Codex config should use the local proxy as a Responses provider. Use Open config to edit %USERPROFILE%\\.codex\\config.toml.";
        ConfigTextBox.Text = """
model_provider = "glm_tokenhub_proxy"
model = "glm-5.1"
model_reasoning_effort = "medium"
model_verbosity = "medium"
model_context_window = 64000
model_max_output_tokens = 8192

[model_providers.glm_tokenhub_proxy]
name = "GLM 5.1 via Tencent TokenHub Proxy"
base_url = "http://127.0.0.1:8787/v1"
wire_api = "responses"
env_key = "CODEX_GLM_PROXY_KEY"
stream_idle_timeout_ms = 300000
stream_max_retries = 3
request_max_retries = 2
""";
        DailyHeadingTextBlock.Text = zh ? "日常使用" : "Daily use";
        DailyTextBlock.Text = zh
            ? "1. 启动本工具。\n2. 输入 TOKENHUB_API_KEY。\n3. 如果换了 Key、URL、模型或网络环境，先点击“探测 TokenHub”。\n4. 点击“启动代理”。\n5. 打开或重启 Codex Desktop / VS Code，让它们读取 CODEX_GLM_PROXY_KEY。"
            : "1. Start this launcher.\n2. Enter TOKENHUB_API_KEY.\n3. Click Probe TokenHub if you changed the key, URL, model, or network.\n4. Click Start proxy.\n5. Open or restart Codex Desktop / VS Code so they can read CODEX_GLM_PROXY_KEY.";
        CodexHomeHeadingTextBlock.Text = zh ? "隔离 CODEX_HOME 启动" : "Isolated CODEX_HOME launchers";
        CodexHomeTextBlock.Text = zh
            ? "“启动桌面端”和“启动 VS Code”可以用不同的 CODEX_HOME 启动客户端。如果 .codex-desktop 或 .codex-vscode 不存在，启动器会创建目录并写入一份指向本地代理的 config.toml。已有 config.toml 不会被覆盖。"
            : "Launch Desktop and Launch VS Code can start clients with separate CODEX_HOME folders. If .codex-desktop or .codex-vscode does not exist, the launcher creates it and writes a proxy-ready config.toml. Existing config.toml files are not overwritten.";
        LogsHeadingTextBlock.Text = zh ? "日志" : "Logs";
        LogsTextBlock.Text = zh
            ? "日志默认跟随最新一行。如果你向上滚动，自动滚动会暂停，方便查看旧日志；滚回底部后会恢复。选中若干行后点击“复制日志”只复制选中内容；未选中时复制全部日志。“清空日志”会删除当前日志。"
            : "The log follows the newest line while it is at the bottom. If you scroll up, auto-scroll pauses so you can inspect old logs. Scroll back to the bottom to resume. Select lines and click Copy log to copy only the selection; with no selection, Copy log copies all logs. Clear log removes current log lines.";
        TroubleshootingHeadingTextBlock.Text = zh ? "排错" : "Troubleshooting";
        TroubleshootingTextBlock.Text = zh
            ? "探测时 HTTP 401 通常表示 TOKENHUB_API_KEY、URL、模型名或账号权限不正确。\n依赖安装失败时，请检查 Python 是否在 PATH 中，以及 pip 是否能访问依赖源。\nCodex 报 unauthorized 时，请确认 CODEX_GLM_PROXY_KEY 已写入用户环境，并重启 Codex。\n如果隔离 CODEX_HOME 没生效，请先完全退出 Codex Desktop 或 VS Code，再从本工具启动。"
            : "HTTP 401 during probe means TOKENHUB_API_KEY, URL, model, or account permission is wrong.\nIf dependencies fail to install, check Python is available in PATH and pip can reach package indexes.\nIf Codex says unauthorized, verify CODEX_GLM_PROXY_KEY is persisted and restart Codex.\nIf Codex Desktop or VS Code does not use isolated CODEX_HOME, fully quit the app first, then launch it from this tool.";
        PresetHeadingTextBlock.Text = zh ? "上游预设" : "Upstream presets";
        PresetTextBlock.Text = zh
            ? "当前启动器内置了 TokenHub / GLM-5.1，以及 DeepSeek 的 deepseek-chat、deepseek-reasoner、deepseek-v3、deepseek-v4-flash、deepseek-v4-pro 预设。切换预设会自动填充 Base URL、模型名和超时时间，但你仍然可以手工修改。DeepSeek 预设使用 https://api.deepseek.com；具体模型是否可用取决于 DeepSeek 当前开放状态和你的账号权限。"
            : "This launcher includes TokenHub / GLM-5.1 plus DeepSeek presets for deepseek-chat, deepseek-reasoner, deepseek-v3, deepseek-v4-flash, and deepseek-v4-pro. Switching a preset auto-fills Base URL, model name, and timeout, but you can still edit them manually. DeepSeek presets use https://api.deepseek.com; actual model availability depends on DeepSeek's current offering and your account permissions.";
        BaseUrlShortcutHeadingTextBlock.Text = zh ? "Base URL 快捷按钮" : "Base URL shortcuts";
        BaseUrlShortcutTextBlock.Text = zh
            ? "Base URL 输入框右侧的 TokenHub / DeepSeek 按钮只替换地址，不会改模型名。它们适合临时切换上游地址；如果你想连模型和超时一起改，请用“上游预设”下拉框。"
            : "The TokenHub / DeepSeek buttons to the right of Base URL only replace the URL and do not change the model. Use them for quick upstream URL switching; if you want model and timeout changed together, use the Upstream preset dropdown.";
        SecurityHeadingTextBlock.Text = zh ? "安全" : "Security";
        SecurityTextBlock.Text = zh
            ? "TOKENHUB_API_KEY 不会被本启动器保存。CODEX_GLM_PROXY_KEY 是本地代理访问密钥，可以写入 Windows 用户环境。不要把真实 Key 写入仓库文件、README、脚本或截图。"
            : "TOKENHUB_API_KEY is intentionally not saved by this launcher. CODEX_GLM_PROXY_KEY is a local proxy key and can be persisted to the Windows user environment. Do not put real keys into repository files, README, scripts, or screenshots.";
        CloseButton.Content = zh ? "关闭" : "Close";
    }
}
