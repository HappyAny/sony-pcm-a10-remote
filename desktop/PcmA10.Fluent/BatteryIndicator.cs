using System.Windows;
using System.Windows.Automation.Peers;
using System.Windows.Media;

namespace PcmA10.Fluent;

public sealed class BatteryIndicator : FrameworkElement
{
    public static readonly DependencyProperty StatusProperty = DependencyProperty.Register(nameof(Status), typeof(int), typeof(BatteryIndicator),
        new FrameworkPropertyMetadata(-1, FrameworkPropertyMetadataOptions.AffectsRender, StatusChanged));
    public int Status { get => (int)GetValue(StatusProperty); set => SetValue(StatusProperty, value); }
    public int FilledCells => Status switch { >= 1 and <= 4 => Status, 5 => 4, 7 or 9 => 1, _ => 0 };
    public string ColorName => FilledCells switch { >= 3 => "green", 2 => "yellow", 1 => "red", _ => "unknown" };
    public bool IsCharging => Status == 6;
    public string StatusText => Status switch
    {
        >= 1 and <= 4 => $"电池：{FilledCells}/4 格", 5 => "电池已充满，4/4 格",
        6 => "正在充电，设备未提供电量档位", 7 or 9 => "电量低，1/4 格",
        8 => "电池温度限制", _ => "电量未知"
    };

    public BatteryIndicator()
    {
        Width = 39;
        Height = 18;
        ToolTip = StatusText;
    }

    private static void StatusChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var battery = (BatteryIndicator)sender;
        battery.ToolTip = battery.StatusText;
    }

    protected override void OnRender(DrawingContext drawing)
    {
        base.OnRender(drawing);
        var outline = TryFindResource("SecondaryBrush") as Brush ?? Brushes.Gray;
        var empty = TryFindResource("StrokeBrush") as Brush ?? Brushes.LightGray;
        var filled = new SolidColorBrush(ColorName switch
        {
            "green" => Color.FromRgb(23, 160, 85), "yellow" => Color.FromRgb(230, 184, 0),
            "red" => Color.FromRgb(215, 59, 62), _ => Colors.Gray
        });
        drawing.DrawRoundedRectangle(null, new Pen(outline, 1), new Rect(0.5, 1.5, 34, 15), 2, 2);
        drawing.DrawRoundedRectangle(outline, null, new Rect(35.5, 6, 2.5, 6), 0.7, 0.7);
        for (int cell = 0; cell < 4; cell++)
            drawing.DrawRoundedRectangle(cell < FilledCells ? filled : empty, null, new Rect(3 + cell * 7.25, 4.5, 5.5, 9), 0.7, 0.7);
        if (IsCharging)
        {
            var bolt = Geometry.Parse("M19,2 L12,10 L17,10 L15,17 L23,7 L18,7 Z");
            drawing.DrawGeometry(TryFindResource("AccentBrush") as Brush ?? Brushes.DodgerBlue, null, bolt);
        }
    }

    protected override AutomationPeer OnCreateAutomationPeer() => new BatteryPeer(this);
    private sealed class BatteryPeer(BatteryIndicator battery) : FrameworkElementAutomationPeer(battery)
    {
        protected override string GetNameCore() => battery.StatusText;
        protected override string GetClassNameCore() => nameof(BatteryIndicator);
    }
}
