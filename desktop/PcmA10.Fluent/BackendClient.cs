using System.Collections.Concurrent;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace PcmA10.Fluent;

public sealed class BackendClient : IAsyncDisposable
{
    private Process? process;
    private OwnedBackendJob? lifetimeJob;
    private readonly SemaphoreSlim writerLock = new(1, 1);
    private readonly ConcurrentDictionary<int, TaskCompletionSource<JsonElement>> pending = new();
    private readonly TaskCompletionSource<bool> startup = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private int nextId;
    private volatile bool disposing;
    private readonly object disposeLock = new();
    private Task? disposeTask;
    private readonly CancellationTokenSource lifetimeCancellation = new();
    private EmbeddedBackend? embeddedBackend;
    private Task? runtimePreparation;
    public int ProcessId { get; private set; }
    public int WorkerProcessId { get; private set; }
    public bool LifetimeGuardEstablished { get; private set; }
    public bool ShutdownAcknowledged { get; private set; }
    public bool BackendExited { get; private set; }
    public JsonElement? ClosedRecorderState { get; private set; }
    public bool UsesEmbeddedBackend => embeddedBackend is not null;
    public string? EmbeddedRuntimeDirectory => embeddedBackend?.DirectoryPath;
    public bool EmbeddedRuntimeRemoved => embeddedBackend?.Removed ?? true;
    public event Action<JsonElement>? EventReceived;
    public event Action<string>? ServiceFailed;

