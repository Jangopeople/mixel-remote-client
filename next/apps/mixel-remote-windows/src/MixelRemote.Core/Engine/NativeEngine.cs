using System.Runtime.InteropServices;
using System.Text.Json;

namespace MixelRemote.Engine;

/// <summary>
/// The Rust engine in mixel_core.dll (crate mixel_core_capi), through its plain C interface. Every call
/// blocks inside the engine until it answers, so it runs on a worker thread; awaiting it returns to the
/// caller's thread (the UI thread for the view models).
/// </summary>
public sealed class NativeEngine : IEngine, IDisposable
{
    /// <summary>The interface version this app was written for (mixel_core_capi::ABI_VERSION).</summary>
    public const uint AbiVersion = 1;
    private const string Library = "mixel_core";
    private nint handle;
    private int disposed;

    public NativeEngine(string dataDirectory)
    {
        if (Native.mixel_core_abi_version() != AbiVersion)
        {
            throw new EngineFailure("The app and its engine do not match. Please reinstall Mixel Remote.");
        }
        Directory.CreateDirectory(dataDirectory);
        if (Native.mixel_engine_new(dataDirectory, out handle, out var error) != 0)
        {
            throw new EngineFailure(Take(error) ?? "The engine could not start");
        }
        Info = ReadInfo();
    }

    public EngineInfo Info { get; }

    private EngineInfo ReadInfo()
    {
        if (Native.mixel_engine_info(handle, out var result) != 0)
        {
            throw new EngineFailure(Take(result) ?? "The engine could not start");
        }
        using var document = JsonDocument.Parse(Take(result) ?? "{}");
        var root = document.RootElement;
        return new EngineInfo(
            root.GetProperty("version").GetString() ?? "",
            root.GetProperty("platform").GetString() ?? "windows",
            root.GetProperty("controller_only").GetBoolean());
    }

    public Task<string> CallAsync(string command, string args) => Task.Run(() =>
    {
        var code = Native.mixel_engine_call(Alive(), command, args, out var result);
        var text = Take(result) ?? "";
        return code == 0 ? text : throw new EngineFailure(text);
    });

    public Task<byte[]> NextVideoAsync(string session, bool compressed) => Task.Run(() =>
    {
        var code = Native.mixel_engine_next_video(Alive(), session, compressed ? (byte)1 : (byte)0, out var data, out var length, out var error);
        if (code != 0) throw new EngineFailure(Take(error) ?? "The picture could not be read");
        if (data == 0 || length == 0) return [];
        try
        {
            var bytes = new byte[checked((int)length)];
            Marshal.Copy(data, bytes, 0, bytes.Length);
            return bytes;
        }
        finally
        {
            Native.mixel_bytes_free(data, length);
        }
    });

    public Task<bool> InputAsync(string session, string json) => Task.Run(() =>
    {
        var code = Native.mixel_engine_input(Alive(), session, json, out var posted, out var error);
        return code == 0 ? posted != 0 : throw new EngineFailure(Take(error) ?? "The input could not be sent");
    });

    public Task ShutdownAsync() => Task.Run(() =>
    {
        if (handle == 0) return;
        if (Native.mixel_engine_shutdown(handle, out var error) != 0) throw new EngineFailure(Take(error) ?? "The engine could not stop cleanly");
    });

    private nint Alive() => handle != 0 && Volatile.Read(ref disposed) == 0 ? handle : throw new EngineFailure("The engine is not running");

    private static string? Take(nint text)
    {
        if (text == 0) return null;
        try { return Marshal.PtrToStringUTF8(text); }
        finally { Native.mixel_string_free(text); }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref disposed, 1) != 0) return;
        var released = Interlocked.Exchange(ref handle, 0);
        if (released != 0) Native.mixel_engine_free(released);
    }

    private static class Native
    {
        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern uint mixel_core_abi_version();

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_new([MarshalAs(UnmanagedType.LPUTF8Str)] string dataDir, out nint engine, out nint error);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_info(nint engine, out nint result);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_call(nint engine, [MarshalAs(UnmanagedType.LPUTF8Str)] string command, [MarshalAs(UnmanagedType.LPUTF8Str)] string args, out nint result);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_next_video(nint engine, [MarshalAs(UnmanagedType.LPUTF8Str)] string session, byte compressed, out nint data, out nuint length, out nint error);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_input(nint engine, [MarshalAs(UnmanagedType.LPUTF8Str)] string session, [MarshalAs(UnmanagedType.LPUTF8Str)] string json, out byte posted, out nint error);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern int mixel_engine_shutdown(nint engine, out nint error);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern void mixel_engine_free(nint engine);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern void mixel_string_free(nint text);

        [DllImport(Library, CallingConvention = CallingConvention.Cdecl)]
        internal static extern void mixel_bytes_free(nint data, nuint length);
    }
}
