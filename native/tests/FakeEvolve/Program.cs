// TEST FIXTURE ONLY: this is NOT the Evolve game or a real dispatcher.
using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;

internal static unsafe class Program
{
    private static int Called;
    private static int Snapshots;
    private const string MockUi = "{\"schema\":1,\"movie\":\"Libs/UI/main_menu.gfx\",\"frame\":1,\"nodes\":[{\"id\":\"root\",\"parent\":null,\"name\":\"MainMenu\",\"kind\":\"movieclip\",\"text\":\"\",\"x\":0,\"y\":0,\"width\":1920,\"height\":1080,\"visible\":true},{\"id\":\"title\",\"parent\":\"root\",\"name\":\"Featured Title\",\"kind\":\"textfield\",\"text\":\"SYNTHETIC ONLY\",\"x\":100,\"y\":50,\"width\":260,\"height\":30,\"visible\":true}]}";

    [UnmanagedCallersOnly(CallConvs = new[] { typeof(CallConvCdecl) })]
    private static int FakeUiSnapshot(byte* output, int capacity)
    {
        byte[] bytes = System.Text.Encoding.UTF8.GetBytes(MockUi);
        if (capacity < bytes.Length) return 0;
        for (int i = 0; i < bytes.Length; i++) output[i] = bytes[i];
        Interlocked.Increment(ref Snapshots);
        return bytes.Length;
    }

    [UnmanagedCallersOnly(CallConvs = new[] { typeof(CallConvCdecl) })]
    private static int FakeLocalHighlights(byte* name, byte* data)
    {
        string? reference = Marshal.PtrToStringUTF8((nint)name);
        string? arguments = Marshal.PtrToStringUTF8((nint)data);
        if (reference != "local_highlights" || arguments != "{\"active\":true}")
            return 0;
        Interlocked.Increment(ref Called);
        return 2; // TEST-ONLY. Never claim a real engine effect occurred.
    }

    public static int Main(string[] args)
    {
        if (args.Length != 1 || !File.Exists(args[0])) return 2;
        nint module = NativeLibrary.Load(Path.GetFullPath(args[0]));
        var start = (delegate* unmanaged[Cdecl]<nint, int, int>)NativeLibrary.GetExport(module, "EvolveBridge_Start");
        var register = (delegate* unmanaged[Cdecl]<nint, nint, nint, nint, int>)NativeLibrary.GetExport(
            module, "EvolveBridge_RegisterVerifiedEvent");
        var registerUi = (delegate* unmanaged[Cdecl]<nint, int>)NativeLibrary.GetExport(
            module, "EvolveBridge_RegisterUiSnapshot");
        var pump = (delegate* unmanaged[Cdecl]<int>)NativeLibrary.GetExport(module, "EvolveBridge_Pump");
        var tokenCopy = (delegate* unmanaged[Cdecl]<nint, int, int>)NativeLibrary.GetExport(module, "EvolveBridge_CopyToken");
        var stop = (delegate* unmanaged[Cdecl]<void>)NativeLibrary.GetExport(module, "EvolveBridge_Stop");

        string ownExe = Environment.ProcessPath ?? "";
        string digest = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(ownExe)));
        nint digestPtr = Marshal.StringToCoTaskMemUTF8(digest);
        nint name = Marshal.StringToCoTaskMemUTF8("local_highlights");
        nint id = Marshal.StringToCoTaskMemUTF8("0x5850f936");
        nint schema = Marshal.StringToCoTaskMemUTF8("{\"active\":\"bool\"}");
        nint tokenBuffer = Marshal.AllocHGlobal(80);
        try
        {
            if (start(digestPtr, 22345) != 1) return 3;
            nint callback = (nint)(delegate* unmanaged[Cdecl]<byte*, byte*, int>)&FakeLocalHighlights;
            if (register(name, id, schema, callback) != 1) return 4;
            nint uiCallback = (nint)(delegate* unmanaged[Cdecl]<byte*, int, int>)&FakeUiSnapshot;
            if (registerUi(uiCallback) != 1) return 7;
            if (registerUi(uiCallback) != 0) return 8; // duplicate must fail
            if (tokenCopy(tokenBuffer, 80) < 65) return 5;
            string token = Marshal.PtrToStringUTF8(tokenBuffer) ?? "";
            Console.WriteLine("READY " + Environment.ProcessId + " " + token);
            Console.Out.Flush();
            Stopwatch timer = Stopwatch.StartNew();
            while (timer.Elapsed < TimeSpan.FromSeconds(15))
            {
                pump();
                if (Volatile.Read(ref Called) != 0)
                {
                    Console.WriteLine("SIMULATED_CALLBACK " + Called +
                        " UI_SNAPSHOTS " + Volatile.Read(ref Snapshots));
                    Console.Out.Flush();
                    // Leave enough time for the loopback reply to be sent
                    // before this synthetic test process unloads the DLL.
                    Thread.Sleep(600);
                    return 0;
                }
                Thread.Sleep(10);
            }
            return 6;
        }
        finally
        {
            stop();
            Marshal.FreeCoTaskMem(digestPtr);
            Marshal.FreeCoTaskMem(name);
            Marshal.FreeCoTaskMem(id);
            Marshal.FreeCoTaskMem(schema);
            Marshal.FreeHGlobal(tokenBuffer);
            NativeLibrary.Free(module);
        }
    }
}
