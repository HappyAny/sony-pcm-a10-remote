using System.IO;
using System.IO.Compression;
using System.Reflection;

namespace PcmA10.Fluent;

// One private temporary runtime per window. The distribution remains one EXE.
internal sealed class EmbeddedBackend : IDisposable
{
    private const string ResourceName = "PcmA10.BackendPayload.zip";
    private const string MarkerName = ".pcma10-runtime";
    private const string MarkerText = "PCM-A10 embedded backend v1";
    private static readonly string RuntimeRoot = Path.GetFullPath(Path.Combine(Path.GetTempPath(), "PCM-A10", "backend"));
    private FileStream? lease;
    public string DirectoryPath { get; }
    public string ExecutablePath => Path.Combine(DirectoryPath, "PCM-A10-Backend.exe");
    public bool Removed => !Directory.Exists(DirectoryPath);

    public static bool Available => Assembly.GetExecutingAssembly().GetManifestResourceInfo(ResourceName) is not null;

    public EmbeddedBackend()
    {
        Directory.CreateDirectory(RuntimeRoot);
        CleanupStale();
        DirectoryPath = Path.Combine(RuntimeRoot, $"run-{Environment.ProcessId}-{Guid.NewGuid():N}");
        Directory.CreateDirectory(DirectoryPath);
        try
        {
            lease = new FileStream(Path.Combine(DirectoryPath, MarkerName), FileMode.CreateNew, FileAccess.ReadWrite, FileShare.None);
            using var writer = new StreamWriter(lease, leaveOpen: true);
            writer.Write(MarkerText);
            writer.Flush();
        }
        catch
        {
            lease?.Dispose();
            throw;
        }
    }

    public void Prepare(CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        using var payload = Assembly.GetExecutingAssembly().GetManifestResourceStream(ResourceName)
            ?? throw new FileNotFoundException("便携版内置控制服务缺失，请重新下载程序。");
        ZipFile.ExtractToDirectory(payload, DirectoryPath);
        cancellation.ThrowIfCancellationRequested();
        if (!File.Exists(ExecutablePath)) throw new InvalidDataException("便携版控制服务不完整，请重新下载程序。");
    }

    public void Dispose()
    {
        lease?.Dispose();
        lease = null;
        DeleteOwnedDirectory(DirectoryPath);
    }

    private static void CleanupStale()
    {
        // A live window holds its marker exclusively. Its runtime is never removed.
        foreach (string path in Directory.EnumerateDirectories(RuntimeRoot, "run-*", SearchOption.TopDirectoryOnly).Take(64))
        {
            try
            {
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) continue;
                using (var probe = new FileStream(Path.Combine(path, MarkerName), FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                using (var reader = new StreamReader(probe))
                    if (reader.ReadToEnd() != MarkerText) continue;
                DeleteOwnedDirectory(path);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    private static void DeleteOwnedDirectory(string path)
    {
        try
        {
            string full = Path.GetFullPath(path);
            // Recursive deletion is restricted to our marked, direct child directory.
            if (!string.Equals(Path.GetDirectoryName(full), RuntimeRoot, StringComparison.OrdinalIgnoreCase) ||
                !Path.GetFileName(full).StartsWith("run-", StringComparison.Ordinal) ||
                !Directory.Exists(full) || (File.GetAttributes(full) & FileAttributes.ReparsePoint) != 0 ||
                File.ReadAllText(Path.Combine(full, MarkerName)) != MarkerText) return;
            Directory.Delete(full, recursive: true);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }
}
