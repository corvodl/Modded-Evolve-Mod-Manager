// In-process, Windows x64 NativeAOT Evolve developer bridge.
// This module authenticates and queues calls, but NEVER guesses a game engine
// dispatcher from an RVA. Real effects require a separately verified adapter
// registering a callback and pumping it from the actual game thread.
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

internal sealed class VerifiedHandler
{
    public required string Name;
    public required string Id;
    public required Dictionary<string, string> Schema;
    public required nint Callback;
}

internal sealed class DispatchRequest
{
    public required VerifiedHandler Handler;
    public required string Parameters;
    public TaskCompletionSource<int> Result = new(TaskCreationOptions.RunContinuationsAsynchronously);
}

internal sealed class UiSnapshotRequest
{
    public TaskCompletionSource<string?> Result =
        new(TaskCreationOptions.RunContinuationsAsynchronously);
}

public static class EvolveBridge
{
    private const string Protocol = "evolve-dev-event-bridge-v1";
    private const string Source = "EVENTS_FOR_PARITY_V3";
    private const string Build = "870227EC";
    // Exact user-supplied Evolve Stage 2 executable (PE x64, 3.6.1.2538).
    // Production companions MUST reject other game builds, even when the
    // caller supplies a matching digest for a different Evolve.exe file.
    private const string VerifiedExeSha256 =
        "870227ec5ef4c2f020e6c825d13a137cc03c7a23ea55362782dc29a96127737d";
    private const int MaximumMessage = 65536;
    // A short, bounded read-only UI tree, never raw game memory.
    private const int MaximumUiSnapshot = 12000;
    private const int MaximumUiNodes = 128;
    private static readonly object Sync = new();
    private static readonly Dictionary<string, VerifiedHandler> Handlers =
        new(StringComparer.Ordinal);
    private static readonly ConcurrentQueue<DispatchRequest> Pending = new();
    private static readonly ConcurrentQueue<UiSnapshotRequest> UiPending = new();
    private static nint UiSnapshotProvider;
    private static readonly HashSet<string> PreviousNonces = new(StringComparer.Ordinal);
    private static CancellationTokenSource? Cancellation;
    private static TcpListener? Listener;
    private static Task? Worker;
    private static string? Token;
    private static uint GameThread;
    private static int GamePid;

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    private static extern void OutputDebugStringW(string message);
    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();