    public async Task StartAsync(bool demo)
    {
        if (disposing) throw new ObjectDisposedException(nameof(BackendClient));
        string appDirectory = AppContext.BaseDirectory;
        string executable = Path.Combine(appDirectory, "runtime", "PCM-A10-Backend.exe");
        var info = new ProcessStartInfo
        {
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            StandardInputEncoding = new UTF8Encoding(false), StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8, WorkingDirectory = appDirectory
        };
        if (EmbeddedBackend.Available)
        {
            embeddedBackend = new EmbeddedBackend();
            runtimePreparation = Task.Run(() => embeddedBackend.Prepare(lifetimeCancellation.Token));
            await runtimePreparation;
            lifetimeCancellation.Token.ThrowIfCancellationRequested();
            info.FileName = embeddedBackend.ExecutablePath;
        }
        else if (File.Exists(executable))
        {
            info.FileName = executable;
        }
        else
        {
            var folder = new DirectoryInfo(appDirectory);
            while (folder is not null && !File.Exists(Path.Combine(folder.FullName, "backend.py"))) folder = folder.Parent;
            if (folder is null) throw new FileNotFoundException("未找到控制服务，请完整解压程序目录。");
            info.FileName = "python.exe";
            info.ArgumentList.Add(Path.Combine(folder.FullName, "backend.py"));
            info.WorkingDirectory = folder.FullName;
        }
        if (demo) info.ArgumentList.Add("--demo");
        info.Environment["PYTHONUNBUFFERED"] = "1";
        process = Process.Start(info) ?? throw new IOException("无法启动录音控制服务");
        ProcessId = process.Id;
        try
        {
            lifetimeJob = new OwnedBackendJob(process);
            LifetimeGuardEstablished = true;
        }
        catch
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
            process.Dispose();
            process = null;
            throw new IOException("无法建立录音控制服务的退出保护，请重新打开程序。");
        }
        _ = ReadAsync(process);
        _ = ReadErrorsAsync(process);
        await startup.Task.WaitAsync(TimeSpan.FromSeconds(12));
    }

    public async Task<JsonElement> SendAsync(string method, object? parameters = null, int seconds = 25)
    {
        if (disposing) throw new ObjectDisposedException(nameof(BackendClient), "录音控制服务正在关闭");
        return await SendCoreAsync(method, parameters, seconds);
    }

    private async Task<JsonElement> SendCoreAsync(string method, object? parameters, int seconds)
    {
        if (process is null || process.HasExited) throw new IOException("录音控制服务未运行");
        int id = Interlocked.Increment(ref nextId);
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        pending[id] = completion;
        try
        {
            await writerLock.WaitAsync();
            try
            {
                if (disposing && method != "shutdown") throw new ObjectDisposedException(nameof(BackendClient));
                await process.StandardInput.WriteLineAsync(JsonSerializer.Serialize(new { id, method, @params = parameters ?? new { } }));
                await process.StandardInput.FlushAsync();
            }
            finally { writerLock.Release(); }
            return await completion.Task.WaitAsync(TimeSpan.FromSeconds(seconds));
        }
        finally { pending.TryRemove(id, out _); }
    }

    private async Task ReadAsync(Process child)
    {
        try
        {
            while (await child.StandardOutput.ReadLineAsync() is { } line)
            {
                using var document = JsonDocument.Parse(line);
                var message = document.RootElement;
                switch (message.GetProperty("type").GetString())
                {
                    case "ready":
                        if (message.TryGetProperty("pid", out var workerPid) && workerPid.TryGetInt32(out int pid)) WorkerProcessId = pid;
                        startup.TrySetResult(true);
                        break;
                    case "event": EventReceived?.Invoke(message.GetProperty("data").Clone()); break;
                    case "response":
                        if (!message.TryGetProperty("id", out var responseId) || responseId.ValueKind != JsonValueKind.Number || !responseId.TryGetInt32(out int id) || !pending.TryGetValue(id, out var completion)) break;
                        if (message.GetProperty("ok").GetBoolean()) completion.TrySetResult(message.GetProperty("result").Clone());
                        else completion.TrySetException(new InvalidOperationException(message.GetProperty("error").GetProperty("message").GetString()));
                        break;
                }
            }
            Fail("录音控制服务已退出，请重新打开程序");
        }
        catch (Exception error) { Fail(error.Message); }
    }

    private async Task ReadErrorsAsync(Process child)
    {
        try
        {
            string errors = await child.StandardError.ReadToEndAsync();
            if (!disposing && errors.Length > 0) Fail(errors.Trim());
        }
        catch (IOException) { }
    }

    private void Fail(string message)
    {
        var error = new IOException(message);
        startup.TrySetException(error);
        foreach (var item in pending.Values) item.TrySetException(error);
        if (!disposing) ServiceFailed?.Invoke(message);
    }

    public ValueTask DisposeAsync()
    {
        lock (disposeLock) return new ValueTask(disposeTask ??= DisposeCoreAsync());
    }

    private async Task DisposeCoreAsync()
    {
        disposing = true;
        lifetimeCancellation.Cancel();
        foreach (var item in pending.Values) item.TrySetException(new IOException("遥控已断开"));
        try
        {
            if (process is { HasExited: false })
            {
                try
                {
                    var reply = await SendCoreAsync("shutdown", null, 3);
                    ShutdownAcknowledged = reply.TryGetProperty("closed", out var closed) && closed.ValueKind == JsonValueKind.True;
                    if (reply.TryGetProperty("last_state", out var state) && state.ValueKind == JsonValueKind.Object) ClosedRecorderState = state.Clone();
                }
                catch { }
                try { process.StandardInput.Close(); } catch { }
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(3));
                try { await process.WaitForExitAsync(timeout.Token); }
                catch (OperationCanceledException) { lifetimeJob?.Dispose(); }
            }
        }
        finally
        {
            // This also handles a shutdown request that raced a pending connect.
            lifetimeJob?.Dispose();
            if (process is not null)
            {
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(2));
                try { await process.WaitForExitAsync(timeout.Token); }
                catch (OperationCanceledException) { if (!process.HasExited) process.Kill(entireProcessTree: true); }
                BackendExited = process.HasExited;
                process.Dispose();
            }
            foreach (var item in pending.Values) item.TrySetException(new IOException("遥控已断开"));
            if (runtimePreparation is not null)
            {
                try { await runtimePreparation; }
                catch (Exception) { }
            }
            embeddedBackend?.Dispose();
        }
    }
}
