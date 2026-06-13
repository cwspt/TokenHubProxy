using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using WinForms = System.Windows.Forms;

namespace TokenHubProxyLauncher;

public partial class MainWindow : Window
{
    private Process? _proxyProcess;
    private readonly HttpClient _httpClient = new();
    private ScrollViewer? _logScrollViewer;
    private bool _logAutoScroll = true;
    private bool _isProgrammaticLogScroll;
    private string _language = "zh";
    private CancellationTokenSource? _recoveryMonitorCts;
    private const int MaxLogLines = 2000;
    private static readonly TimeSpan RecoveryMonitorInterval = TimeSpan.FromSeconds(60);
    private readonly List<UpstreamPreset> _upstreamPresets = new();
    private bool _suppressPresetChangeLog;
    private bool _omitForcedToolChoice;

    public MainWindow()
    {
        InitializeComponent();

        var projectRoot = FindProjectRoot();
        ProjectRootTextBox.Text = projectRoot;
        DesktopCodexHomeTextBox.Text = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".codex-desktop");
        VsCodeCodexHomeTextBox.Text = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".codex-vscode");

        var existingProxyKey = Environment.GetEnvironmentVariable("CODEX_GLM_PROXY_KEY", EnvironmentVariableTarget.User);
        CodexProxyKeyTextBox.Text = string.IsNullOrWhiteSpace(existingProxyKey) ? GenerateProxyKey() : existingProxyKey;

        LanguageComboBox.Items.Add("中文");
        LanguageComboBox.Items.Add("English");
        LanguageComboBox.SelectedIndex = 0;
        InitializeResponseLanguageOptions();
        InitializeUpstreamPresets();
        ApplyLanguage();
        ApplySelectedUpstreamPreset(0, logChange: false);

        Closed += (_, _) =>
        {
            StopRecoveryMonitor(writeLog: false);
            StopProxyNonBlocking(writeLog: false);
        };
        LogListBox.Loaded += (_, _) => AttachLogScrollViewer();
    }

    private void LanguageComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        _language = LanguageComboBox.SelectedIndex == 1 ? "en" : "zh";
        ApplyLanguage();
    }

    private void ApplyLanguage()
    {
        Title = T("window.title");
        TitleTextBlock.Text = T("app.title");
        SubtitleTextBlock.Text = T("app.subtitle");
        ProjectGroupBox.Header = T("group.project");
        ProjectRootLabel.Content = T("label.projectRoot");
        BrowseProjectButton.Content = T("button.browse");
        PythonCommandLabel.Content = T("label.pythonCommand");
        TokenHubGroupBox.Header = T("group.tokenhub");
        TokenHubKeyHintTextBlock.Text = T("hint.tokenhubKey");
        BaseUrlLabel.Content = T("label.baseUrl");
        UseTokenHubBaseUrlButton.Content = T("button.useTokenHubBaseUrl");
        UseDeepSeekBaseUrlButton.Content = T("button.useDeepSeekBaseUrl");
        ModelLabel.Content = T("label.model");
        LocalProxyGroupBox.Header = T("group.localProxy");
        UpstreamPresetLabel.Content = T("label.upstreamPreset");
        GenerateProxyKeyButton.Content = T("button.generate");
        PersistCodexProxyKeyCheckBox.Content = T("checkbox.persistProxyKey");
        HostLabel.Content = T("label.host");
        PortLabel.Content = T("label.port");
        TimeoutLabel.Content = T("label.timeout");
        CodexContextLabel.Content = T("label.codexContext");
        ContextWindowLabel.Text = T("label.contextWindow");
        AutoCompactLabel.Text = T("label.autoCompact");
        MaxOutputTokensLabel.Text = T("label.maxOutputTokens");
        ResponseLanguageLabel.Content = T("label.responseLanguage");
        EnableToolCallsCheckBox.Content = T("checkbox.enableToolCalls");
        ToolCallsHintTextBlock.Text = T("hint.toolCalls");
        RefreshResponseLanguageOptions();
        CodexHomeGroupBox.Header = T("group.codexHome");
        DesktopCodexHomeLabel.Content = T("label.desktopHome");
        VsCodeCodexHomeLabel.Content = T("label.vscodeHome");
        if (UpstreamPresetComboBox.SelectedItem is UpstreamPreset preset)
        {
            UpstreamPresetComboBox.ToolTip = string.Format(T("tooltip.upstreamPreset"), preset.DisplayName);
        }
        ActionsGroupBox.Header = T("group.actions");
        InstallDependenciesButton.Content = T("button.installDeps");
        ProbeButton.Content = T("button.probe");
        RecoveryMonitorButton.Content = _recoveryMonitorCts is null ? T("button.monitorRecovery") : T("button.stopMonitor");
        StartProxyButton.Content = T("button.startProxy");
        StopProxyButton.Content = T("button.stopProxy");
        HealthCheckButton.Content = T("button.health");
        OpenConfigButton.Content = T("button.openConfig");
        LaunchDesktopButton.Content = T("button.launchDesktop");
        LaunchDesktopButton.ToolTip = T("tooltip.launchDesktop");
        LaunchVsCodeButton.Content = T("button.launchVsCode");
        LaunchVsCodeButton.ToolTip = T("tooltip.launchVsCode");
        WriteConfigButton.Content = T("button.writeConfig");
        WriteConfigButton.ToolTip = T("tooltip.writeConfig");
        CopyLogButton.Content = T("button.copyLog");
        ClearLogButton.Content = T("button.clearLog");
        HelpButton.Content = T("button.help");
        LogGroupBox.Header = T("group.log");
        FooterTextBlock.Text = T("footer");
        StatusText.Text = _proxyProcess is { HasExited: false } ? T("status.running") : T("status.stopped");
    }

    private void InitializeUpstreamPresets()
    {
        _upstreamPresets.Clear();
        _upstreamPresets.Add(new UpstreamPreset(
            "TokenHub / GLM-5.1",
            "https://tokenhub.tencentmaas.com/plan/v3/chat/completions",
            "glm-5.1",
            600,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "DeepSeek / deepseek-chat",
            "https://api.deepseek.com/chat/completions",
            "deepseek-chat",
            600,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "DeepSeek / deepseek-reasoner",
            "https://api.deepseek.com/chat/completions",
            "deepseek-reasoner",
            900,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "DeepSeek / deepseek-v3",
            "https://api.deepseek.com/chat/completions",
            "deepseek-v3",
            600,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "DeepSeek / deepseek-v4-flash",
            "https://api.deepseek.com/chat/completions",
            "deepseek-v4-flash",
            600,
             1000000,
             800000,
             384000));
        _upstreamPresets.Add(new UpstreamPreset(
            "DeepSeek / deepseek-v4-pro",
            "https://api.deepseek.com/chat/completions",
            "deepseek-v4-pro",
            900,
             1000000,
             800000,
             384000));
        _upstreamPresets.Add(new UpstreamPreset(
            "Vinno DeepSeek Relay / deepseek-v4-pro",
            "https://t.vinno.com/v1/chat/completions",
            "deepseek-v4-pro",
            900,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "Vinno DeepSeek Relay / glm-5-1",
            "https://t.vinno.com/v1/chat/completions",
            "glm-5-1",
            900,
            64000,
            48000,
            8192));
        _upstreamPresets.Add(new UpstreamPreset(
            "Vinno DeepSeek Relay / minimax-m-2-7",
            "https://t.vinno.com/v1/chat/completions",
            "minimax-m-2-7",
            900,
            32000,
            24000,
            4096));

        UpstreamPresetComboBox.Items.Clear();
        foreach (var preset in _upstreamPresets)
        {
            UpstreamPresetComboBox.Items.Add(preset);
        }

        if (UpstreamPresetComboBox.Items.Count > 0)
        {
            _suppressPresetChangeLog = true;
            UpstreamPresetComboBox.SelectedIndex = 0;
            _suppressPresetChangeLog = false;
        }
    }

    private void InitializeResponseLanguageOptions()
    {
        ResponseLanguageComboBox.Items.Clear();
        ResponseLanguageComboBox.Items.Add(new ResponseLanguageOption("zh", "简体中文", "Simplified Chinese"));
        ResponseLanguageComboBox.Items.Add(new ResponseLanguageOption("auto", "自动", "Auto"));
        ResponseLanguageComboBox.Items.Add(new ResponseLanguageOption("en", "English", "English"));
        ResponseLanguageComboBox.SelectedIndex = 0;
    }

    private void RefreshResponseLanguageOptions()
    {
        foreach (var item in ResponseLanguageComboBox.Items.OfType<ResponseLanguageOption>())
        {
            item.UseEnglish = _language == "en";
        }
        ResponseLanguageComboBox.Items.Refresh();
    }

    private string ResponseLanguageInstruction()
    {
        var option = ResponseLanguageComboBox.SelectedItem as ResponseLanguageOption;
        return option?.Code switch
        {
            "zh" => "除非用户明确要求其他语言，否则所有面向用户的自然语言回复都使用简体中文。代码、命令、文件路径、API 名称、工具名和引用的错误信息保持原文。",
            "en" => "Respond to the user in English unless the user explicitly asks for another language. Keep code, commands, file paths, API names, tool names, and quoted errors in their original language.",
            _ => ""
        };
    }

    private void ApplySelectedUpstreamPreset(int index, bool logChange)
    {
        if (index < 0 || index >= _upstreamPresets.Count)
        {
            return;
        }

        var preset = _upstreamPresets[index];
        _suppressPresetChangeLog = !logChange;
        UpstreamPresetComboBox.SelectedIndex = index;
        _suppressPresetChangeLog = false;
        TokenHubBaseUrlTextBox.Text = preset.BaseUrl;
        TokenHubModelTextBox.Text = preset.Model;
        TimeoutTextBox.Text = preset.TimeoutSeconds.ToString();
        ContextWindowTextBox.Text = preset.ContextWindowTokens.ToString();
        AutoCompactTextBox.Text = preset.AutoCompactTokenLimit.ToString();
        MaxOutputTokensTextBox.Text = preset.MaxOutputTokens.ToString();
        _omitForcedToolChoice = false;
        if (logChange)
        {
            Log(string.Format(
                T("log.presetApplied"),
                preset.DisplayName,
                preset.BaseUrl,
                preset.Model,
                preset.TimeoutSeconds,
                preset.ContextWindowTokens,
                preset.AutoCompactTokenLimit,
                preset.MaxOutputTokens));
        }
    }

    private string T(string key)
    {
        return _language == "en"
            ? En.TryGetValue(key, out var en) ? en : key
            : Zh.TryGetValue(key, out var zh) ? zh : key;
    }

    private string LocalizeOperationName(string operationName)
    {
        return operationName switch
        {
            "install dependencies" => T("op.install"),
            "probe upstream" => T("op.probe"),
            "monitor recovery" => T("op.monitorRecovery"),
            "start proxy" => T("op.start"),
            "open config" => T("op.openConfig"),
            "launch Codex Desktop" => T("op.launchDesktop"),
            "launch VS Code" => T("op.launchVsCode"),
            "health check" => T("op.health"),
            "write config" => T("op.writeConfig"),
            _ => operationName
        };
    }

    private void BrowseProjectButton_Click(object sender, RoutedEventArgs e)
    {
        using var dialog = new WinForms.FolderBrowserDialog
        {
            Description = "Select TokenHubResponsesProxy project root",
            SelectedPath = Directory.Exists(ProjectRootTextBox.Text) ? ProjectRootTextBox.Text : ""
        };

        if (dialog.ShowDialog() == WinForms.DialogResult.OK)
        {
            ProjectRootTextBox.Text = dialog.SelectedPath;
        }
    }

    private void GenerateProxyKeyButton_Click(object sender, RoutedEventArgs e)
    {
        CodexProxyKeyTextBox.Text = GenerateProxyKey();
        Log(T("log.generatedProxyKey"));
    }

    private void UseTokenHubBaseUrlButton_Click(object sender, RoutedEventArgs e)
    {
        TokenHubBaseUrlTextBox.Text = "https://tokenhub.tencentmaas.com/plan/v3/chat/completions";
        _omitForcedToolChoice = false;
        Log(T("log.baseUrlAppliedTokenHub"));
    }

    private void UseDeepSeekBaseUrlButton_Click(object sender, RoutedEventArgs e)
    {
        TokenHubBaseUrlTextBox.Text = "https://api.deepseek.com/chat/completions";
        _omitForcedToolChoice = false;
        Log(T("log.baseUrlAppliedDeepSeek"));
    }

    private async void InstallDependenciesButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("install dependencies", async () =>
        {
            var root = ValidateProjectRoot();
            var pythonPath = PythonPath(root);
            if (!File.Exists(pythonPath))
            {
                Log(T("log.creatingVenv"));
                await RunCommandAsync(PythonCommandTextBox.Text.Trim(), "-m venv .venv", root, BuildBaseEnvironment());
            }
            else
            {
                Log(T("log.venvExists"));
            }

            Log(T("log.installingDeps"));
            await RunCommandAsync(pythonPath, "-m pip install -r requirements.txt", root, BuildBaseEnvironment());
        });
    }

    private async void ProbeButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("probe upstream", async () =>
        {
            Log(T("log.probeRequested"));
            var root = ValidateProjectRoot();
            ValidateSecretInputs();
            await EnsureCodexProxyKeyPersistedIfRequestedAsync();

            var pythonPath = PythonPath(root);
            if (!File.Exists(pythonPath))
            {
                throw new InvalidOperationException(T("err.missingVenv"));
            }

            Log(T("log.probing"));
            await Task.Yield();
            var result = await RunCommandCaptureAsync(pythonPath, "scripts\\probe_tokenhub.py", root, BuildProxyEnvironment(root));
            var toolCallsOk = result.Output.Contains("non_stream_tool_calls: PASS", StringComparison.Ordinal)
                && result.Output.Contains("stream_tool_calls: PASS", StringComparison.Ordinal);
            var noForcedToolChoiceOk = result.Output.Contains("tool_variant_no_forced_choice: PASS", StringComparison.Ordinal);

            _omitForcedToolChoice = !toolCallsOk && noForcedToolChoiceOk;
            EnableToolCallsCheckBox.IsChecked = toolCallsOk || noForcedToolChoiceOk;
            if (toolCallsOk)
            {
                Log(T("log.toolProbePassed"));
            }
            else if (noForcedToolChoiceOk)
            {
                Log(T("log.toolProbeNoForcedChoicePassed"));
            }
            else
            {
                Log(T("log.toolProbeFailed"));
            }

            if (result.ExitCode != 0)
            {
                throw new InvalidOperationException(T("err.probeFailed"));
            }
        });
    }

    private async void RecoveryMonitorButton_Click(object sender, RoutedEventArgs e)
    {
        if (_recoveryMonitorCts is not null)
        {
            StopRecoveryMonitor(writeLog: true);
            return;
        }

        try
        {
            var root = ValidateProjectRoot();
            ValidateSecretInputs();
            await EnsureCodexProxyKeyPersistedIfRequestedAsync();

            var pythonPath = PythonPath(root);
            if (!File.Exists(pythonPath))
            {
                throw new InvalidOperationException(T("err.missingVenv"));
            }

            var cts = new CancellationTokenSource();
            _recoveryMonitorCts = cts;
            RecoveryMonitorButton.Content = T("button.stopMonitor");
            Log(string.Format(T("log.monitorStarted"), (int)RecoveryMonitorInterval.TotalSeconds));
            _ = MonitorRecoveryAsync(root, pythonPath, BuildProxyEnvironment(root), cts.Token);
        }
        catch (Exception ex)
        {
            LogError(ex);
        }
    }

    private async Task MonitorRecoveryAsync(
        string root,
        string pythonPath,
        Dictionary<string, string> environment,
        CancellationToken cancellationToken)
    {
        var attempt = 0;
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                attempt++;
                Log(string.Format(T("log.monitorProbeAttempt"), attempt));
                var result = await RunCommandCaptureAsync(
                    pythonPath,
                    "scripts\\probe_tokenhub.py",
                    root,
                    environment,
                    logOutput: false);
                if (IsProbeSuccessful(result))
                {
                    await Dispatcher.InvokeAsync(() =>
                    {
                        _omitForcedToolChoice = false;
                        EnableToolCallsCheckBox.IsChecked = true;
                        Log(T("log.monitorRecovered"));
                        ShowRecoveryNotification();
                        StopRecoveryMonitor(writeLog: false);
                    });
                    return;
                }

                Log(string.Format(T("log.monitorStillDown"), SummarizeProbeFailure(result)));
                await Task.Delay(RecoveryMonitorInterval, cancellationToken);
            }
        }
        catch (OperationCanceledException)
        {
        }
        catch (Exception ex)
        {
            LogError(ex);
            await Dispatcher.InvokeAsync(() => StopRecoveryMonitor(writeLog: false));
        }
    }

    private void UpstreamPresetComboBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_suppressPresetChangeLog)
        {
            return;
        }

        if (UpstreamPresetComboBox.SelectedItem is not UpstreamPreset preset)
        {
            return;
        }

        TokenHubBaseUrlTextBox.Text = preset.BaseUrl;
        TokenHubModelTextBox.Text = preset.Model;
        TimeoutTextBox.Text = preset.TimeoutSeconds.ToString();
        ContextWindowTextBox.Text = preset.ContextWindowTokens.ToString();
        AutoCompactTextBox.Text = preset.AutoCompactTokenLimit.ToString();
        MaxOutputTokensTextBox.Text = preset.MaxOutputTokens.ToString();
        _omitForcedToolChoice = false;
        Log(string.Format(
            T("log.presetApplied"),
            preset.DisplayName,
            preset.BaseUrl,
            preset.Model,
            preset.TimeoutSeconds,
            preset.ContextWindowTokens,
            preset.AutoCompactTokenLimit,
            preset.MaxOutputTokens));
    }

    private async void StartProxyButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("start proxy", async () =>
        {
            if (_proxyProcess is { HasExited: false })
            {
                Log(T("log.proxyAlreadyRunning"));
                return;
            }

            var root = ValidateProjectRoot();
            ValidateSecretInputs();
            await EnsureCodexProxyKeyPersistedIfRequestedAsync();

            var pythonPath = PythonPath(root);
            if (!File.Exists(pythonPath))
            {
                throw new InvalidOperationException(T("err.missingVenv"));
            }

            var env = BuildProxyEnvironment(root);
            env["ENABLE_TOOL_CALLS"] = EnableToolCallsCheckBox.IsChecked == true ? "true" : "false";
            env["UPSTREAM_TOOL_CHOICE_MODE"] = _omitForcedToolChoice ? "omit_forced" : "passthrough";

            var args = $"-m uvicorn proxy_app.main:app --host {ProxyHostTextBox.Text.Trim()} --port {ProxyPortTextBox.Text.Trim()}";
            _proxyProcess = CreateProcess(pythonPath, args, root, env);
            _proxyProcess.EnableRaisingEvents = true;
            _proxyProcess.OutputDataReceived += (_, ev) => { if (ev.Data is not null) Log(ev.Data); };
            _proxyProcess.ErrorDataReceived += (_, ev) => { if (ev.Data is not null) Log(ev.Data); };
            var startedProcess = _proxyProcess;
            _proxyProcess.Exited += (_, _) => Dispatcher.BeginInvoke(() =>
            {
                SetStatus(false);
                var exitCode = TryGetExitCode(startedProcess);
                Log(exitCode is null ? T("log.proxyExited") : string.Format(T("log.proxyExitedCode"), exitCode));
            });

            await Task.Run(() =>
            {
                _proxyProcess.Start();
                _proxyProcess.BeginOutputReadLine();
                _proxyProcess.BeginErrorReadLine();
            });
            SetStatus(true);
            Log(string.Format(T("log.proxyStarted"), ProxyHostTextBox.Text.Trim(), ProxyPortTextBox.Text.Trim()));
            Log(string.Format(T("log.diagnosticLogPath"), Path.Combine(root, "logs")));

            await Task.Delay(500);
        });
    }

    private void StopProxyButton_Click(object sender, RoutedEventArgs e)
    {
        StopProxyNonBlocking(writeLog: true);
    }

    private async void HealthCheckButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("health check", async () =>
        {
            var url = $"http://{ProxyHostTextBox.Text.Trim()}:{ProxyPortTextBox.Text.Trim()}/health";
            var response = await _httpClient.GetStringAsync(url);
            Log($"GET {url}");
            Log(FormatHealthResponse(response));
        });
    }

    private async void OpenConfigButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("open config", async () =>
        {
            var configPath = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
                ".codex",
                "config.toml");
            await Task.Run(() =>
            {
                Directory.CreateDirectory(Path.GetDirectoryName(configPath)!);
                if (!File.Exists(configPath))
                {
                    File.WriteAllText(configPath, "", Encoding.UTF8);
                }
                Process.Start(new ProcessStartInfo("notepad.exe", $"\"{configPath}\"") { UseShellExecute = false });
            });
            Log(string.Format(T("log.openedConfig"), configPath));
        });
    }

    private async void LaunchDesktopButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("launch Codex Desktop", async () =>
        {
            await EnsureCodexProxyKeyPersistedIfRequestedAsync();
            var codexHome = DesktopCodexHomeTextBox.Text.Trim();
            var defaultConfig = BuildDefaultCodexConfig();
            var homeInfo = await Task.Run(() => InitializeCodexHome(codexHome, defaultConfig));
            var exe = await Task.Run(FindCodexDesktopPath);
            if (exe is null)
            {
                Log(T("log.desktopNotFound"));
                return;
            }

            var env = BuildStartInfoEnvironment();
            env["CODEX_HOME"] = homeInfo.Home;
            await Task.Run(() => StartProcessWithEnvironment(exe, "", Path.GetDirectoryName(exe)!, env));
            Log(string.Format(T("log.launchedDesktop"), homeInfo.Home));
            Log($"Config: {homeInfo.Config}");
        });
    }

    private async void LaunchVsCodeButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("launch VS Code", async () =>
        {
            await EnsureCodexProxyKeyPersistedIfRequestedAsync();
            var codexHome = VsCodeCodexHomeTextBox.Text.Trim();
            var root = ValidateProjectRoot();
            var defaultConfig = BuildDefaultCodexConfig();
            var homeInfo = await Task.Run(() => InitializeCodexHome(codexHome, defaultConfig));
            var exe = await Task.Run(FindVsCodePath);
            if (exe is null)
            {
                Log(T("log.vscodeNotFound"));
                return;
            }

            var env = BuildStartInfoEnvironment();
            env["CODEX_HOME"] = homeInfo.Home;
            await Task.Run(() => StartProcessWithEnvironment(exe, $"\"{root}\"", root, env));
            Log(string.Format(T("log.launchedVsCode"), homeInfo.Home));
            Log($"Config: {homeInfo.Config}");
        });
    }

    private async void WriteConfigButton_Click(object sender, RoutedEventArgs e)
    {
        await RunBusyAsync("write config", async () =>
        {
            var config = BuildDefaultCodexConfig();
            var codexHomes = new[]
            {
                DesktopCodexHomeTextBox.Text.Trim(),
                VsCodeCodexHomeTextBox.Text.Trim(),
            };

            foreach (var codexHome in codexHomes)
            {
                if (string.IsNullOrWhiteSpace(codexHome))
                {
                    continue;
                }
                var home = Environment.ExpandEnvironmentVariables(codexHome);
                var targetConfig = Path.Combine(home, "config.toml");
                await Task.Run(() =>
                {
                    Directory.CreateDirectory(home);
                    File.WriteAllText(targetConfig, config, Encoding.UTF8);
                });
                Log(string.Format(T("log.configWritten"), targetConfig));
            }
        });
    }

    private void CopyLogButton_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            var lines = LogListBox.SelectedItems.Count > 0
                ? LogListBox.SelectedItems.Cast<string>()
                : LogListBox.Items.Cast<string>();
            var text = string.Join(Environment.NewLine, lines);
            if (!string.IsNullOrWhiteSpace(text))
            {
                System.Windows.Clipboard.SetText(text);
            }
        }
        catch (Exception ex)
        {
            LogError(ex);
        }
    }

    private void ClearLogButton_Click(object sender, RoutedEventArgs e)
    {
        LogListBox.Items.Clear();
        _logAutoScroll = true;
    }

    private void HelpButton_Click(object sender, RoutedEventArgs e)
    {
        var helpWindow = new HelpWindow(_language)
        {
            Owner = this
        };
        helpWindow.ShowDialog();
    }

    private async Task RunBusyAsync(string operationName, Func<Task> operation)
    {
        SetButtonsEnabled(false);
        try
        {
            Log(string.Format(T("log.startingOperation"), LocalizeOperationName(operationName)));
            await Dispatcher.InvokeAsync(() => { }, System.Windows.Threading.DispatcherPriority.Background);
            await operation();
            Log(string.Format(T("log.finishedOperation"), LocalizeOperationName(operationName)));
        }
        catch (Exception ex)
        {
            Log(string.Format(T("log.failedOperation"), LocalizeOperationName(operationName)));
            LogError(ex);
        }
        finally
        {
            SetButtonsEnabled(true);
        }
    }

    private string ValidateProjectRoot()
    {
        var root = ProjectRootTextBox.Text.Trim();
        if (!Directory.Exists(root))
        {
            throw new InvalidOperationException(T("err.projectRootMissing"));
        }
        if (!File.Exists(Path.Combine(root, "requirements.txt")) ||
            !File.Exists(Path.Combine(root, "proxy_app", "main.py")) ||
            !File.Exists(Path.Combine(root, "scripts", "probe_tokenhub.py")))
        {
            throw new InvalidOperationException(T("err.projectRootInvalid"));
        }
        return root;
    }

    private void ValidateSecretInputs()
    {
        if (string.IsNullOrWhiteSpace(TokenHubApiKeyBox.Password))
        {
            throw new InvalidOperationException(T("err.tokenhubKeyRequired"));
        }
        if (string.IsNullOrWhiteSpace(CodexProxyKeyTextBox.Text))
        {
            throw new InvalidOperationException(T("err.proxyKeyRequired"));
        }
    }

    private Dictionary<string, string> BuildBaseEnvironment()
    {
        return new Dictionary<string, string>
        {
            ["PYTHONUTF8"] = "1"
        };
    }

    private Dictionary<string, string> BuildProxyEnvironment(string projectRoot)
    {
        var env = BuildBaseEnvironment();
        var selectedPreset = GetSelectedUpstreamPreset();
        env["TOKENHUB_API_KEY"] = NormalizeTokenHubKey(TokenHubApiKeyBox.Password);
        env["TOKENHUB_BASE_URL"] = NormalizeUpstreamBaseUrl(
            string.IsNullOrWhiteSpace(TokenHubBaseUrlTextBox.Text)
                ? selectedPreset.BaseUrl
                : TokenHubBaseUrlTextBox.Text.Trim());
        env["TOKENHUB_MODEL"] = string.IsNullOrWhiteSpace(TokenHubModelTextBox.Text)
            ? selectedPreset.Model
            : TokenHubModelTextBox.Text.Trim();
        env["CODEX_GLM_PROXY_KEY"] = CodexProxyKeyTextBox.Text.Trim();
        env["PROXY_HOST"] = ProxyHostTextBox.Text.Trim();
        env["PROXY_PORT"] = ProxyPortTextBox.Text.Trim();
        env["PROXY_REQUEST_TIMEOUT_SECONDS"] = TimeoutTextBox.Text.Trim();
        env["ENABLE_TOOL_CALLS"] = EnableToolCallsCheckBox.IsChecked == true ? "true" : "false";
        env["UPSTREAM_TOOL_CHOICE_MODE"] = _omitForcedToolChoice ? "omit_forced" : "passthrough";
        env["PROXY_DIAGNOSTIC_LOG_ENABLED"] = "true";
        env["PROXY_DIAGNOSTIC_LOG_DIR"] = Path.Combine(projectRoot, "logs");
        var languageInstruction = ResponseLanguageInstruction();
        if (!string.IsNullOrWhiteSpace(languageInstruction))
        {
            env["RESPONSE_LANGUAGE_INSTRUCTION"] = languageInstruction;
        }
        return env;
    }

    private void EnsureCodexProxyKeyPersistedIfRequested()
    {
        if (PersistCodexProxyKeyCheckBox.IsChecked == true)
        {
            Environment.SetEnvironmentVariable(
                "CODEX_GLM_PROXY_KEY",
                CodexProxyKeyTextBox.Text.Trim(),
                EnvironmentVariableTarget.User);
            Log(T("log.persistedProxyKey"));
        }
    }

    private async Task EnsureCodexProxyKeyPersistedIfRequestedAsync()
    {
        var shouldPersist = PersistCodexProxyKeyCheckBox.IsChecked == true;
        var proxyKey = CodexProxyKeyTextBox.Text.Trim();
        if (!shouldPersist)
        {
            return;
        }

        await Task.Run(() =>
        {
            Environment.SetEnvironmentVariable(
                "CODEX_GLM_PROXY_KEY",
                proxyKey,
                EnvironmentVariableTarget.User);
        });
        Log(T("log.persistedProxyKey"));
    }

    private async Task RunCommandAsync(string fileName, string arguments, string workingDirectory, Dictionary<string, string> environment)
    {
        var result = await RunCommandCaptureAsync(fileName, arguments, workingDirectory, environment);
        if (result.ExitCode != 0)
        {
            throw new InvalidOperationException(BuildCommandFailureMessage(fileName, arguments, result));
        }
    }

    private static string BuildCommandFailureMessage(string fileName, string arguments, CommandResult result)
    {
        var message = new StringBuilder();
        message.AppendLine($"Command failed with exit code {result.ExitCode}: {fileName} {arguments}");

        var lines = result.Output
            .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
            .TakeLast(20)
            .ToArray();
        if (lines.Length > 0)
        {
            message.AppendLine("Last command output:");
            foreach (var line in lines)
            {
                message.AppendLine(line);
            }
        }

        return message.ToString().TrimEnd();
    }

    private Task<CommandResult> RunCommandCaptureAsync(
        string fileName,
        string arguments,
        string workingDirectory,
        Dictionary<string, string> environment,
        bool logOutput = true)
    {
        var tcs = new TaskCompletionSource<CommandResult>(TaskCreationOptions.RunContinuationsAsynchronously);
        var output = new StringBuilder();
        var process = CreateProcess(fileName, arguments, workingDirectory, environment);

        process.EnableRaisingEvents = true;
        process.OutputDataReceived += (_, ev) =>
        {
            if (string.IsNullOrWhiteSpace(ev.Data)) return;
            output.AppendLine(ev.Data);
            if (logOutput)
            {
                Log(ev.Data);
            }
        };
        process.ErrorDataReceived += (_, ev) =>
        {
            if (string.IsNullOrWhiteSpace(ev.Data)) return;
            output.AppendLine(ev.Data);
            if (logOutput)
            {
                Log(ev.Data);
            }
        };
        process.Exited += (_, _) =>
        {
            tcs.TrySetResult(new CommandResult(process.ExitCode, output.ToString()));
            process.Dispose();
        };

        Task.Run(() =>
        {
            try
            {
                if (logOutput)
                {
                    Log($"Running: {fileName} {arguments}");
                }
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                process.Dispose();
                tcs.TrySetException(ex);
            }
        });
        return tcs.Task;
    }

    private static Process CreateProcess(string fileName, string arguments, string workingDirectory, Dictionary<string, string> environment)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };

        foreach (var (key, value) in environment)
        {
            startInfo.Environment[key] = value;
        }

        return new Process { StartInfo = startInfo };
    }

    private static Dictionary<string, string> BuildStartInfoEnvironment()
    {
        var env = new Dictionary<string, string>();
        var userProxyKey = Environment.GetEnvironmentVariable("CODEX_GLM_PROXY_KEY", EnvironmentVariableTarget.User);
        if (!string.IsNullOrWhiteSpace(userProxyKey))
        {
            env["CODEX_GLM_PROXY_KEY"] = userProxyKey;
        }
        return env;
    }

    private static void StartProcessWithEnvironment(string fileName, string arguments, string workingDirectory, Dictionary<string, string> environment)
    {
        var startInfo = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false
        };

        foreach (var (key, value) in environment)
        {
            startInfo.Environment[key] = value;
        }

        Process.Start(startInfo);
    }

    private void StopProxyNonBlocking(bool writeLog)
    {
        var process = _proxyProcess;
        _proxyProcess = null;
        SetStatus(false);
        SetButtonsEnabled(true);

        if (process is null)
        {
            if (writeLog)
            {
                Log(T("log.proxyNotRunning"));
            }
            return;
        }

        if (writeLog)
        {
            Log(T("log.stopRequested"));
        }

        _ = Task.Run(() =>
        {
            try
            {
                if (!process.HasExited)
                {
                    process.Kill(entireProcessTree: true);
                    process.WaitForExit(3000);
                }
                Log(T("log.proxyTerminationCompleted"));
            }
            catch (Exception ex)
            {
                Log(string.Format(T("log.proxyTerminationWarning"), ex.GetType().Name, ex.Message));
            }
            finally
            {
                process.Dispose();
            }
        });
    }

    private void StopRecoveryMonitor(bool writeLog)
    {
        var cts = _recoveryMonitorCts;
        _recoveryMonitorCts = null;
        cts?.Cancel();
        cts?.Dispose();
        RecoveryMonitorButton.Content = T("button.monitorRecovery");
        if (writeLog)
        {
            Log(T("log.monitorStopped"));
        }
    }

    private static bool IsProbeSuccessful(CommandResult result)
    {
        return result.ExitCode == 0
            && result.Output.Contains("non_stream_text: PASS", StringComparison.Ordinal)
            && result.Output.Contains("stream_text: PASS", StringComparison.Ordinal)
            && result.Output.Contains("non_stream_tool_calls: PASS", StringComparison.Ordinal)
            && result.Output.Contains("stream_tool_calls: PASS", StringComparison.Ordinal);
    }

    private static string SummarizeProbeFailure(CommandResult result)
    {
        var lines = result.Output
            .Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
            .Where(line =>
                line.Contains(": FAIL", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("HTTP ", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("required", StringComparison.OrdinalIgnoreCase) ||
                line.Contains("failed", StringComparison.OrdinalIgnoreCase))
            .TakeLast(4)
            .ToArray();
        if (lines.Length == 0)
        {
            return $"exit_code={result.ExitCode}";
        }
        return string.Join(" | ", lines);
    }

    private void ShowRecoveryNotification()
    {
        using var notification = new WinForms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Information,
            Visible = true,
            BalloonTipTitle = T("notify.recoveredTitle"),
            BalloonTipText = T("notify.recoveredText")
        };
        notification.ShowBalloonTip(8000);
    }

    private static int? TryGetExitCode(Process process)
    {
        try
        {
            return process.HasExited ? process.ExitCode : null;
        }
        catch
        {
            return null;
        }
    }

    private string FormatHealthResponse(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;
            var metrics = root.TryGetProperty("metrics", out var metricsElement)
                ? metricsElement
                : default;
            if (metrics.ValueKind != JsonValueKind.Object)
            {
                return json;
            }

            var requests = metrics.GetProperty("requests");
            var chars = metrics.GetProperty("chars");
            var usage = metrics.GetProperty("upstream_usage_tokens");
            var lines = new List<string>
            {
                string.Format(T("log.healthSummary"), GetString(root, "status"), GetString(root, "model"), GetBool(root, "tool_calls_enabled")),
                string.Format(T("log.languageInstruction"), GetBool(root, "response_language_instruction_configured")),
                string.Format(T("log.metricsRequests"), GetInt(requests, "started"), GetInt(requests, "completed"), GetInt(requests, "failed")),
                string.Format(T("log.metricsChars"), GetInt(chars, "request_text"), GetInt(chars, "response_text"), GetInt(chars, "response_tool_calls"), GetIntWithFallback(chars, "total_counted", "total")),
                string.Format(T("log.metricsTokens"), GetInt(usage, "prompt"), GetInt(usage, "completion"), GetInt(usage, "total")),
            };

            if (root.TryGetProperty("diagnostic_log", out var diagnosticLog) &&
                diagnosticLog.ValueKind == JsonValueKind.Object)
            {
                lines.Add(string.Format(T("log.diagnosticLogSummary"), GetBool(diagnosticLog, "enabled"), GetString(diagnosticLog, "path")));
            }

            lines.Add(T("log.metricsNote"));
            return string.Join(Environment.NewLine, lines);
        }
        catch (JsonException)
        {
            return json;
        }
        catch (InvalidOperationException)
        {
            return json;
        }
    }

    private static string GetString(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";
    }

    private static bool GetBool(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;
    }

    private static int GetInt(JsonElement element, string name)
    {
        return element.TryGetProperty(name, out var value) && value.TryGetInt32(out var result)
            ? result
            : 0;
    }

    private static int GetIntWithFallback(JsonElement element, string primaryName, string fallbackName)
    {
        var primary = GetInt(element, primaryName);
        return primary != 0 || !element.TryGetProperty(fallbackName, out _) ? primary : GetInt(element, fallbackName);
    }

    private CodexHomeInfo InitializeCodexHome(string codexHome, string defaultConfig)
    {
        if (string.IsNullOrWhiteSpace(codexHome))
        {
            throw new InvalidOperationException(T("err.codexHomeRequired"));
        }

        var home = Environment.ExpandEnvironmentVariables(codexHome);
        Directory.CreateDirectory(home);
        var targetConfig = Path.Combine(home, "config.toml");
        if (!File.Exists(targetConfig))
        {
            File.WriteAllText(targetConfig, defaultConfig, Encoding.UTF8);
            Log(T("log.createdIsolatedConfig"));
        }
        return new CodexHomeInfo(home, targetConfig);
    }

    private string BuildDefaultCodexConfig()
    {
        var selectedPreset = GetSelectedUpstreamPreset();
        var model = string.IsNullOrWhiteSpace(TokenHubModelTextBox.Text) ? selectedPreset.Model : TokenHubModelTextBox.Text.Trim();
        var host = string.IsNullOrWhiteSpace(ProxyHostTextBox.Text) ? "127.0.0.1" : ProxyHostTextBox.Text.Trim();
        var port = string.IsNullOrWhiteSpace(ProxyPortTextBox.Text) ? "8787" : ProxyPortTextBox.Text.Trim();
        var contextWindow = ParsePositiveIntOrDefault(ContextWindowTextBox.Text, selectedPreset.ContextWindowTokens);
        var autoCompact = ParsePositiveIntOrDefault(AutoCompactTextBox.Text, selectedPreset.AutoCompactTokenLimit);
        var maxOutputTokens = ParsePositiveIntOrDefault(MaxOutputTokensTextBox.Text, selectedPreset.MaxOutputTokens);
        ContextWindowTextBox.Text = contextWindow.ToString();
        AutoCompactTextBox.Text = autoCompact.ToString();
        MaxOutputTokensTextBox.Text = maxOutputTokens.ToString();
        var presetLabel = string.IsNullOrWhiteSpace(selectedPreset.DisplayName) ? model : selectedPreset.DisplayName;
        return $$"""
model_provider = "glm_tokenhub_proxy"
model = "{{model}}"
model_reasoning_effort = "medium"
model_verbosity = "medium"
model_context_window = {{contextWindow}}
model_auto_compact_token_limit = {{autoCompact}}
model_max_output_tokens = {{maxOutputTokens}}

[model_providers.glm_tokenhub_proxy]
name = "{{presetLabel}} via local proxy"
base_url = "http://{{host}}:{{port}}/v1"
wire_api = "responses"
env_key = "CODEX_GLM_PROXY_KEY"
stream_idle_timeout_ms = 300000
stream_max_retries = 3
request_max_retries = 2
""";
    }

    private static string? FindCodexDesktopPath()
    {
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var candidates = new[]
        {
            Path.Combine(localAppData, "Programs", "Codex", "Codex.exe"),
            Path.Combine(localAppData, "Programs", "Codex Desktop", "Codex Desktop.exe"),
            Path.Combine(localAppData, "Programs", "codex", "Codex.exe"),
            Path.Combine(localAppData, "OpenAI Codex", "Codex.exe"),
            Path.Combine(programFiles, "Codex", "Codex.exe"),
            Path.Combine(programFiles, "Codex Desktop", "Codex Desktop.exe"),
            Path.Combine(programFilesX86, "Codex", "Codex.exe"),
            Path.Combine(programFilesX86, "Codex Desktop", "Codex Desktop.exe")
        };
        return candidates.FirstOrDefault(File.Exists);
    }

    private static string? FindVsCodePath()
    {
        var fromPath = FindExecutableOnPath("code.cmd") ?? FindExecutableOnPath("code.exe") ?? FindExecutableOnPath("code");
        if (fromPath is not null) return fromPath;

        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var candidates = new[]
        {
            Path.Combine(localAppData, "Programs", "Microsoft VS Code", "Code.exe"),
            Path.Combine(programFiles, "Microsoft VS Code", "Code.exe"),
            Path.Combine(programFilesX86, "Microsoft VS Code", "Code.exe")
        };
        return candidates.FirstOrDefault(File.Exists);
    }

    private static string? FindExecutableOnPath(string executableName)
    {
        var pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
        foreach (var path in pathValue.Split(Path.PathSeparator, StringSplitOptions.RemoveEmptyEntries))
        {
            var candidate = Path.Combine(path.Trim(), executableName);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }
        return null;
    }

    private static string FindProjectRoot()
    {
        var current = AppContext.BaseDirectory;
        for (var directory = new DirectoryInfo(current); directory is not null; directory = directory.Parent)
        {
            if (File.Exists(Path.Combine(directory.FullName, "requirements.txt")) &&
                Directory.Exists(Path.Combine(directory.FullName, "proxy_app")))
            {
                return directory.FullName;
            }
        }

        var fallback = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."));
        return Directory.Exists(fallback) ? fallback : Environment.CurrentDirectory;
    }

    private static string PythonPath(string root) => Path.Combine(root, ".venv", "Scripts", "python.exe");

    private static string GenerateProxyKey()
    {
        var bytes = RandomNumberGenerator.GetBytes(32);
        return Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
    }

    private static string NormalizeTokenHubKey(string value)
    {
        var normalized = value.Trim().Trim('"', '\'').Trim();
        return normalized.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase)
            ? normalized[7..].Trim()
            : normalized;
    }

    private static string NormalizeUpstreamBaseUrl(string value)
    {
        var normalized = value.Trim().TrimEnd('/');
        if (normalized.Equals("https://api.deepseek.com", StringComparison.OrdinalIgnoreCase))
        {
            return "https://api.deepseek.com/chat/completions";
        }

        return normalized;
    }

    private static int ParsePositiveIntOrDefault(string value, int defaultValue)
    {
        return int.TryParse(value.Trim(), out var parsed) && parsed > 0
            ? parsed
            : defaultValue;
    }

    private UpstreamPreset GetSelectedUpstreamPreset()
    {
        if (UpstreamPresetComboBox.SelectedIndex >= 0 &&
            UpstreamPresetComboBox.SelectedIndex < _upstreamPresets.Count)
        {
            return _upstreamPresets[UpstreamPresetComboBox.SelectedIndex];
        }

        return _upstreamPresets.Count > 0
            ? _upstreamPresets[0]
            : new UpstreamPreset(
                "Custom",
                TokenHubBaseUrlTextBox.Text.Trim(),
                TokenHubModelTextBox.Text.Trim(),
                600,
                64000,
                48000,
                8192);
    }

    private void SetButtonsEnabled(bool enabled)
    {
        InstallDependenciesButton.IsEnabled = enabled;
        ProbeButton.IsEnabled = enabled;
        RecoveryMonitorButton.IsEnabled = enabled || _recoveryMonitorCts is not null;
        StartProxyButton.IsEnabled = enabled && _proxyProcess is not { HasExited: false };
        StopProxyButton.IsEnabled = enabled && _proxyProcess is { HasExited: false };
        HealthCheckButton.IsEnabled = enabled;
        LaunchDesktopButton.IsEnabled = enabled;
        LaunchVsCodeButton.IsEnabled = enabled;
        WriteConfigButton.IsEnabled = enabled;
        CopyLogButton.IsEnabled = true;
        ClearLogButton.IsEnabled = true;
        HelpButton.IsEnabled = true;
    }

    private void SetStatus(bool running)
    {
        StatusDot.Fill = running ? System.Windows.Media.Brushes.ForestGreen : System.Windows.Media.Brushes.Gray;
        StatusText.Text = running ? "Running" : "Stopped";
        StartProxyButton.IsEnabled = !running;
        StopProxyButton.IsEnabled = running;
    }

    private void Log(string message)
    {
        if (string.IsNullOrWhiteSpace(message))
        {
            return;
        }

        Dispatcher.BeginInvoke(() =>
        {
            var shouldScroll = _logAutoScroll || IsLogScrolledToBottom();
            LogListBox.Items.Add($"[{DateTime.Now:HH:mm:ss}] {message}");
            while (LogListBox.Items.Count > MaxLogLines)
            {
                LogListBox.Items.RemoveAt(0);
            }
            if (shouldScroll)
            {
                ScrollLogToBottom();
            }
        });
    }

    private void AttachLogScrollViewer()
    {
        _logScrollViewer = FindVisualChild<ScrollViewer>(LogListBox);
        if (_logScrollViewer is not null)
        {
            _logScrollViewer.ScrollChanged += (_, _) =>
            {
                if (_isProgrammaticLogScroll)
                {
                    return;
                }
                var distanceFromBottom = _logScrollViewer.ScrollableHeight - _logScrollViewer.VerticalOffset;
                _logAutoScroll = distanceFromBottom < 2.0;
            };
        }
    }

    private bool IsLogScrolledToBottom()
    {
        if (_logScrollViewer is null)
        {
            return true;
        }
        return _logScrollViewer.ScrollableHeight - _logScrollViewer.VerticalOffset < 2.0;
    }

    private void ScrollLogToBottom()
    {
        if (_logScrollViewer is null)
        {
            if (LogListBox.Items.Count > 0)
            {
                LogListBox.ScrollIntoView(LogListBox.Items[^1]);
            }
            return;
        }

        _isProgrammaticLogScroll = true;
        try
        {
            LogListBox.UpdateLayout();
            _logScrollViewer.ScrollToEnd();
            _logAutoScroll = true;
        }
        finally
        {
            Dispatcher.BeginInvoke(() => _isProgrammaticLogScroll = false, System.Windows.Threading.DispatcherPriority.Background);
        }
    }

    private static T? FindVisualChild<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var i = 0; i < VisualTreeHelper.GetChildrenCount(parent); i++)
        {
            var child = VisualTreeHelper.GetChild(parent, i);
            if (child is T typedChild)
            {
                return typedChild;
            }
            var nested = FindVisualChild<T>(child);
            if (nested is not null)
            {
                return nested;
            }
        }
        return null;
    }

    private void LogError(Exception ex)
    {
        Log($"{ex.GetType().Name}: {ex.Message}");
    }

    private static readonly Dictionary<string, string> Zh = new()
    {
        ["window.title"] = "TokenHub Responses 代理启动器",
        ["app.title"] = "TokenHub Responses 代理启动器",
        ["app.subtitle"] = "图形化配置、探测并运行本地 Responses 到 TokenHub 的协议适配代理。",
        ["group.project"] = "项目",
        ["label.projectRoot"] = "项目根目录",
        ["button.browse"] = "浏览",
        ["label.pythonCommand"] = "创建 .venv 使用的 Python 命令",
        ["group.tokenhub"] = "TokenHub",
        ["hint.tokenhubKey"] = "只保存在本启动器进程和子代理进程中，不写入文件。",
        ["label.baseUrl"] = "Base URL",
        ["button.useTokenHubBaseUrl"] = "TokenHub",
        ["button.useDeepSeekBaseUrl"] = "DeepSeek",
        ["label.model"] = "模型",
        ["group.localProxy"] = "本地代理",
        ["label.upstreamPreset"] = "上游预设",
        ["button.generate"] = "生成",
        ["checkbox.persistProxyKey"] = "将 CODEX_GLM_PROXY_KEY 写入 Windows 用户环境",
        ["label.host"] = "主机",
        ["label.port"] = "端口",
        ["label.timeout"] = "超时秒数",
        ["label.codexContext"] = "Codex 上下文设置",
        ["label.contextWindow"] = "上下文窗口",
        ["label.autoCompact"] = "自动压缩",
        ["label.maxOutputTokens"] = "最大输出",
        ["label.responseLanguage"] = "回复语言",
        ["checkbox.enableToolCalls"] = "启用工具调用",
        ["hint.toolCalls"] = "先探测上游。非流式和流式工具调用都通过后，启动器会自动启用工具调用。",
        ["group.codexHome"] = "隔离 CODEX_HOME 启动",
        ["label.desktopHome"] = "桌面端 CODEX_HOME",
        ["label.vscodeHome"] = "VS Code CODEX_HOME",
        ["group.actions"] = "操作",
        ["button.installDeps"] = "创建 .venv / 安装依赖",
        ["button.probe"] = "探测上游",
        ["button.monitorRecovery"] = "监控恢复",
        ["button.stopMonitor"] = "停止监控",
        ["button.startProxy"] = "启动代理",
        ["button.stopProxy"] = "停止代理",
        ["button.health"] = "健康检查",
        ["button.openConfig"] = "打开配置",
        ["button.launchDesktop"] = "启动桌面端",
        ["tooltip.launchDesktop"] = "使用配置的隔离 CODEX_HOME 启动 Codex Desktop",
        ["button.launchVsCode"] = "启动 VS Code",
        ["tooltip.launchVsCode"] = "使用配置的隔离 CODEX_HOME 启动 VS Code",
        ["button.copyLog"] = "复制日志",
        ["button.clearLog"] = "清空日志",
        ["button.help"] = "帮助",
        ["group.log"] = "日志",
        ["footer"] = "代理运行时请保持本窗口打开。WPF 启动器不会保存 TOKENHUB_API_KEY。",
        ["status.running"] = "运行中",
        ["status.stopped"] = "已停止",
        ["op.install"] = "安装依赖",
        ["op.probe"] = "探测上游",
        ["op.monitorRecovery"] = "监控恢复",
        ["op.start"] = "启动代理",
        ["op.openConfig"] = "打开配置",
        ["op.launchDesktop"] = "启动 Codex Desktop",
        ["op.launchVsCode"] = "启动 VS Code",
        ["op.health"] = "健康检查",
        ["log.generatedProxyKey"] = "已生成新的 CODEX_GLM_PROXY_KEY。",
        ["log.creatingVenv"] = "正在创建虚拟环境...",
        ["log.venvExists"] = "虚拟环境已存在。",
        ["log.installingDeps"] = "正在安装依赖...",
        ["log.probeRequested"] = "已请求探测。",
        ["log.probing"] = "正在探测上游兼容性...",
        ["log.toolProbePassed"] = "工具调用探测通过。启动代理时将启用工具调用。",
        ["log.toolProbeNoForcedChoicePassed"] = "工具调用兼容模式通过：启动代理时将启用工具调用，并省略强制 tool_choice。",
        ["log.toolProbeFailed"] = "工具调用探测未通过。工具调用将保持关闭。",
        ["log.monitorStarted"] = "已启动恢复监控，每 {0} 秒探测一次。",
        ["log.monitorStopped"] = "已停止恢复监控。",
        ["log.monitorProbeAttempt"] = "恢复监控探测第 {0} 次...",
        ["log.monitorStillDown"] = "服务尚未恢复：{0}",
        ["log.monitorRecovered"] = "上游探测已恢复正常。",
        ["log.proxyAlreadyRunning"] = "代理已在运行。",
        ["log.proxyExited"] = "代理进程已退出。",
        ["log.proxyExitedCode"] = "代理进程已退出，退出码 {0}。",
        ["log.proxyStarted"] = "代理已启动：http://{0}:{1}/v1",
        ["log.diagnosticLogPath"] = "后台安全诊断日志目录：{0}",
        ["log.openedConfig"] = "已打开配置：{0}",
        ["log.desktopNotFound"] = "未能自动找到 Codex Desktop。可使用脚本并传入 -CodexDesktopPath。",
        ["log.vscodeNotFound"] = "未能自动找到 VS Code。请确认 code 在 PATH 中，或使用 PowerShell 脚本传入 -CodePath。",
        ["log.launchedDesktop"] = "已使用 CODEX_HOME={0} 启动 Codex Desktop",
        ["log.launchedVsCode"] = "已使用 CODEX_HOME={0} 启动 VS Code",
        ["log.startingOperation"] = "开始：{0}...",
        ["log.finishedOperation"] = "完成：{0}。",
        ["log.failedOperation"] = "失败：{0}。",
        ["log.persistedProxyKey"] = "CODEX_GLM_PROXY_KEY 已写入 Windows 用户环境。",
        ["log.proxyNotRunning"] = "代理未运行。",
        ["log.stopRequested"] = "已请求停止。正在后台终止代理。",
        ["log.proxyTerminationCompleted"] = "代理终止完成。",
        ["log.proxyTerminationWarning"] = "代理终止警告：{0}: {1}",
        ["log.createdIsolatedConfig"] = "已创建默认隔离 Codex 配置 glm_tokenhub_proxy。",
        ["log.healthSummary"] = "健康状态：{0}，模型：{1}，工具调用：{2}",
        ["log.languageInstruction"] = "回复语言约束：{0}",
        ["log.metricsRequests"] = "请求数：已开始 {0}，已完成 {1}，失败 {2}",
        ["log.metricsChars"] = "字符数：请求文本 {0}，响应文本 {1}，响应工具调用 {2}，合计 {3}",
        ["log.metricsTokens"] = "上游 usage token：prompt {0}，completion {1}，total {2}",
        ["log.diagnosticLogSummary"] = "后台安全诊断日志：启用 {0}，路径 {1}",
        ["log.metricsNote"] = "说明：字符数和 usage 只保存在当前代理进程内存中；不会保存对话正文；字符数不等同于精确 token 数。",
        ["log.presetApplied"] = "已切换上游预设：{0} -> {1}，模型 {2}，超时 {3} 秒，窗口 {4}，压缩 {5}，输出 {6}",
        ["log.baseUrlAppliedTokenHub"] = "已填入 TokenHub Base URL。",
        ["log.baseUrlAppliedDeepSeek"] = "已填入 DeepSeek Base URL。",
        ["tooltip.upstreamPreset"] = "当前上游预设：{0}",
        ["notify.recoveredTitle"] = "上游服务已恢复",
        ["notify.recoveredText"] = "探测已全部通过，可以重新启动代理或继续使用 Codex。",
        ["err.missingVenv"] = "缺少虚拟环境。请先点击“创建 .venv / 安装依赖”。",
        ["err.probeFailed"] = "上游文本或流式探测失败。请检查 TOKENHUB_API_KEY、Base URL 和模型名。",
        ["err.projectRootMissing"] = "项目根目录不存在。",
        ["err.projectRootInvalid"] = "项目根目录看起来不是 TokenHubResponsesProxy。",
        ["err.tokenhubKeyRequired"] = "必须填写 TOKENHUB_API_KEY。",
        ["err.proxyKeyRequired"] = "必须填写 CODEX_GLM_PROXY_KEY。需要时可点击生成。",
        ["err.codexHomeRequired"] = "必须填写 CODEX_HOME 路径。",
        ["button.writeConfig"] = "写入配置",
        ["tooltip.writeConfig"] = "将当前上下文设置强制写入两个隔离的 CODEX_HOME config.toml 文件",
        ["log.configWritten"] = "已写入配置：{0}",
        ["op.writeConfig"] = "写入配置",
    };

    private static readonly Dictionary<string, string> En = new()
    {
        ["window.title"] = "TokenHub Responses Proxy Launcher",
        ["app.title"] = "TokenHub Responses Proxy Launcher",
        ["app.subtitle"] = "Configure, probe, and run the local Responses-to-TokenHub proxy without editing scripts.",
        ["group.project"] = "Project",
        ["label.projectRoot"] = "Project root",
        ["button.browse"] = "Browse",
        ["label.pythonCommand"] = "Python command for creating .venv",
        ["group.tokenhub"] = "TokenHub",
        ["hint.tokenhubKey"] = "Only stored in this launcher process and child proxy process.",
        ["label.baseUrl"] = "Base URL",
        ["button.useTokenHubBaseUrl"] = "TokenHub",
        ["button.useDeepSeekBaseUrl"] = "DeepSeek",
        ["label.model"] = "Model",
        ["group.localProxy"] = "Local proxy",
        ["label.upstreamPreset"] = "Upstream preset",
        ["button.generate"] = "Generate",
        ["checkbox.persistProxyKey"] = "Persist CODEX_GLM_PROXY_KEY to Windows user environment",
        ["label.host"] = "Host",
        ["label.port"] = "Port",
        ["label.timeout"] = "Timeout seconds",
        ["label.codexContext"] = "Codex context settings",
        ["label.contextWindow"] = "Context window",
        ["label.autoCompact"] = "Auto compact",
        ["label.maxOutputTokens"] = "Max output",
        ["label.responseLanguage"] = "Response language",
        ["checkbox.enableToolCalls"] = "Enable tool calls",
        ["hint.toolCalls"] = "Probe the upstream first. The launcher enables tool calls automatically when both tool-call checks pass.",
        ["group.codexHome"] = "Isolated CODEX_HOME launchers",
        ["label.desktopHome"] = "Desktop CODEX_HOME",
        ["label.vscodeHome"] = "VS Code CODEX_HOME",
        ["group.actions"] = "Actions",
        ["button.installDeps"] = "Create .venv / Install dependencies",
        ["button.probe"] = "Probe upstream",
        ["button.monitorRecovery"] = "Monitor recovery",
        ["button.stopMonitor"] = "Stop monitor",
        ["button.startProxy"] = "Start proxy",
        ["button.stopProxy"] = "Stop proxy",
        ["button.health"] = "Health check",
        ["button.openConfig"] = "Open config",
        ["button.launchDesktop"] = "Launch Desktop",
        ["tooltip.launchDesktop"] = "Launch Codex Desktop with the configured isolated CODEX_HOME",
        ["button.launchVsCode"] = "Launch VS Code",
        ["tooltip.launchVsCode"] = "Launch VS Code with the configured isolated CODEX_HOME",
        ["button.copyLog"] = "Copy log",
        ["button.clearLog"] = "Clear log",
        ["button.help"] = "Help",
        ["group.log"] = "Log",
        ["footer"] = "Keep this launcher open while the proxy is running. TOKENHUB_API_KEY is not saved to disk by this WPF launcher.",
        ["status.running"] = "Running",
        ["status.stopped"] = "Stopped",
        ["op.install"] = "install dependencies",
        ["op.probe"] = "probe upstream",
        ["op.monitorRecovery"] = "monitor recovery",
        ["op.start"] = "start proxy",
        ["op.openConfig"] = "open config",
        ["op.launchDesktop"] = "launch Codex Desktop",
        ["op.launchVsCode"] = "launch VS Code",
        ["op.health"] = "health check",
        ["log.generatedProxyKey"] = "Generated a new CODEX_GLM_PROXY_KEY.",
        ["log.creatingVenv"] = "Creating virtual environment...",
        ["log.venvExists"] = "Virtual environment already exists.",
        ["log.installingDeps"] = "Installing dependencies...",
        ["log.probeRequested"] = "Probe requested.",
        ["log.probing"] = "Probing upstream compatibility...",
        ["log.toolProbePassed"] = "Tool-call probe passed. Tool calls will be enabled when starting the proxy.",
        ["log.toolProbeNoForcedChoicePassed"] = "Tool-call compatibility mode passed. Tool calls will be enabled while omitting forced tool_choice.",
        ["log.toolProbeFailed"] = "Tool-call probe did not pass. Tool calls will remain disabled.",
        ["log.monitorStarted"] = "Recovery monitor started. Probing every {0} seconds.",
        ["log.monitorStopped"] = "Recovery monitor stopped.",
        ["log.monitorProbeAttempt"] = "Recovery monitor probe attempt {0}...",
        ["log.monitorStillDown"] = "Service has not recovered: {0}",
        ["log.monitorRecovered"] = "Upstream probe recovered.",
        ["log.proxyAlreadyRunning"] = "Proxy is already running.",
        ["log.proxyExited"] = "Proxy process exited.",
        ["log.proxyExitedCode"] = "Proxy process exited with code {0}.",
        ["log.proxyStarted"] = "Proxy started at http://{0}:{1}/v1",
        ["log.diagnosticLogPath"] = "Safe backend diagnostic log directory: {0}",
        ["log.openedConfig"] = "Opened config: {0}",
        ["log.desktopNotFound"] = "Could not auto-detect Codex Desktop. Use scripts\\launch_codex_desktop_with_home.ps1 with -CodexDesktopPath.",
        ["log.vscodeNotFound"] = "Could not auto-detect VS Code. Ensure 'code' is in PATH or use the PowerShell launcher with -CodePath.",
        ["log.launchedDesktop"] = "Launched Codex Desktop with CODEX_HOME={0}",
        ["log.launchedVsCode"] = "Launched VS Code with CODEX_HOME={0}",
        ["log.startingOperation"] = "Starting {0}...",
        ["log.finishedOperation"] = "Finished {0}.",
        ["log.failedOperation"] = "Failed to {0}.",
        ["log.persistedProxyKey"] = "CODEX_GLM_PROXY_KEY persisted to Windows user environment.",
        ["log.proxyNotRunning"] = "Proxy is not running.",
        ["log.stopRequested"] = "Stop requested. Terminating proxy in background.",
        ["log.proxyTerminationCompleted"] = "Proxy termination completed.",
        ["log.proxyTerminationWarning"] = "Proxy termination warning: {0}: {1}",
        ["log.createdIsolatedConfig"] = "Created default isolated Codex config for glm_tokenhub_proxy.",
        ["log.healthSummary"] = "Health: {0}, model: {1}, tool calls: {2}",
        ["log.languageInstruction"] = "Response language instruction: {0}",
        ["log.metricsRequests"] = "Requests: started {0}, completed {1}, failed {2}",
        ["log.metricsChars"] = "Chars: request text {0}, response text {1}, response tool calls {2}, total {3}",
        ["log.metricsTokens"] = "Upstream usage tokens: prompt {0}, completion {1}, total {2}",
        ["log.diagnosticLogSummary"] = "Safe backend diagnostic log: enabled {0}, path {1}",
        ["log.metricsNote"] = "Note: char counts and usage stay in this proxy process memory only; dialogue text is not stored; chars are not tokenizer-exact token counts.",
        ["log.presetApplied"] = "Upstream preset switched: {0} -> {1}, model {2}, timeout {3}s, window {4}, compact {5}, output {6}",
        ["log.baseUrlAppliedTokenHub"] = "TokenHub Base URL applied.",
        ["log.baseUrlAppliedDeepSeek"] = "DeepSeek Base URL applied.",
        ["tooltip.upstreamPreset"] = "Current upstream preset: {0}",
        ["notify.recoveredTitle"] = "Upstream service recovered",
        ["notify.recoveredText"] = "All probe checks passed. You can restart the proxy or continue using Codex.",
        ["err.missingVenv"] = "Virtual environment is missing. Run Create .venv / Install dependencies first.",
        ["err.probeFailed"] = "Upstream text or stream probe failed. Check TOKENHUB_API_KEY, base URL, and model.",
        ["err.projectRootMissing"] = "Project root does not exist.",
        ["err.projectRootInvalid"] = "Project root does not look like TokenHubResponsesProxy.",
        ["err.tokenhubKeyRequired"] = "TOKENHUB_API_KEY is required.",
        ["err.proxyKeyRequired"] = "CODEX_GLM_PROXY_KEY is required. Generate one if needed.",
        ["button.writeConfig"] = "Write config",
        ["tooltip.writeConfig"] = "Force-write current context settings to both isolated CODEX_HOME config.toml files",
        ["log.configWritten"] = "Config written: {0}",
        ["op.writeConfig"] = "write config",
        ["err.codexHomeRequired"] = "CODEX_HOME path is required.",
    };

    private sealed record CommandResult(int ExitCode, string Output);
    private sealed record CodexHomeInfo(string Home, string Config);
    private sealed record ResponseLanguageOption(string Code, string ZhName, string EnName)
    {
        public bool UseEnglish { get; set; }
        public override string ToString() => UseEnglish ? EnName : ZhName;
    }
    private sealed record UpstreamPreset(
        string DisplayName,
        string BaseUrl,
        string Model,
        int TimeoutSeconds,
        int ContextWindowTokens,
        int AutoCompactTokenLimit,
        int MaxOutputTokens)
    {
        public override string ToString() => DisplayName;
    }
}