    // Native host calls Start outside loader lock, on its verified game thread.
    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_Start",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static int Start(nint digestPointer, int port)
    {
        try
        {
            string? digest = Marshal.PtrToStringUTF8(digestPointer);
            if (!IsHex(digest, 64) || port is < 1024 or > 65535)
                return 0;
#if !EVOLVE_BRIDGE_SYNTHETIC_TEST
            if (!digest.Equals(VerifiedExeSha256, StringComparison.OrdinalIgnoreCase))
                return 0;
#endif
            lock (Sync)
            {
                if (Listener != null) return 0;
                using Process process = Process.GetCurrentProcess();
                string path = process.MainModule?.FileName ?? "";
                if (!Path.GetFileName(path).Equals("Evolve.exe", StringComparison.OrdinalIgnoreCase))
                    return 0;
                FileVersionInfo version = FileVersionInfo.GetVersionInfo(path);
                if (version.FileMajorPart != 3 || version.FileMinorPart != 6 ||
                    version.FileBuildPart != 1 || version.FilePrivatePart != 2538)
                    return 0;
                using FileStream file = File.OpenRead(path);
                string actualDigest = Convert.ToHexString(SHA256.HashData(file));
                if (!actualDigest.Equals(digest, StringComparison.OrdinalIgnoreCase))
                    return 0;

                // No exposed network binding except loopback, no arbitrary command execution.
                CancellationTokenSource cancel = new();
                TcpListener listener = new(IPAddress.Loopback, port);
                listener.Start(4);
                string token = Convert.ToHexString(RandomNumberGenerator.GetBytes(32))
                    .ToLowerInvariant();
                GameThread = GetCurrentThreadId();
                GamePid = process.Id;
                PreviousNonces.Clear();
                while (Pending.TryDequeue(out DispatchRequest? old))
                    old.Result.TrySetResult(0);
                Handlers.Clear();
                UiSnapshotProvider = 0;
                while (UiPending.TryDequeue(out UiSnapshotRequest? item))
                    item.Result.TrySetResult(null);
                Token = token;
                Cancellation = cancel;
                Listener = listener;
                Worker = Task.Run(() => ServeAsync(listener, cancel.Token));
                // Explicit one-session debug channel; no token is persisted on disk.
                OutputDebugStringW("[EvolveEventBridge] PID " + GamePid +
                    " session token: " + token + " (local offline only)\n");
                return 1;
            }
        }
        catch (Exception ex)
        {
            OutputDebugStringW("[EvolveEventBridge] Failed to start: " +
                ex.GetType().Name + "\n");
            return 0;
        }
    }

    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_RegisterVerifiedEvent",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static int RegisterEvent(nint namePointer, nint eventIdPointer,
                                    nint schemaPointer, nint callback)
    {
        try
        {
            if (callback == 0 || GetCurrentThreadId() != GameThread ||
                GameThread == 0) return 0;
            string? name = Marshal.PtrToStringUTF8(namePointer);
            string? eventId = Marshal.PtrToStringUTF8(eventIdPointer);
            string? schemaJson = Marshal.PtrToStringUTF8(schemaPointer);
            if (string.IsNullOrEmpty(name) || name.Length > 120 ||
                !name.All(c => char.IsAsciiLetterLower(c) || char.IsAsciiDigit(c) || c == '_') ||
                string.IsNullOrEmpty(eventId) || !eventId.StartsWith("0x") ||
                !IsHex(eventId.Substring(2), 8) || schemaJson is null ||
                schemaJson.Length > 8192) return 0;
            using JsonDocument schemaDoc = JsonDocument.Parse(schemaJson);
            if (schemaDoc.RootElement.ValueKind != JsonValueKind.Object) return 0;
            Dictionary<string, string> schema = new(StringComparer.Ordinal);
            foreach (JsonProperty field in schemaDoc.RootElement.EnumerateObject())
            {
                string kind = field.Value.GetString() ?? "";
                if (field.Name.Length > 90 || !new[] { "entity", "string", "bool", "int", "float" }
                    .Contains(kind) || !schema.TryAdd(field.Name, kind)) return 0;
            }
            lock (Sync)
            {
                if (Listener == null || Handlers.ContainsKey(name)) return 0;
                Handlers.Add(name, new VerifiedHandler {
                    Name = name, Id = eventId, Schema = schema, Callback = callback
                });
            }
            return 1;
        }
        catch (Exception) { return 0; }
    }

    // Read-only, explicitly verified Scaleform tree provider. This callback
    // is registered only by an existing trusted game-side adapter, never by
    // network input. It returns UTF-8 JSON in a bounded caller-owned buffer.
    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_RegisterUiSnapshot",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static int RegisterUiSnapshot(nint callback)
    {
        if (callback == 0 || GameThread == 0 || GetCurrentThreadId() != GameThread)
            return 0;
        lock (Sync)
        {
            if (Listener == null || UiSnapshotProvider != 0) return 0;
            UiSnapshotProvider = callback;
        }
        return 1;
    }

    // The game's integration calls Pump once per main game tick. The listener
    // NEVER invokes native callbacks on a socket/background thread.
    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_Pump",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static unsafe int Pump()
    {
        if (GameThread == 0 || GetCurrentThreadId() != GameThread) return 0;
        int processed = 0;
        while (processed < 16 && Pending.TryDequeue(out DispatchRequest? item))
        {
            // If a socket request timed out, never activate that stale event
            // on a future frame.
            if (item.Result.Task.IsCompleted) continue;
            int outcome = 0;
            try
            {
                byte[] name = Encoding.UTF8.GetBytes(item.Handler.Name + "\0");
                byte[] data = Encoding.UTF8.GetBytes(item.Parameters + "\0");
                fixed (byte* namePointer = name, dataPointer = data)
                {
                    var fn = (delegate* unmanaged[Cdecl]<byte*, byte*, int>)item.Handler.Callback;
                    outcome = fn(namePointer, dataPointer);
                    if (outcome is not (1 or 2)) outcome = 0;
                }
            }
            catch (Exception) { outcome = 0; }
            item.Result.TrySetResult(outcome);
            processed++;
        }
        // Read-only tree snapshots are requested over authenticated loopback
        // but collected ONLY by a registered native adapter on game thread.
        while (processed < 16 && UiPending.TryDequeue(out UiSnapshotRequest? ui))
        {
            if (ui.Result.Task.IsCompleted) continue;
            string? snapshot = null;
            nint callback;
            lock (Sync) { callback = UiSnapshotProvider; }
            if (callback != 0)
            {
                try
                {
                    byte[] buffer = new byte[MaximumUiSnapshot];
                    fixed (byte* ptr = buffer)
                    {
                        var fn = (delegate* unmanaged[Cdecl]<byte*, int, int>)callback;
                        int size = fn(ptr, buffer.Length);
                        if (size > 0 && size <= buffer.Length)
                        {
                            string json = new UTF8Encoding(false, true).GetString(buffer, 0, size);
                            if (ValidateUiSnapshot(json)) snapshot = json;
                        }
                    }
                }
                catch (Exception) { snapshot = null; }
            }
            ui.Result.TrySetResult(snapshot);
            processed++;
        }
        return processed;
    }

    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_CopyToken",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static int CopyToken(nint destination, int capacity)
    {
        if (GameThread == 0 || GetCurrentThreadId() != GameThread || destination == 0)
            return 0;
        lock (Sync)
        {
            if (Token is null || capacity < 65) return 0;
            byte[] bytes = Encoding.ASCII.GetBytes(Token + "\0");
            Marshal.Copy(bytes, 0, destination, bytes.Length);
            return bytes.Length;
        }
    }

    [UnmanagedCallersOnly(EntryPoint = "EvolveBridge_Stop",
        CallConvs = new[] { typeof(CallConvCdecl) })]
    public static void Stop()
    {
        Task? worker;
        lock (Sync)
        {
            Cancellation?.Cancel();
            Listener?.Stop();
            worker = Worker;
            Worker = null;
            Listener = null;
            Cancellation = null;
            Token = null;
            Handlers.Clear();
            UiSnapshotProvider = 0;
            while (UiPending.TryDequeue(out UiSnapshotRequest? ui))
                ui.Result.TrySetResult(null);
            PreviousNonces.Clear();
            while (Pending.TryDequeue(out DispatchRequest? task))
                task.Result.TrySetResult(0);
            GameThread = 0;
        }
        // A native DLL must not unload while its listener thread is still
        // executing AOT code. Finish pending client reads before returning.
        try { worker?.Wait(TimeSpan.FromSeconds(4)); }
        catch (AggregateException) { /* listener stopped during shutdown */ }
    }

    private static async Task ServeAsync(TcpListener listener, CancellationToken cancellation)
    {
        while (!cancellation.IsCancellationRequested)
        {
            try
            {
                using TcpClient client = await listener.AcceptTcpClientAsync(cancellation);
                client.NoDelay = true;
                client.ReceiveTimeout = 2000;
                client.SendTimeout = 2000;
                await ServeClientAsync(client);
            }
            catch (OperationCanceledException) { break; }
            catch (ObjectDisposedException) { break; }
            catch (SocketException) { if (cancellation.IsCancellationRequested) break; }
            catch (Exception ex)
            {
                OutputDebugStringW("[EvolveEventBridge] Client rejected: " +
                    ex.GetType().Name + "\n");
            }
        }
    }

    private static async Task ServeClientAsync(TcpClient client)
    {
        using NetworkStream stream = client.GetStream();
        string? token;
        int pid;
        lock (Sync) { token = Token; pid = GamePid; }
        if (token == null) return;
        JsonElement hello = ParseAndVerify(ReadLine(stream), token);
        string nonce = GetText(hello, "nonce");
        if (GetText(hello, "protocol") != Protocol ||
            GetText(hello, "op") != "hello" ||
            GetText(hello, "source") != Source ||
            GetText(hello, "build") != Build ||
            GetInt(hello, "pid") != pid || !IsHex(nonce, 32)) return;
        lock (Sync)
        {
            // Stop accepting sessions when the replay cache is full. Never
            // silently clear the cache and permit an old nonce to be reused.
            if (PreviousNonces.Count >= 2048 || !PreviousNonces.Add(nonce)) return;
        }
        string[] available;
        bool hasUi;
        lock (Sync)
        {
            available = Handlers.Keys.OrderBy(x => x, StringComparer.Ordinal).ToArray();
            hasUi = UiSnapshotProvider != 0;
        }
        Send(stream, token, new Dictionary<string, object> {
            ["protocol"] = Protocol, ["op"] = "ready", ["nonce"] = nonce,
            ["pid"] = pid, ["build"] = Build, ["status"] = "ready", ["events"] = available,
            ["ui_snapshot"] = hasUi ? "available" : "unavailable"
        });
        JsonElement request;
        try { request = ParseAndVerify(ReadLine(stream), token); }
        catch (IOException) { return; } // A probe closes immediately after ready.
        string operation = GetText(request, "op");
        if (GetText(request, "protocol") != Protocol ||
            (operation != "activate" && operation != "ui_snapshot") ||
            GetText(request, "nonce") != nonce || GetInt(request, "pid") != pid) return;
        if (operation == "ui_snapshot")
        {
            string id = GetText(request, "request_id");
            if (!IsHex(id, 32)) return;
            string? snapshot = null;
            bool supported;
            lock (Sync) { supported = UiSnapshotProvider != 0; }
            if (supported)
            {
                UiSnapshotRequest item = new();
                UiPending.Enqueue(item);
                Task done = await Task.WhenAny(item.Result.Task, Task.Delay(1400));
                if (done == item.Result.Task) snapshot = item.Result.Task.Result;
                else item.Result.TrySetResult(null);  // No stale callback next frame.
            }
            Dictionary<string, object> reply = new() {
                ["protocol"] = Protocol, ["op"] = "ui_snapshot_result",
                ["nonce"] = nonce, ["request_id"] = id, ["pid"] = pid,
                ["status"] = snapshot != null ? "captured" :
                    supported ? "unavailable" : "unsupported"
            };
            if (snapshot != null) reply["snapshot"] = snapshot;
            Send(stream, token, reply);
            return;
        }
        string eventName = GetText(request, "event");
        string identifier = GetText(request, "event_id");
        string requestId = GetText(request, "request_id");
        if (!IsHex(requestId, 32) || !request.TryGetProperty("parameters", out JsonElement parameters))
            return;
        VerifiedHandler? handler;
        lock (Sync) { Handlers.TryGetValue(eventName, out handler); }
        int outcome = 0;
        if (handler != null && handler.Id == identifier &&
            ValidateParameters(parameters, handler.Schema))
        {
            // Queue exactly one allowlisted handler. The game thread pumps it.
            DispatchRequest item = new() { Handler = handler, Parameters = Canonical(parameters) };
            Pending.Enqueue(item);
            Task completed = await Task.WhenAny(item.Result.Task, Task.Delay(1400));
            if (completed == item.Result.Task)
                outcome = item.Result.Task.Result;
            else
                item.Result.TrySetResult(0); // prevents late-frame execution
        }
        Dictionary<string, object> result = new() {
            ["protocol"] = Protocol, ["op"] = "result", ["nonce"] = nonce,
            ["request_id"] = requestId, ["pid"] = pid, ["event"] = eventName,
            ["status"] = outcome == 2 ? "effect_confirmed" :
                outcome == 1 ? "accepted" : "rejected"
        };
        if (outcome == 0) result["error"] = "No verified game-thread handler confirmed this request.";
        Send(stream, token, result);
    }

    private static bool ValidateUiSnapshot(string data)
    {
        // Only structured UI metadata. The bridge never accepts memory
        // addresses, arbitrary commands, or write operations.
        try
        {
            if (Encoding.UTF8.GetByteCount(data) > MaximumUiSnapshot) return false;
            using JsonDocument doc = JsonDocument.Parse(data);
            JsonElement root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object ||
                GetInt(root, "schema") != 1 ||
                root.TryGetProperty("movie", out JsonElement movie) == false ||
                movie.ValueKind != JsonValueKind.String ||
                movie.GetString() is not { Length: > 0 and <= 256 } ||
                GetInt(root, "frame") is < 0 or > 100000 ||
                !root.TryGetProperty("nodes", out JsonElement nodes) ||
                nodes.ValueKind != JsonValueKind.Array ||
                nodes.GetArrayLength() > MaximumUiNodes) return false;
            HashSet<string> ids = new(StringComparer.Ordinal);
            foreach (JsonElement node in nodes.EnumerateArray())
            {
                if (node.ValueKind != JsonValueKind.Object) return false;
                string id = GetText(node, "id");
                string kind = GetText(node, "kind");
                if (id.Length is < 1 or > 120 || !ids.Add(id) ||
                    kind is not ("movieclip" or "button" or "textfield" or "sprite" or "shape" or "unknown") ||
                    GetText(node, "name").Length > 256 ||
                    GetText(node, "text").Length > 512) return false;
                foreach (string key in new[] { "x", "y", "width", "height" })
                {
                    if (!node.TryGetProperty(key, out JsonElement value) ||
                        !value.TryGetDouble(out double number) ||
                        !double.IsFinite(number) || Math.Abs(number) > 100000)
                        return false;
                }
                if (!node.TryGetProperty("visible", out JsonElement visibility) ||
                    visibility.ValueKind is not (JsonValueKind.True or JsonValueKind.False))
                    return false;
                if (!node.TryGetProperty("parent", out JsonElement parent) ||
                    (parent.ValueKind != JsonValueKind.Null &&
                     (parent.ValueKind != JsonValueKind.String ||
                      parent.GetString()?.Length > 120))) return false;
            }
            return true;
        }
        catch (Exception) { return false; }
    }

    private static bool ValidateParameters(JsonElement param,
                                           Dictionary<string, string> schema)
    {
        if (param.ValueKind != JsonValueKind.Object) return false;
        int count = 0;
        HashSet<string> seen = new(StringComparer.Ordinal);
        foreach (JsonProperty prop in param.EnumerateObject())
        {
            if (!seen.Add(prop.Name) ||
                !schema.TryGetValue(prop.Name, out string? kind)) return false;
            bool valid = kind switch {
                "entity" or "string" => prop.Value.ValueKind == JsonValueKind.String &&
                    prop.Value.GetString() is { Length: > 0 and <= 1024 },
                "bool" => prop.Value.ValueKind is JsonValueKind.True or JsonValueKind.False,
                "int" => prop.Value.ValueKind == JsonValueKind.Number &&
                    prop.Value.TryGetInt64(out _),
                "float" => prop.Value.ValueKind == JsonValueKind.Number &&
                    prop.Value.TryGetDouble(out double val) && double.IsFinite(val),
                _ => false,
            };
            if (!valid) return false;
            count++;
        }
        return count == schema.Count;
    }

    private static bool IsHex(string? value, int count) =>
        value is not null && value.Length == count &&
        value.All(c => (c is >= '0' and <= '9') ||
                       (c is >= 'a' and <= 'f') ||
                       (c is >= 'A' and <= 'F'));

    private static string GetText(JsonElement root, string key) =>
        root.TryGetProperty(key, out JsonElement value) &&
        value.ValueKind == JsonValueKind.String ? value.GetString() ?? "" : "";

    private static int GetInt(JsonElement root, string key) =>
        root.TryGetProperty(key, out JsonElement value) &&
        value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out int number)
            ? number : -1;

    private static string ReadLine(NetworkStream stream)
    {
        // Bounded read; no unbounded StreamReader.ReadLine allocations.
        using MemoryStream buffer = new();
        for (int i = 0; i < MaximumMessage; i++)
        {
            int value = stream.ReadByte();
            if (value < 0) throw new IOException("Bridge disconnected.");
            if (value == '\n') return Encoding.ASCII.GetString(buffer.ToArray());
            if (value < 0x20 || value > 0x7e)
                throw new InvalidDataException("Only canonical ASCII JSON is supported.");
            buffer.WriteByte((byte)value);
        }
        throw new InvalidDataException("Game-bridge message exceeds 64 KiB.");
    }

    private static JsonElement ParseAndVerify(string text, string token)
    {
        using JsonDocument document = JsonDocument.Parse(text);
        JsonElement root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object) throw new InvalidDataException("Expected JSON object.");
        string mac = GetText(root, "mac");
        if (!IsHex(mac, 64)) throw new InvalidDataException("Missing message MAC.");
        string body = Canonical(root, excludeMac: true);
        byte[] computed = HMACSHA256.HashData(Encoding.UTF8.GetBytes(token),
                                             Encoding.ASCII.GetBytes(body));
        if (!CryptographicOperations.FixedTimeEquals(
            computed, Convert.FromHexString(mac)))
            throw new InvalidDataException("Invalid message MAC.");
        return root.Clone();
    }

    private static void Send(NetworkStream stream, string token,
                             Dictionary<string, object> fields)
    {
        // NativeAOT does not have reflection-driven JSON metadata for
        // Dictionary<string, object>. Our response wire schema has only
        // strings, integer PID and string arrays: serialize it explicitly.
        string body = CanonicalFields(fields);
        string mac = Convert.ToHexString(HMACSHA256.HashData(
            Encoding.UTF8.GetBytes(token), Encoding.ASCII.GetBytes(body))).ToLowerInvariant();
        fields["mac"] = mac;
        byte[] payload = Encoding.ASCII.GetBytes(CanonicalFields(fields) + "\n");
        if (payload.Length > MaximumMessage) throw new IOException("Response is too large.");
        stream.Write(payload, 0, payload.Length);
    }

    private static string CanonicalFields(Dictionary<string, object> fields) =>
        "{" + string.Join(",", fields.OrderBy(x => x.Key, StringComparer.Ordinal)
            .Select(x => Quote(x.Key) + ":" + (x.Value switch {
                string value => Quote(value),
                int number => number.ToString(CultureInfo.InvariantCulture),
                string[] strings => "[" + string.Join(",", strings.Select(Quote)) + "]",
                _ => throw new InvalidDataException("Unsupported response JSON type."),
            }))) + "}";

    // Match Python's json.dumps(sort_keys=True, ensure_ascii=True,
    // separators=(',', ':'), allow_nan=False) for the restricted ASCII
    // event protocol. Numeric inputs are emitted canonically by the Python
    // client. Non-ASCII wire content is rejected rather than signing a
    // differently normalized payload.
    private static string Canonical(JsonElement element, bool excludeMac = false)
    {
        return element.ValueKind switch {
            JsonValueKind.Object => "{" + string.Join(",", element.EnumerateObject()
                .Where(p => !excludeMac || p.Name != "mac")
                .OrderBy(p => p.Name, StringComparer.Ordinal)
                .Select(p => Quote(p.Name) + ":" + Canonical(p.Value))) + "}",
            JsonValueKind.Array => "[" + string.Join(",", element.EnumerateArray()
                .Select(x => Canonical(x))) + "]",
            JsonValueKind.String => Quote(element.GetString() ?? ""),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => "null",
            JsonValueKind.Number => element.GetRawText(),
            _ => throw new InvalidDataException("Unsupported JSON value."),
        };
    }

    private static string Quote(string value)
    {
        StringBuilder output = new("\"");
        foreach (char c in value)
        {
            switch (c)
            {
                case '\"': output.Append("\\\""); break;
                case '\\': output.Append("\\\\"); break;
                case '\b': output.Append("\\b"); break;
                case '\f': output.Append("\\f"); break;
                case '\n': output.Append("\\n"); break;
                case '\r': output.Append("\\r"); break;
                case '\t': output.Append("\\t"); break;
                default:
                    if (c < 0x20 || c > 0x7e)
                        output.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else output.Append(c);
                    break;
            }
        }
        return output.Append('\"').ToString();
    }
}
