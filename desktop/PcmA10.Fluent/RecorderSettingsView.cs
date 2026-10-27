using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation;
using System.Windows.Media;

namespace PcmA10.Fluent;

public sealed record SettingChoice(int Value, string Label);

public sealed class RecorderSettingsView : UserControl
{
    private readonly StackPanel groups = new();
    private readonly TextBlock summary = new() { Text = "连接后读取录音设置", FontSize = 11, VerticalAlignment = VerticalAlignment.Center };
    private readonly Button refresh = new() { Content = "刷新设置", Padding = new Thickness(9, 4, 9, 4), Height = 29, IsEnabled = false };
    private readonly Dictionary<string, (ComboBox Combo, Button Apply, TextBlock Current, Dictionary<int, string> Labels)> rows = new();
    private bool available;
    public event Func<string, int, Task>? ChangeRequested;
    public event Func<Task>? RefreshRequested;
    public int ControlCount => rows.Count;
    public IEnumerable<string> Keys => rows.Keys;

    public RecorderSettingsView()
    {
        summary.SetResourceReference(TextBlock.ForegroundProperty, "SecondaryBrush");
        var root = new DockPanel { Margin = new Thickness(0, 5, 0, 0) };
        var toolbar = new DockPanel { Margin = new Thickness(1, 0, 1, 7), LastChildFill = true };
        DockPanel.SetDock(refresh, Dock.Right);
        toolbar.Children.Add(refresh);
        toolbar.Children.Add(summary);
        DockPanel.SetDock(toolbar, Dock.Top);
        root.Children.Add(toolbar);
        root.Children.Add(new ScrollViewer { Content = groups, VerticalScrollBarVisibility = ScrollBarVisibility.Auto, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, Padding = new Thickness(0, 0, 2, 0) });
        Content = root;
        refresh.Click += async (_, _) => { if (RefreshRequested is { } handler) await handler(); };
    }

    private static TextBlock Caption(string text, double size = 10)
    {
        var caption = new TextBlock { Text = text, FontSize = size, TextWrapping = TextWrapping.Wrap };
        caption.SetResourceReference(TextBlock.ForegroundProperty, "SecondaryBrush");
        return caption;
    }

    public void Load(JsonElement result)
    {
        groups.Children.Clear();
        rows.Clear();
        foreach (var grouping in result.GetProperty("settings").EnumerateArray().GroupBy(row => row.GetProperty("group").GetString()))
        {
            var body = new StackPanel();
            body.Children.Add(new TextBlock { Text = grouping.Key, FontSize = 12, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 3) });
            foreach (var row in grouping)
            {
                string key = row.GetProperty("key").GetString()!, title = row.GetProperty("title").GetString()!;
                bool action = row.GetProperty("kind").GetString() == "action";
                var choices = row.GetProperty("choices").EnumerateArray().Select(choice => new SettingChoice(choice.GetProperty("value").GetInt32(), choice.GetProperty("label").GetString()!)).ToList();
                var line = new Grid { Margin = new Thickness(0, 6, 0, 4) };
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(192) });
                line.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
                var label = new StackPanel { VerticalAlignment = VerticalAlignment.Center, Margin = new Thickness(0, 0, 9, 0) };
                label.Children.Add(new TextBlock { Text = title, FontSize = 12, TextWrapping = TextWrapping.Wrap });
                var current = Caption(action ? "保存会覆盖所选场景" : "当前：" + row.GetProperty("display_value").GetString());
                current.Margin = new Thickness(0, 3, 0, 0);
                label.Children.Add(current);
                line.Children.Add(label);
                var combo = new ComboBox { ItemsSource = choices, DisplayMemberPath = "Label", SelectedValuePath = "Value", MinHeight = 30, VerticalAlignment = VerticalAlignment.Center, FontSize = 11 };
                AutomationProperties.SetName(combo, title + "选项");
                AutomationProperties.SetAutomationId(combo, "setting-" + key);
                var confirmed = row.GetProperty("value");
                if (confirmed.ValueKind == JsonValueKind.Number) combo.SelectedValue = confirmed.GetInt32();
                else if (action) combo.SelectedIndex = 0;
                Grid.SetColumn(combo, 1);
                line.Children.Add(combo);
                var apply = new Button { Content = action ? "保存" : "应用", MinHeight = 30, Padding = new Thickness(9, 3, 9, 3), Margin = new Thickness(6, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
                AutomationProperties.SetName(apply, (action ? "保存" : "应用") + title);
                Grid.SetColumn(apply, 2);
                line.Children.Add(apply);
                string hint = row.GetProperty("hint").GetString() ?? "";
                line.ToolTip = hint.Length > 0 ? hint : title;
                if (result.TryGetProperty("errors", out var errors) && errors.TryGetProperty(key, out var error))
                {
                    current.Text = "读取失败，可刷新重试";
                    current.ToolTip = error.GetString();
                }
                apply.Click += async (_, _) =>
                {
                    if (combo.SelectedItem is not SettingChoice choice || ChangeRequested is not { } handler) return;
                    await handler(key, choice.Value);
                };
                rows[key] = (combo, apply, current, choices.ToDictionary(choice => choice.Value, choice => choice.Label));
                body.Children.Add(line);
            }
            var card = new Border { Child = body, Padding = new Thickness(12, 9, 12, 9), Margin = new Thickness(0, 0, 0, 7) };
            card.SetResourceReference(StyleProperty, "Card");
            groups.Children.Add(card);
        }
        summary.Text = $"{rows.Count} 项控制 · 选项由录音机返回";
        if (result.TryGetProperty("additional_capabilities", out var additional) && additional.GetArrayLength() > 0)
        {
            var detail = Caption("以下扩展项没有已确认的操作语义，仅保留能力记录，不向它们写入命令：\n" + string.Join("、", additional.EnumerateArray().Select(item => item.GetProperty("item").GetString())));
            groups.Children.Add(new Expander { Header = "扩展能力记录", Content = detail, FontSize = 10, Margin = new Thickness(0, 2, 0, 5) });
        }
        SetAvailable(available, false);
    }

    public void UpdateValues(JsonElement state)
    {
        if (!state.TryGetProperty("settings", out var settings)) return;
        foreach (var (key, row) in rows)
        {
            if (key == "save-scene" || !settings.TryGetProperty(key, out var value) || value.ValueKind != JsonValueKind.Number) continue;
            int number = value.GetInt32();
            row.Current.Text = "当前：" + row.Labels.GetValueOrDefault(number, $"设备值 {number}");
        }
    }

    public void SetAvailable(bool value, bool rehearsal)
    {
        available = value;
        refresh.IsEnabled = value;
        foreach (var row in rows.Values) row.Combo.IsEnabled = row.Apply.IsEnabled = value && !rehearsal;
    }

    public string CurrentLabel(string key) => rows.TryGetValue(key, out var row) ? row.Current.Text : "";
    public void Clear()
    {
        groups.Children.Clear();
        rows.Clear();
        summary.Text = "连接后读取录音设置";
        SetAvailable(false, false);
    }
}
