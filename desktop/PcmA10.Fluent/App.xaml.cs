using System.Windows;
using System.IO;

namespace PcmA10.Fluent;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        if (e.Args.Contains("--self-test"))
            DispatcherUnhandledException += (_, args) =>
            {
                // Preserve the original failure; this handler does not suppress it.
                try { File.WriteAllText(Path.Combine(AppContext.BaseDirectory, "self-test-error.txt"), args.Exception.ToString()); }
                catch (IOException) { }
            };
        var window = new MainWindow(e.Args.Contains("--demo"), e.Args.Contains("--self-test"));
        MainWindow = window;
        window.Show();
    }
}
