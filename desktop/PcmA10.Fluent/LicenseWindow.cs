using System.IO;
using System.Windows;
using System.Windows.Controls;

namespace PcmA10.Fluent;

internal sealed class LicenseWindow : Window
{
    public int DocumentCount { get; private set; }
    public LicenseWindow(Window owner)
    {
        Owner = owner;
        Title = "许可证 · PCM-A10";
        Width = 660;
        Height = 520;
        MinWidth = 460;
        MinHeight = 340;
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        var tabs = new TabControl { Margin = new Thickness(12) };
        foreach (var (label, name) in new[]
        {
            ("项目", "Project"), ("第三方说明", "Notices"), ("Python", "Python"),
            ("PyInstaller", "PyInstaller"), (".NET Runtime", "DotNetRuntime"), ("WPF", "DotNetDesktop"),
            (".NET 第三方", "DotNetRuntimeNotices"), ("其他许可", "DotNet"), ("其他声明", "DotNet-ThirdPartyNotices")
        })
        {
            using var stream = typeof(LicenseWindow).Assembly.GetManifestResourceStream($"PcmA10.Licenses.{name}.txt")
                ?? throw new InvalidDataException("内置许可证缺失");
            using var reader = new StreamReader(stream);
            tabs.Items.Add(new TabItem { Header = label, Content = new TextBox
            {
                Text = reader.ReadToEnd(), IsReadOnly = true, TextWrapping = TextWrapping.Wrap,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto, Padding = new Thickness(10),
                FontFamily = new System.Windows.Media.FontFamily("Consolas"), FontSize = 12
            }});
            DocumentCount++;
        }
        Content = tabs;
    }
}
