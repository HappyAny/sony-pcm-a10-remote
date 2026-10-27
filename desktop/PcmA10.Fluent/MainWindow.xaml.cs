using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Media.Imaging;
using System.Windows.Threading;
using Microsoft.Win32;

namespace PcmA10.Fluent;

public sealed record DeviceOption(string Name, string Address, bool Paired)
{
    public string DisplayName => Paired ? Name : Name + " · 未配对";
}
public sealed record ActivityEntry(string Text, string Time);

public partial class MainWindow : Window
{
    private readonly BackendClient backend = new();
    private readonly ObservableCollection<DeviceOption> devices = new();
    private readonly ObservableCollection<ActivityEntry> activity = new();
    private readonly DispatcherTimer renderTimer = new() { Interval = TimeSpan.FromMilliseconds(50) };
    private readonly DispatcherTimer pollTimer = new() { Interval = TimeSpan.FromSeconds(2) };
    private readonly object stateLock = new();
    private JsonElement? pendingState;
    private JsonElement? lastState;
    private readonly bool demo;
    private readonly bool selfTest;
    private bool serviceReady, ready, busy, polling, connecting, closing, allowClose;
    private int remoteStatus = 1;
    private bool rehearsalActive, rehearsalSupported, settingsLoaded;
    private string selectedTheme = "System";

    public MainWindow(bool demo, bool selfTest)
    {
        this.demo = demo || selfTest || Environment.GetCommandLineArgs().Contains("--lifetime-probe");
        this.selfTest = selfTest;
        InitializeComponent();
        Width = Math.Min(540, SystemParameters.WorkArea.Width - 24);
        Height = Math.Min(620, SystemParameters.WorkArea.Height - 24);
        MinHeight = Math.Min(520, SystemParameters.WorkArea.Height - 24);
        MinWidth = Math.Min(480, SystemParameters.WorkArea.Width - 24);
        Title = "PCM-A10" + (this.demo ? " · 模拟模式" : "");
        DemoBadge.Visibility = this.demo ? Visibility.Visible : Visibility.Collapsed;
        DeviceCombo.ItemsSource = devices;
        ActivityList.ItemsSource = activity;
        RecorderSettings.ChangeRequested += SetRecorderSettingAsync;
        RecorderSettings.RefreshRequested += RefreshSettingsAsync;
        backend.EventReceived += BackendEvent;
        backend.ServiceFailed += message => OnUi(() =>
        {
            serviceReady = false;
            RenderDisconnected();
            ShowNotice(message);
        });
        renderTimer.Tick += (_, _) =>
        {
            JsonElement? state;
            lock (stateLock) { state = pendingState; pendingState = null; }
            if (state.HasValue) RenderState(state.Value);
        };
        pollTimer.Tick += async (_, _) => await PollAsync();
        Loaded += async (_, _) => await InitializeAsync();
        SourceInitialized += (_, _) => ApplyWindowMaterial();
        Closing += WindowClosing;
        SystemEvents.UserPreferenceChanged += UserPreferenceChanged;
        ApplyTheme("System");
        UpdateControls();
    }

