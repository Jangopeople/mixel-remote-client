using MixelRemote.ViewModels;

namespace MixelRemote.App.Shell;

/// <summary>
/// Mixel Remote in the notification area: the connection and the ID as the tooltip; a click opens the
/// window; the menu has Open, Copy ID, Make available / Go offline, End session and Quit.
/// </summary>
internal sealed class TrayIcon : IDisposable
{
    private const uint Callback = Native.WM_APP + 41;
    private const nuint SubclassId = 4711;
    private enum Item : uint { Open = 1, CopyId, Online, Offline, EndSession, Quit }

    private readonly MainWindow window;
    private readonly AppModel app;
    private readonly Native.SubclassProc procedure;
    private readonly nint icon;
    private bool added;

    public TrayIcon(MainWindow window, AppModel app)
    {
        this.window = window;
        this.app = app;
        procedure = Procedure;
        icon = Native.LoadImage(0, Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"), 1, 16, 16, 0x10);
        Native.SetWindowSubclass(window.Handle, procedure, SubclassId, 0);
        var data = Data();
        added = Native.Shell_NotifyIcon(Native.NIM_ADD, ref data);
        data.uVersion = 4;
        Native.Shell_NotifyIcon(Native.NIM_SETVERSION, ref data);
    }

    private Native.NOTIFYICONDATA Data() => new()
    {
        cbSize = (uint)System.Runtime.InteropServices.Marshal.SizeOf<Native.NOTIFYICONDATA>(),
        hWnd = window.Handle,
        uID = 1,
        uFlags = Native.NIF_MESSAGE | Native.NIF_ICON | Native.NIF_TIP | Native.NIF_SHOWTIP,
        uCallbackMessage = Callback,
        hIcon = icon,
        szTip = Tip(),
        szInfo = "",
        szInfoTitle = "",
    };

    /// <summary>The tooltip text (also used by the screenshot of the menu).</summary>
    public string Tip() => TipFor(app);

    public static string TipFor(AppModel app)
    {
        var remote = app.Remote;
        var text = remote.Connected ? L("Online · ID %@", Format.RemoteId(remote.Host)) : L("Offline");
        if (remote.InSession) text += " · " + app.ConnectionMessage;
        text = "Mixel Remote · " + text;
        return text.Length > 127 ? text[..127] : text;
    }

    public void Update()
    {
        if (!added) return;
        var data = Data();
        Native.Shell_NotifyIcon(Native.NIM_MODIFY, ref data);
    }

    /// <summary>The menu entries for the current state (text, id, enabled); null is a separator.</summary>
    public List<(string Text, uint Id, bool Enabled)?> Entries() => EntriesFor(app);

    public static List<(string Text, uint Id, bool Enabled)?> EntriesFor(AppModel app)
    {
        var remote = app.Remote;
        var entries = new List<(string, uint, bool)?>
        {
            (remote.Connected ? L("Online · ID %@", Format.RemoteId(remote.Host)) : L("Offline"), 0, false),
        };
        if (remote.InSession) entries.Add((L("End session"), (uint)Item.EndSession, true));
        entries.Add(null);
        entries.Add((L("Open Mixel Remote"), (uint)Item.Open, true));
        if (app.SignedIn && remote.Host is not null) entries.Add((L("Copy ID"), (uint)Item.CopyId, true));
        if (app.SignedIn)
        {
            if (remote.Disconnected) entries.Add((L("Make this computer available"), (uint)Item.Online, true));
            else if (!remote.InSession) entries.Add((L("Go offline"), (uint)Item.Offline, true));
        }
        else
        {
            entries.Add((L("Sign in…"), (uint)Item.Open, true));
        }
        entries.Add(null);
        entries.Add((L("Quit Mixel Remote"), (uint)Item.Quit, true));
        return entries;
    }

    private nint Procedure(nint hwnd, uint message, nint wParam, nint lParam, nuint id, nuint data)
    {
        if (message == Callback)
        {
            var mouse = (int)(lParam & 0xFFFF);
            if (mouse == Native.WM_LBUTTONUP) window.Show();
            else if (mouse is Native.WM_RBUTTONUP or Native.WM_CONTEXTMENU) ShowMenu();
            return 0;
        }
        return Native.DefSubclassProc(hwnd, message, wParam, lParam);
    }

    private void ShowMenu()
    {
        var menu = Native.CreatePopupMenu();
        foreach (var entry in Entries())
        {
            if (entry is not { } item) Native.AppendMenu(menu, Native.MF_SEPARATOR, 0, null);
            else Native.AppendMenu(menu, Native.MF_STRING | (item.Enabled ? 0 : Native.MF_GRAYED), item.Id, item.Text);
        }
        Native.GetCursorPos(out var point);
        Native.SetForegroundWindow(window.Handle);
        var chosen = (Item)Native.TrackPopupMenuEx(menu, Native.TPM_RIGHTBUTTON | Native.TPM_RETURNCMD, point.X, point.Y, window.Handle, 0);
        Native.DestroyMenu(menu);
        switch (chosen)
        {
            case Item.Open:
                window.Show();
                if (!app.SignedIn) app.Section = AppSection.Account;
                break;
            case Item.CopyId: _ = app.Host.CopyId(); break;
            case Item.Online: _ = app.GoOnline(); break;
            case Item.Offline: _ = app.GoOffline(); break;
            case Item.EndSession: _ = app.Session.End(); break;
            case Item.Quit: _ = App.Current.Quit(); break;
        }
    }

    public void Dispose()
    {
        if (!added) return;
        added = false;
        var data = Data();
        Native.Shell_NotifyIcon(Native.NIM_DELETE, ref data);
    }
}
