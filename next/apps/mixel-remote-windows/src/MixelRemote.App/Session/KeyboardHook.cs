using System.Runtime.InteropServices;
using MixelRemote.App.Shell;

namespace MixelRemote.App.Session;

/// <summary>
/// Full keyboard capture while the remote screen has the focus: a low-level keyboard hook receives every
/// key before Windows acts on it (Windows key, Alt+Tab, Alt+Esc, Ctrl+Esc, Print Screen), hands it to the
/// session as a scan code and keeps it from this computer. Ctrl+Alt+Del itself never reaches an app; the
/// toolbar button and Ctrl+Alt+End send it instead.
/// </summary>
internal sealed class KeyboardHook : IDisposable
{
    /// <summary>scan code, extended, down; return true when handled (kept from this computer).</summary>
    private readonly Func<ushort, bool, bool, bool> key;
    private readonly nint window;
    private readonly Native.HookProc procedure;
    private nint hook;

    public KeyboardHook(nint window, Func<ushort, bool, bool, bool> key)
    {
        this.window = window;
        this.key = key;
        procedure = Callback;
    }

    public bool Active => hook != 0;

    public void Start()
    {
        if (hook != 0) return;
        hook = Native.SetWindowsHookEx(Native.WH_KEYBOARD_LL, procedure, Native.GetModuleHandle(null), 0);
        if (hook == 0) App.Log($"keyboard hook: {Marshal.GetLastWin32Error()}");
    }

    public void Stop()
    {
        if (hook == 0) return;
        Native.UnhookWindowsHookEx(hook);
        hook = 0;
    }

    private nint Callback(int code, nint wParam, nint lParam)
    {
        if (code >= 0 && Native.GetForegroundWindow() == window)
        {
            var data = Marshal.PtrToStructure<Native.KBDLLHOOKSTRUCT>(lParam);
            // Keys typed into this computer by software (and our own echoes) are left alone.
            if ((data.flags & Native.LLKHF_INJECTED) == 0)
            {
                var message = (int)wParam;
                var down = message is Native.WM_KEYDOWN or Native.WM_SYSKEYDOWN;
                var extended = (data.flags & Native.LLKHF_EXTENDED) != 0;
                // Left and right Windows keys report scan codes 0x5B/0x5C with the extended flag.
                if (key((ushort)data.scanCode, extended, down)) return 1;
            }
        }
        return Native.CallNextHookEx(hook, code, wParam, lParam);
    }

    public void Dispose() => Stop();
}