    private async Task InitializeAsync()
    {
        try
        {
            SetBusy(true, "正在准备录音控制…");
            await backend.StartAsync(demo);
            serviceReady = true;
            renderTimer.Start();
            await DiscoverAsync(false);
            if (demo) await ConnectAsync();
            pollTimer.Start();
        }
        catch (Exception error) { ShowNotice(error.Message); }
        finally { SetBusy(false); }
        if (selfTest) await RunSelfTestAsync();
        else if (Environment.GetCommandLineArgs().Contains("--lifetime-probe"))
        {
            if (!demo || !ready) throw new InvalidOperationException("Lifecycle probe requires the simulator");
            await ExecuteActionAsync("record");
            File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "lifetime-probe.json"), JsonSerializer.Serialize(new
            {
                mode = "simulation", frontendPid = Environment.ProcessId, backendPid = backend.ProcessId,
                workerPid = backend.WorkerProcessId, guard = backend.LifetimeGuardEstablished, remoteStatus,
                runtimeDirectory = backend.EmbeddedRuntimeDirectory
            }));
        }
    }

    private void OnUi(Action action)
    {
        if (!closing && !Dispatcher.HasShutdownStarted) Dispatcher.BeginInvoke(action);
    }

    private void BackendEvent(JsonElement data)
    {
        string kind = GetText(data, "event");
        if (kind == "state")
        {
            lock (stateLock) pendingState = data.GetProperty("state").Clone();
            return;
        }
        OnUi(() =>
        {
            switch (kind)
            {
                case "connected": AddActivity(demo ? "已连接模拟录音机" : "已连接 PCM-A10"); break;
                case "connection":
                    connecting = true;
                    ConnectionText.Text = "连接中";
                    RecorderHintText.Text = GetText(data, "phase", "正在连接录音机，请稍候");
                    break;
                case "disconnected":
                    connecting = false;
                    RenderDisconnected();
                    AddActivity("蓝牙遥控已断开");
                    break;
                case "action_confirmed":
                    string action = GetText(data, "action");
                    AddActivity(action switch
                    {
                        "record" => "录音已开始 / 继续", "pause" => "已进入暂停 / 待机", "stop" => "录音已停止",
                        "mark" => "已添加录音标记", "gain-up" => "录音电平已提高", "gain-down" => "录音电平已降低",
                        "gain" => "录音电平已设置", "rehearsal-start" => "自动试音已开始", "rehearsal-stop" => "自动试音已结束",
                        _ => "设备设置已更新"
                    });
                    break;
                case "setting_confirmed": AddActivity(GetText(data, "title") + "：" + GetText(data, "label")); break;
                case "recorder_notice": ShowNotice(GetText(data, "message")); break;
                case "error": case "protocol_error": ShowNotice(GetText(data, "message")); break;
            }
        });
    }

    private async Task DiscoverAsync(bool scan)
    {
        string? previous = (DeviceCombo.SelectedItem as DeviceOption)?.Address;
        JsonElement result = await backend.SendAsync("devices", new { scan });
        devices.Clear();
        foreach (var item in result.EnumerateArray())
            devices.Add(new DeviceOption(GetText(item, "name"), GetText(item, "address"), GetBool(item, "paired")));
        DeviceCombo.SelectedItem = devices.FirstOrDefault(device => device.Address == previous) ?? devices.FirstOrDefault();
        if (devices.Count == 0)
        {
            HelpPanel.Visibility = Visibility.Visible;
            AddActivity("未发现 PCM-A10，请先完成蓝牙配对");
        }
        else if (scan) AddActivity($"发现 {devices.Count} 台录音机");
    }

    private async Task ConnectAsync()
    {
        DeviceOption? device = DeviceCombo.SelectedItem as DeviceOption;
        if (device is null)
        {
            string address = DeviceCombo.Text.Trim();
            if (!System.Text.RegularExpressions.Regex.IsMatch(address, @"^(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}$"))
                throw new InvalidOperationException("请选择录音机，或输入蓝牙地址（AA:BB:CC:DD:EE:FF）。首次使用请先配对。");
            device = new DeviceOption("PCM-A10", address, true);
        }
        connecting = true;
        UpdateControls();
        try
        {
            var state = await backend.SendAsync("connect", new { address = device.Address }, seconds: 45);
            RenderState(state);
            ApplySettingsResponse(await backend.SendAsync("settings", seconds: 45));
        }
        finally { connecting = false; UpdateControls(); }
    }

    private async void ConnectClick(object sender, RoutedEventArgs e)
    {
        if (busy || !serviceReady) return;
        SetBusy(true, ready ? "正在断开遥控…" : "正在连接录音机…");
        HideNotice();
        try
        {
            if (ready) { await backend.SendAsync("disconnect"); RenderDisconnected(); }
            else await ConnectAsync();
        }
        catch (Exception error) { ShowNotice(error.Message); }
        finally { SetBusy(false); }
    }

    private async void ScanClick(object sender, RoutedEventArgs e)
    {
        if (busy) return;
        SetBusy(true, "正在搜索附近的 PCM-A10…");
        try { await DiscoverAsync(true); }
        catch (Exception error) { ShowNotice(error.Message); }
        finally { SetBusy(false); }
    }

    private async Task ExecuteActionAsync(string action, int? value = null, int markType = 1, string markName = "Mark")
    {
        if (busy || !ready) return;
        SetBusy(true, "正在同步设备操作…");
        HideNotice();
        try
        {
            var state = await backend.SendAsync("action", new { name = action, value, mark_type = markType, mark_name = markName }, seconds: 45);
            RenderState(state);
            if (action == "manual-input") ApplySettingsResponse(await backend.SendAsync("settings", seconds: 45));
        }
        catch (Exception error)
        {
            ShowNotice(error.Message);
            if (selfTest) throw;
        }
        finally { SetBusy(false); }
    }

    private Task ToggleRecordingAsync() => ExecuteActionAsync("record-toggle");
    private async void RecordClick(object sender, RoutedEventArgs e) => await ToggleRecordingAsync();
    private async void StandbyClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("pause");
    private async void StopClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("stop");
    private async void MarkClick(object sender, RoutedEventArgs e)
    {
        int kind = sender is Button button && int.TryParse(button.Tag?.ToString(), out int parsed) ? parsed : 1;
        string label = MarkNameText.Text.Trim();
        if (label.Length == 0) label = kind switch { 2 => "Important", 3 => "Question", 4 => "New Topic", _ => "Track Mark" };
        await ExecuteActionAsync("mark", markType: kind, markName: label);
    }
    private async void GainUpClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("gain-up");
    private async void GainDownClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("gain-down");
    private async void ApplyGainClick(object sender, RoutedEventArgs e)
    {
        if (!int.TryParse(GainText.Text, out int gain) || gain is < 0 or > 30) { ShowNotice("请输入 0 到 30 的整数电平。"); return; }
        await ExecuteActionAsync("gain", value: gain);
    }
    private async void RehearsalClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync(rehearsalActive ? "rehearsal-stop" : "rehearsal-start");
    private async void ManualInputClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("manual-input");
    private async void LastMarkClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("last-mark");
    private async void PeakResetClick(object sender, RoutedEventArgs e) => await ExecuteActionAsync("peak-reset");

    private void ApplySettingsResponse(JsonElement result)
    {
        RecorderSettings.Load(result);
        settingsLoaded = true;
        RenderState(result.GetProperty("state"));
    }

    private async Task RefreshSettingsAsync()
    {
        if (!ready || busy) return;
        SetBusy(true, "正在读取录音设置…");
        try { ApplySettingsResponse(await backend.SendAsync("settings", seconds: 45)); }
        catch (Exception error) { ShowNotice(error.Message); if (selfTest) throw; }
        finally { SetBusy(false); }
    }

    private async Task SetRecorderSettingAsync(string key, int value)
    {
        if (!ready || busy) return;
        if (key == "save-scene" && !selfTest && MessageBox.Show(this, $"保存当前设置会覆盖 My Scene {value} 的旧设置。", "保存自定义场景", MessageBoxButton.OKCancel, MessageBoxImage.Question) != MessageBoxResult.OK) return;
        SetBusy(true, "正在应用并读取设备确认…");
        HideNotice();
        try { ApplySettingsResponse(await backend.SendAsync("settings-set", new { key, value }, seconds: 45)); }
        catch (Exception error)
        {
            ShowNotice(error.Message);
            if (selfTest) throw;
            // A setting can affect other settings before a later step fails.
            try { ApplySettingsResponse(await backend.SendAsync("settings", seconds: 45)); } catch { }
        }
        finally { SetBusy(false); }
    }

    private async void MainTabChanged(object sender, SelectionChangedEventArgs e)
    {
        if (IsLoaded && e.Source == MainTabs && MainTabs.SelectedItem == RecordingSettingsTab && ready && !busy)
            await RefreshSettingsAsync();
    }

    private async Task PollAsync()
    {
        if (!ready || busy || polling || closing) return;
        polling = true;
        try
        {
            var state = await backend.SendAsync("refresh", seconds: 15);
            // State events provide the newest live values; the response also
            // covers periods when the recorder is stopped and sends no meter.
            if (!busy) RenderState(state);
        }
        catch (Exception error) { if (!closing) ShowNotice(error.Message); }
        finally { polling = false; }
    }

    private void RenderState(JsonElement state)
    {
        lastState = state.Clone();
        ready = GetBool(state, "ready");
        remoteStatus = GetInt(state, "remote_status") ?? 1;
        rehearsalActive = GetInt(state, "rehearsal_status") == 2;
        rehearsalSupported = state.TryGetProperty("features", out var supported) && GetBool(supported, "rehearsal");
        if (!ready)
        {
            UpdateControls();
            return;
        }
        RecorderStatusText.Text = rehearsalActive ? "自动试音中" : remoteStatus switch { 3 => "正在录音", 2 => "暂停 / 待机", _ => "已停止" };
        RecorderHintText.Text = demo ? "本地模拟数据，不会操作录音机" : remoteStatus switch
        {
            3 => "录音正在设备中进行", 2 => "录音已暂停，仍可监听输入电平", _ => "一切就绪，随时开始下一段录音"
        };
        if (rehearsalActive) RecorderHintText.Text = "请试奏最大音量段落；结束试音后可直接开始录音。";
        FilenameText.Text = GetText(state, "filename");
        if (FilenameText.Text.Length > 0) RecorderHintText.Text += " · " + FilenameText.Text;
        if (GetInt(state, "pre_record_seconds") is { } cached) RecorderHintText.Text += $" · 预录缓存 {cached} 秒";
        if (GetInt(state, "sleep_timer") == 1) RecorderHintText.Text += " · 睡眠定时器已开启";
        ElapsedText.Text = GetText(state, "elapsed", "00:00:00");
        RemainingText.Text = GetText(state, "remaining", "—");
        if (!GainText.IsKeyboardFocusWithin || busy) GainText.Text = GetInt(state, "gain")?.ToString() ?? "—";
        BatteryIcon.Status = BatteryDetailIcon.Status = GetInt(state, "battery") ?? -1;
        double? left = GetDouble(state, "left_db"), right = GetDouble(state, "right_db");
        LeftMeter.Value = left ?? -60;
        RightMeter.Value = right ?? -60;
        LeftMeter.Over = GetBool(state, "left_over");
        RightMeter.Over = GetBool(state, "right_over");
        LeftDbText.Text = left.HasValue ? left.Value.ToString("F1") : "—";
        RightDbText.Text = right.HasValue ? right.Value.ToString("F1") : "—";
        MeterHintText.Text = LeftMeter.Over || RightMeter.Over ? "输入过载，请降低录音电平" : remoteStatus == 1 ? "进入录音或待机后显示实时电平" : "设备实时返回的双声道输入电平";
        if (!LeftMeter.Over && !RightMeter.Over && GetDouble(state, "peak_left_db") is { } peakLeft && GetDouble(state, "peak_right_db") is { } peakRight)
            MeterHintText.Text = $"设备峰值  L {peakLeft:F1} / R {peakRight:F1} dBFS";
        PeakResetButton.Visibility = state.TryGetProperty("features", out var options) && GetBool(options, "remote_peak_reset") ? Visibility.Visible : Visibility.Collapsed;
        PeakResetButton.IsEnabled = ready && !busy;
        RecordingDot.Fill = ResourceBrush(remoteStatus == 3 ? "DangerBrush" : remoteStatus == 2 ? "AccentBrush" : "SecondaryBrush");
        if (remoteStatus != 3) RecordingDot.BeginAnimation(OpacityProperty, null);
        else if (!RecordingDot.HasAnimatedProperties && SystemParameters.ClientAreaAnimation)
            RecordingDot.BeginAnimation(OpacityProperty, new DoubleAnimation(1, 0.35, TimeSpan.FromSeconds(0.8)) { AutoReverse = true, RepeatBehavior = RepeatBehavior.Forever });
        RecordButtonText.Text = remoteStatus == 3 ? "暂停录音" : remoteStatus == 2 ? "继续录音" : "开始录音";
        RecordButtonGlyph.Text = remoteStatus == 3 ? "Ⅱ" : "●";
        System.Windows.Automation.AutomationProperties.SetName(RecordButton, RecordButtonText.Text);
        RehearsalButtonText.Text = rehearsalActive ? "结束试音" : "开始试音";
        RecorderSettings.UpdateValues(state);
        string input = GetInt(state, "input_status") switch { 4 => "内置麦克风", 1 => "外接麦克风", 2 => "音频输入", _ => "" };
        string format = RecorderSettings.CurrentLabel("rec-mode").Replace("当前：", "");
        InputSummaryText.Text = input + (format.Length > 0 ? " · " + format : "");
        if (GetBool(state, "scene_edited")) InputSummaryText.Text += " · 场景已修改";
        if (state.TryGetProperty("last_mark", out var mark) && mark.ValueKind == JsonValueKind.Object)
        {
            string kind = GetInt(mark, "type") switch { 2 => "重要", 3 => "问题", 4 => "新话题", _ => "普通标记" };
            LastMarkText.Text = $"#{GetInt(mark, "number")}  {GetText(mark, "time")}  {kind} · {GetText(mark, "name")}";
        }
        else LastMarkText.Text = "尚无标记";
        UpdateControls();
    }

    private void RenderDisconnected()
    {
        ready = false;
        lastState = null;
        remoteStatus = 1;
        rehearsalActive = rehearsalSupported = settingsLoaded = false;
        RecorderSettings.Clear();
        lock (stateLock) pendingState = null;
        RecorderStatusText.Text = "等待连接";
        RecorderHintText.Text = "连接录音机后，即可开始控制";
        ElapsedText.Text = "00:00:00";
        RemainingText.Text = GainText.Text = "—";
        BatteryIcon.Status = BatteryDetailIcon.Status = -1;
        RecordButtonText.Text = "开始录音";
        RecordButtonGlyph.Text = "●";
        LeftMeter.Value = RightMeter.Value = -60;
        LeftDbText.Text = RightDbText.Text = "—";
        InputSummaryText.Text = FilenameText.Text = "";
        LastMarkText.Text = "尚无标记";
        RecordingDot.BeginAnimation(OpacityProperty, null);
        RecordingDot.Fill = ResourceBrush("SecondaryBrush");
        UpdateControls();
    }

    private void SetBusy(bool value, string? label = null)
    {
        busy = value;
        UpdateControls();
        if (value && label is not null) FooterText.Text = label;
    }

    private void UpdateControls()
    {
        if (RecordButton is null) return;
        bool available = ready && !busy && !closing;
        RecordButton.IsEnabled = available;
        StandbyMenu.IsEnabled = RecordStandbyMenu.IsEnabled = available && remoteStatus == 1;
        StopButton.IsEnabled = available && remoteStatus != 1;
        MarkButton.IsEnabled = ImportantMarkButton.IsEnabled = QuestionMarkButton.IsEnabled = TopicMarkButton.IsEnabled = available && !rehearsalActive && remoteStatus is 2 or 3;
        GainUpButton.IsEnabled = GainDownButton.IsEnabled = ApplyGainButton.IsEnabled = GainText.IsEnabled = available && !rehearsalActive && remoteStatus is 2 or 3;
        ManualInputButton.IsEnabled = available && !rehearsalActive;
        LastMarkButton.IsEnabled = available;
        PeakResetButton.IsEnabled = available;
        RehearsalButton.IsEnabled = available && rehearsalSupported && (remoteStatus != 3 || rehearsalActive);
        RecorderSettings.SetAvailable(available, rehearsalActive);
        ConnectButton.IsEnabled = serviceReady && !busy && !closing;
        ConnectButton.Content = ready ? "断开连接" : "连接设备";
        ConnectButton.Background = ready ? ResourceBrush("InsetBrush") : ResourceBrush("AccentBrush");
        ConnectButton.Foreground = ResourceBrush(ready ? "PrimaryBrush" : "AccentTextBrush");
        ScanButton.IsEnabled = DeviceCombo.IsEnabled = serviceReady && !ready && !busy && !closing;
        ConnectionText.Text = ready ? (demo ? "模拟器已连接" : "蓝牙已连接") : connecting ? "连接中" : "未连接";
        ConnectionDot.Fill = ResourceBrush(ready ? "SuccessBrush" : "SecondaryBrush");
        if (!busy) FooterText.Text = demo ? "模拟模式 · 所有状态来自本地模拟器" : ready ? "蓝牙遥控已连接 · 录音保存在设备中" : "请选择已配对的录音机";
    }

    private void AddActivity(string message)
    {
        activity.Insert(0, new ActivityEntry(message, DateTime.Now.ToString("HH:mm:ss")));
        while (activity.Count > 60) activity.RemoveAt(activity.Count - 1);
    }

    private void ShowNotice(string message)
    {
        if (message.Contains("0xA1", StringComparison.OrdinalIgnoreCase) && message.Contains("增益"))
        {
            NoticeText.Text = "录音机拒绝调整电平（0xA1）。请检查设备的手动录音电平和输入设置。";
            GainHintText.Text = "设备拒绝调整，请检查输入设置";
        }
        else if (message.Contains("10049")) NoticeText.Text = "录音机可能正在被其他遥控窗口使用。请先断开其他连接，再重试。";
        else if (message.Contains("WinError 5")) NoticeText.Text = "无法访问 Windows 蓝牙接口。请通过桌面启动脚本运行，并确认蓝牙已打开。";
        else NoticeText.Text = message;
        NoticePanel.Visibility = Visibility.Visible;
        AddActivity(NoticeText.Text);
    }

    private void HideNotice() => NoticePanel.Visibility = Visibility.Collapsed;
    private void DismissNoticeClick(object sender, RoutedEventArgs e) => HideNotice();
    private void HelpClick(object sender, RoutedEventArgs e) => HelpPanel.Visibility = HelpPanel.Visibility == Visibility.Visible ? Visibility.Collapsed : Visibility.Visible;
    private void PairClick(object sender, RoutedEventArgs e) => Process.Start(new ProcessStartInfo("ms-settings:bluetooth") { UseShellExecute = true });
    private void SettingsClick(object sender, RoutedEventArgs e)
    {
        SettingsButton.ContextMenu.PlacementTarget = SettingsButton;
        SettingsButton.ContextMenu.IsOpen = true;
    }

    private void ThemeClick(object sender, RoutedEventArgs e)
    {
        if (sender is not MenuItem item || item.Tag is not string mode) return;
        foreach (var entry in SettingsButton.ContextMenu.Items.OfType<MenuItem>())
            if (entry.Tag is string) entry.IsChecked = entry == item;
        ApplyTheme(mode);
    }

    private async void TraceClick(object sender, RoutedEventArgs e)
    {
        if (!serviceReady) return;
        try
        {
            bool enabled = TraceMenu.IsChecked;
            await backend.SendAsync("trace", new { enabled });
            AddActivity(enabled ? "通信日志已开启" : "通信日志已关闭");
        }
        catch (Exception error) { TraceMenu.IsChecked = false; ShowNotice(error.Message); }
    }

    private void OpenLogsClick(object sender, RoutedEventArgs e)
    {
        try
        {
            string folder = Path.Combine(AppContext.BaseDirectory, "logs");
            Directory.CreateDirectory(folder);
            Process.Start(new ProcessStartInfo(folder) { UseShellExecute = true });
        }
        catch (Exception error) { ShowNotice(error.Message); }
    }

    private void LicensesClick(object sender, RoutedEventArgs e) => new LicenseWindow(this).ShowDialog();

    private void ApplyTheme(string theme)
    {
        selectedTheme = theme;
        Application.Current.ThemeMode = theme switch { "Light" => ThemeMode.Light, "Dark" => ThemeMode.Dark, _ => ThemeMode.System };
        bool dark = theme == "Dark" || (theme == "System" && IsSystemDark());
        var colors = dark ? new Dictionary<string, string>
        {
            ["CanvasBrush"]="#171B22", ["SurfaceBrush"]="#202630", ["InsetBrush"]="#282F3B", ["StrokeBrush"]="#343E4C",
            ["PrimaryBrush"]="#F3F5FA", ["SecondaryBrush"]="#AAB6C9", ["AccentBrush"]="#78A9FF", ["AccentSoftBrush"]="#24354E", ["AccentTextBrush"]="#14223D",
            ["SuccessBrush"]="#6ED7A0", ["WarningSurfaceBrush"]="#3D321C", ["WarningTextBrush"]="#F1C985"
        } : new Dictionary<string, string>
        {
            ["CanvasBrush"]="#F3F5F9", ["SurfaceBrush"]="#FFFFFF", ["InsetBrush"]="#F5F7FA", ["StrokeBrush"]="#E4E7ED",
            ["PrimaryBrush"]="#192230", ["SecondaryBrush"]="#657287", ["AccentBrush"]="#3268D4", ["AccentSoftBrush"]="#EDF3FF", ["AccentTextBrush"]="#FFFFFF",
            ["SuccessBrush"]="#107C41", ["WarningSurfaceBrush"]="#FFF6E5", ["WarningTextBrush"]="#8A4C00"
        };
        foreach (var item in colors)
        {
            var brush = new SolidColorBrush((Color)ColorConverter.ConvertFromString(item.Value));
            brush.Freeze();
            Application.Current.Resources[item.Key] = brush;
        }
        if (SystemParameters.HighContrast)
        {
            foreach (string key in new[] { "CanvasBrush", "SurfaceBrush", "InsetBrush" }) Application.Current.Resources[key] = SystemColors.WindowBrush;
            foreach (string key in new[] { "PrimaryBrush", "SecondaryBrush" }) Application.Current.Resources[key] = SystemColors.WindowTextBrush;
        }
        ApplyWindowMaterial();
        UpdateControls();
    }

    private static bool IsSystemDark() => Registry.GetValue(@"HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "AppsUseLightTheme", 1) is int value && value == 0;
    private void UserPreferenceChanged(object sender, UserPreferenceChangedEventArgs e)
    {
        if (selectedTheme == "System") OnUi(() => ApplyTheme("System"));
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);
    private void ApplyWindowMaterial()
    {
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero) return;
        int dark = selectedTheme == "Dark" || (selectedTheme == "System" && IsSystemDark()) ? 1 : 0;
        int rounded = 2, mica = 2;
        DwmSetWindowAttribute(handle, 20, ref dark, sizeof(int));
        DwmSetWindowAttribute(handle, 33, ref rounded, sizeof(int));
        DwmSetWindowAttribute(handle, 38, ref mica, sizeof(int));
    }

    private async void WindowClosing(object? sender, CancelEventArgs e)
    {
        if (allowClose) return;
        e.Cancel = true;
        if (closing) return;
        closing = true;
        SetBusy(true, "正在断开蓝牙并释放设备…");
        MainTabs.IsEnabled = ConnectButton.IsEnabled = ScanButton.IsEnabled = false;
        renderTimer.Stop();
        pollTimer.Stop();
        SystemEvents.UserPreferenceChanged -= UserPreferenceChanged;
        try { await backend.DisposeAsync(); }
        finally
        {
            allowClose = true;
            // Cleanup can finish synchronously when the backend already exited.
            // Let this Closing event return before asking WPF to close again.
            _ = Dispatcher.BeginInvoke(new Action(Close));
        }
    }

    private async Task RunSelfTestAsync()
    {
        var steps = new List<object>();
        var batteryRules = new List<object>();
        int settingControls = 0;
        int licenseDocuments = 0;
        bool success = false;
        string? error = null;
        try
        {
            if (!demo || !ready || !settingsLoaded) throw new InvalidOperationException("Self-test requires the connected simulator and loaded settings");
            var licenses = new LicenseWindow(this);
            licenseDocuments = licenses.DocumentCount;
            licenses.Close();
            if (licenseDocuments != 9) throw new InvalidOperationException("Embedded licenses are incomplete");
            var initial = await backend.SendAsync("settings");
            foreach (var row in initial.GetProperty("settings").EnumerateArray())
            {
                if (GetText(row, "kind") == "action") continue;
                string key = GetText(row, "key");
                int value = row.GetProperty("choices").EnumerateArray().Last().GetProperty("value").GetInt32();
                await SetRecorderSettingAsync(key, value);
                if (lastState is not { } state || state.GetProperty("settings").GetProperty(key).GetInt32() != value)
                    throw new InvalidOperationException("Setting readback failed: " + key);
                steps.Add(new { setting = key, value, display = RecorderSettings.CurrentLabel(key) });
            }
            await SetRecorderSettingAsync("save-scene", 2);
            await SetRecorderSettingAsync("rec-mode", 13);
            await ExecuteActionAsync("rehearsal-start");
            if (!rehearsalActive) throw new InvalidOperationException("Rehearsal state did not reach the UI");
            steps.Add(new { action = "rehearsal-start", remoteStatus, visibleStatus = RecorderStatusText.Text });
            await ExecuteActionAsync("rehearsal-stop");
            if (rehearsalActive) throw new InvalidOperationException("Rehearsal did not end");
            await ExecuteActionAsync("gain", value: 20);
            if (GainText.Text != "20") throw new InvalidOperationException("Direct gain input did not update");
            for (int kind = 1; kind <= 4; kind++)
            {
                await ExecuteActionAsync("mark", markType: kind, markName: "测试 " + kind);
                if (lastState is not { } state || state.GetProperty("last_mark").GetProperty("type").GetInt32() != kind)
                    throw new InvalidOperationException("Track mark type did not update");
                steps.Add(new { action = "mark", kind, visibleMark = LastMarkText.Text });
            }
            await ExecuteActionAsync("last-mark");
            await ExecuteActionAsync("stop");
            foreach (var (expected, label) in new[] { (3, "暂停录音"), (2, "继续录音"), (3, "暂停录音") })
            {
                await ToggleRecordingAsync();
                if (remoteStatus != expected || RecordButtonText.Text != label || !RecordButton.IsEnabled)
                    throw new InvalidOperationException("Unified record/pause button did not update");
                steps.Add(new { action = "record-toggle", remoteStatus, visibleButton = RecordButtonText.Text });
            }
            await ExecuteActionAsync("stop");
            await ExecuteActionAsync("pause");
            await PollAsync();
            if (FilenameText.Text.Length == 0) throw new InvalidOperationException("Filename did not reach the native UI");
            ApplyTheme("Light");
            await Task.Delay(250);
            for (int attempt = 0; attempt < 30 && LeftDbText.Text == "—"; attempt++) await Task.Delay(50);
            foreach (var (status, cells, color) in new[] { (4, 4, "green"), (3, 3, "green"), (2, 2, "yellow"), (1, 1, "red"), (5, 4, "green"), (6, 0, "unknown") })
            {
                var icon = new BatteryIndicator { Status = status };
                if (icon.FilledCells != cells || icon.ColorName != color) throw new InvalidOperationException("Battery icon rule failed");
                batteryRules.Add(new { status, cells, color });
            }
            if (Title.Contains("Fluent")) throw new InvalidOperationException("Technical name remained in the title");
            if (Environment.GetCommandLineArgs().Contains("--capture-preview")) CapturePreview("preview-light.png");
            if (Environment.GetCommandLineArgs().Contains("--capture-preview")) CaptureBatteryPreview();
            ApplyTheme("Dark");
            await Task.Delay(180);
            if (Environment.GetCommandLineArgs().Contains("--capture-preview")) CapturePreview("preview-dark.png");
            MainTabs.SelectedItem = RecordingSettingsTab;
            while (busy) await Task.Delay(30);
            await Task.Delay(120);
            if (RecorderSettings.ControlCount != 16) throw new InvalidOperationException("Expected all 16 A10 setting controls");
            settingControls = RecorderSettings.ControlCount;
            if (Environment.GetCommandLineArgs().Contains("--capture-preview")) CapturePreview("preview-settings.png");
            MainTabs.SelectedIndex = 0;
            Width = 480;
            Height = 520;
            await Task.Delay(150);
            if (Environment.GetCommandLineArgs().Contains("--capture-preview")) CapturePreview("preview-small.png");
            Width = 540;
            Height = 620;
            ApplyTheme("System");
            await ExecuteActionAsync("record");
            await backend.DisposeAsync();
            if (!backend.EmbeddedRuntimeRemoved) throw new InvalidOperationException("Embedded backend runtime was not cleaned up");
            if (!backend.ShutdownAcknowledged || !backend.BackendExited || backend.ClosedRecorderState is not { } closedState ||
                GetBool(closedState, "ready") || GetInt(closedState, "remote_status") != 3)
                throw new InvalidOperationException("Shutdown did not disconnect and exit while preserving recorder state");
            success = true;
        }
        catch (Exception exception) { error = exception.Message; ShowNotice(error); }
        File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "self-test.json"), JsonSerializer.Serialize(
            new { success, release = typeof(MainWindow).Assembly.GetName().Version?.ToString(), mode = "simulation", frontend = "WPF .NET 10 official Fluent theme", steps,
                  supportedSettingControls = settingControls, themeSwitchingTested = success, unifiedRecordButton = success, batteryRules, licenseDocuments,
                  deployment = new { embeddedBackend = backend.UsesEmbeddedBackend, runtimeVersion = Environment.Version.ToString(),
                                     backendRuntimeDirectory = backend.EmbeddedRuntimeDirectory, backendRuntimeRemoved = backend.EmbeddedRuntimeRemoved },
                  shutdown = new { acknowledged = backend.ShutdownAcknowledged, backendExited = backend.BackendExited,
                                   lifetimeGuard = backend.LifetimeGuardEstablished, recorderState = backend.ClosedRecorderState },
                  defaultWindow = new { width = 540, height = 620 }, minimumWindow = new { width = 480, height = 520 },
                  previewsAreOwnSimulatorRendering = true, hardwareCommandsSent = false, error },
            new JsonSerializerOptions { WriteIndented = true }));
        if (Environment.GetCommandLineArgs().Contains("--exit-after-self-test"))
        {
            Environment.ExitCode = success ? 0 : 1;
            Close();
        }
    }

    private void CaptureBatteryPreview()
    {
        var drawing = new DrawingVisual();
        using (var context = drawing.RenderOpen())
        {
            context.DrawRectangle(ResourceBrush("CanvasBrush"), null, new Rect(0, 0, 340, 64));
            int index = 0;
            foreach (int status in new[] { 4, 3, 2, 1, 6 })
            {
                var icon = new BatteryIndicator { Status = status };
                icon.Measure(new Size(39, 18));
                icon.Arrange(new Rect(0, 0, 39, 18));
                context.DrawRectangle(new VisualBrush(icon), null, new Rect(14 + index * 66, 30, 39, 18));
                context.DrawText(new FormattedText(status == 6 ? "充电" : status + " 格", System.Globalization.CultureInfo.GetCultureInfo("zh-CN"),
                    FlowDirection.LeftToRight, new Typeface("Microsoft YaHei UI"), 11, ResourceBrush("PrimaryBrush"), 1), new Point(14 + index * 66, 8));
                index++;
            }
        }
        var bitmap = new RenderTargetBitmap(340, 64, 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(drawing);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var output = File.Create(Path.Combine(AppContext.BaseDirectory, "preview-battery.png"));
        encoder.Save(output);
    }

    private void CapturePreview(string name)
    {
        // Render only this app's own simulated WPF content, not the desktop.
        var root = (FrameworkElement)Content;
        root.UpdateLayout();
        var drawing = new DrawingVisual();
        var size = new Size(root.ActualWidth + 28, root.ActualHeight + 18);
        using (var context = drawing.RenderOpen())
        {
            context.DrawRectangle(ResourceBrush("CanvasBrush"), null, new Rect(size));
            context.DrawRectangle(new VisualBrush(root) { Stretch = Stretch.Fill }, null, new Rect(14, 10, root.ActualWidth, root.ActualHeight));
        }
        var bitmap = new RenderTargetBitmap((int)Math.Ceiling(size.Width), (int)Math.Ceiling(size.Height), 96, 96, PixelFormats.Pbgra32);
        bitmap.Render(drawing);
        var encoder = new PngBitmapEncoder();
        encoder.Frames.Add(BitmapFrame.Create(bitmap));
        using var output = File.Create(Path.Combine(AppContext.BaseDirectory, name));
        encoder.Save(output);
    }

    private Brush ResourceBrush(string key) => (Brush)FindResource(key);
    private static string GetText(JsonElement value, string key, string fallback = "") => value.TryGetProperty(key, out var property) && property.ValueKind == JsonValueKind.String ? property.GetString() ?? fallback : fallback;
    private static bool GetBool(JsonElement value, string key) => value.TryGetProperty(key, out var property) && property.ValueKind == JsonValueKind.True;
    private static int? GetInt(JsonElement value, string key) => value.TryGetProperty(key, out var property) && property.ValueKind == JsonValueKind.Number && property.TryGetInt32(out int result) ? result : null;
    private static double? GetDouble(JsonElement value, string key) => value.TryGetProperty(key, out var property) && property.ValueKind == JsonValueKind.Number && property.TryGetDouble(out double result) ? result : null;
}
