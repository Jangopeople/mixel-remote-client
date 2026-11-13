using System.Runtime.InteropServices;

namespace MixelRemote.App.Shell;

/// <summary>The few Win32 calls WinUI does not offer: tray icon, menus, keyboard hook, window capture.</summary>
internal static partial class Native
{
    public const int WM_APP = 0x8000;
    public const int WM_COMMAND = 0x0111;
    public const int WM_LBUTTONUP = 0x0202;
    public const int WM_RBUTTONUP = 0x0205;
    public const int WM_CONTEXTMENU = 0x007B;
    public const int WM_KEYDOWN = 0x0100;
    public const int WM_KEYUP = 0x0101;
    public const int WM_SYSKEYDOWN = 0x0104;
    public const int WM_SYSKEYUP = 0x0105;
    public const int WH_KEYBOARD_LL = 13;
    public const uint LLKHF_EXTENDED = 0x01;
    public const uint LLKHF_INJECTED = 0x10;
    public const uint NIM_ADD = 0, NIM_MODIFY = 1, NIM_DELETE = 2, NIM_SETVERSION = 4;
    public const uint NIF_MESSAGE = 1, NIF_ICON = 2, NIF_TIP = 4, NIF_SHOWTIP = 0x80;
    public const uint MF_STRING = 0, MF_SEPARATOR = 0x800, MF_GRAYED = 1;
    public const uint TPM_RIGHTBUTTON = 2, TPM_RETURNCMD = 0x100;
    public const int SW_SHOW = 5, SW_RESTORE = 9;

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    public struct NOTIFYICONDATA
    {
        public uint cbSize;
        public nint hWnd;
        public uint uID;
        public uint uFlags;
        public uint uCallbackMessage;
        public nint hIcon;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string szTip;
        public uint dwState;
        public uint dwStateMask;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string szInfo;
        public uint uVersion;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string szInfoTitle;
        public uint dwInfoFlags;
        public Guid guidItem;
        public nint hBalloonIcon;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct KBDLLHOOKSTRUCT
    {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public nint dwExtraInfo;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public delegate nint HookProc(int code, nint wParam, nint lParam);
    public delegate nint SubclassProc(nint hWnd, uint msg, nint wParam, nint lParam, nuint id, nuint data);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool Shell_NotifyIcon(uint message, ref NOTIFYICONDATA data);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SetCurrentProcessExplicitAppUserModelID(string id);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern nint LoadImage(nint instance, string name, uint type, int cx, int cy, uint load);

    [DllImport("user32.dll")]
    public static extern nint CreatePopupMenu();

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool AppendMenu(nint menu, uint flags, nuint id, string? text);

    [DllImport("user32.dll")]
    public static extern uint TrackPopupMenuEx(nint menu, uint flags, int x, int y, nint hwnd, nint parameters);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DestroyMenu(nint menu);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetCursorPos(out POINT point);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(nint hwnd);

    [DllImport("user32.dll")]
    public static extern nint GetForegroundWindow();

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool ShowWindow(nint hwnd, int command);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern nint SetWindowsHookEx(int id, HookProc proc, nint module, uint thread);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool UnhookWindowsHookEx(nint hook);

    [DllImport("user32.dll")]
    public static extern nint CallNextHookEx(nint hook, int code, nint wParam, nint lParam);

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern nint GetModuleHandle(string? name);

    [DllImport("comctl32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetWindowSubclass(nint hwnd, SubclassProc proc, nuint id, nuint data);

    [DllImport("comctl32.dll")]
    public static extern nint DefSubclassProc(nint hwnd, uint msg, nint wParam, nint lParam);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool PrintWindow(nint hwnd, nint hdc, uint flags);

    [DllImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetWindowRect(nint hwnd, out RECT rect);

    [DllImport("user32.dll")]
    public static extern nint GetDC(nint hwnd);

    [DllImport("user32.dll")]
    public static extern int ReleaseDC(nint hwnd, nint hdc);

    [DllImport("gdi32.dll")]
    public static extern nint CreateCompatibleDC(nint hdc);

    [DllImport("gdi32.dll")]
    public static extern nint CreateCompatibleBitmap(nint hdc, int width, int height);

    [DllImport("gdi32.dll")]
    public static extern nint SelectObject(nint hdc, nint value);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteObject(nint value);

    [DllImport("gdi32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool DeleteDC(nint hdc);

    [StructLayout(LayoutKind.Sequential)]
    public struct BITMAPINFOHEADER
    {
        public uint biSize;
        public int biWidth;
        public int biHeight;
        public ushort biPlanes;
        public ushort biBitCount;
        public uint biCompression;
        public uint biSizeImage;
        public int biXPelsPerMeter;
        public int biYPelsPerMeter;
        public uint biClrUsed;
        public uint biClrImportant;
    }

    [DllImport("dwmapi.dll")]
    public static extern int DwmSetWindowAttribute(nint hwnd, int attribute, ref int value, int size);

    /// <summary>A dark or light title bar for the window (DWMWA_USE_IMMERSIVE_DARK_MODE; 19 before Windows 10 20H1).</summary>
    public static void DarkTitleBar(nint hwnd, bool dark)
    {
        var value = dark ? 1 : 0;
        if (DwmSetWindowAttribute(hwnd, 20, ref value, sizeof(int)) != 0) DwmSetWindowAttribute(hwnd, 19, ref value, sizeof(int));
    }

    [DllImport("gdi32.dll")]
    public static extern int GetDIBits(nint hdc, nint bitmap, uint start, uint lines, [Out] byte[] bits, ref BITMAPINFOHEADER info, uint usage);
}
