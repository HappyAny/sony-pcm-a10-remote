using System.Windows;
using System.Windows.Automation.Peers;
using System.Windows.Media;
using System.Windows.Media.Animation;

namespace PcmA10.Fluent;

public sealed class LevelMeter : FrameworkElement
{
    public static readonly DependencyProperty ValueProperty = DependencyProperty.Register(
        nameof(Value), typeof(double), typeof(LevelMeter), new PropertyMetadata(-60d, ValueChanged));
    private static readonly DependencyProperty DisplayedValueProperty = DependencyProperty.Register(
        "DisplayedValue", typeof(double), typeof(LevelMeter), new FrameworkPropertyMetadata(-60d, FrameworkPropertyMetadataOptions.AffectsRender));
    public static readonly DependencyProperty OverProperty = DependencyProperty.Register(
        nameof(Over), typeof(bool), typeof(LevelMeter), new FrameworkPropertyMetadata(false, FrameworkPropertyMetadataOptions.AffectsRender));
    public double Value { get => (double)GetValue(ValueProperty); set => SetValue(ValueProperty, value); }
    public bool Over { get => (bool)GetValue(OverProperty); set => SetValue(OverProperty, value); }

    private static void ValueChanged(DependencyObject sender, DependencyPropertyChangedEventArgs args)
    {
        var meter = (LevelMeter)sender;
        var value = Math.Clamp((double)args.NewValue, -60, 0);
        if (!SystemParameters.ClientAreaAnimation)
        {
            meter.BeginAnimation(DisplayedValueProperty, null);
            meter.SetValue(DisplayedValueProperty, value);
            return;
        }
        meter.BeginAnimation(DisplayedValueProperty, new DoubleAnimation(value, TimeSpan.FromMilliseconds(130))
        { EasingFunction = new QuadraticEase { EasingMode = EasingMode.EaseOut } });
    }

    protected override void OnRender(DrawingContext drawing)
    {
        base.OnRender(drawing);
        var track = new Rect(0, 0, ActualWidth, ActualHeight);
        var background = TryFindResource("StrokeBrush") as Brush ?? Brushes.LightGray;
        drawing.DrawRoundedRectangle(background, null, track, 5, 5);
        var normalized = Math.Clamp(((double)GetValue(DisplayedValueProperty) + 60) / 60, 0, 1);
        if (normalized <= 0 || ActualWidth <= 0) return;
        var gradient = new LinearGradientBrush(new GradientStopCollection
        {
            new(Color.FromRgb(25, 151, 88), 0), new(Color.FromRgb(25, 151, 88), 0.68),
            new(Color.FromRgb(220, 172, 42), 0.82), new(Color.FromRgb(210, 63, 43), 0.98)
        }, new Point(0, 0), new Point(1, 0));
        drawing.PushClip(new RectangleGeometry(new Rect(0, 0, ActualWidth * normalized, ActualHeight), 5, 5));
        drawing.DrawRoundedRectangle(Over ? Brushes.IndianRed : gradient, null, track, 5, 5);
        drawing.Pop();
    }

    protected override AutomationPeer OnCreateAutomationPeer() => new FrameworkElementAutomationPeer(this);
}
