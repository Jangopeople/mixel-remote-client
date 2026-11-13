using System.Runtime.InteropServices.WindowsRuntime;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Host;
using MixelRemote.App.Pages;
using MixelRemote.App.Session;
using MixelRemote.App.Shell;
using MixelRemote.Demo;
using MixelRemote.ViewModels;
using Windows.Graphics.Imaging;

namespace MixelRemote.App.Lab;

/// <summary>
/// --screenshots &lt;folder&gt;: every screen of the app with the demo engine (no account, no network), in light
/// and dark, plus the main screens in German, French and Italian. Each picture is the real window as Windows
/// draws it (PrintWindow, so Mica and the Direct3D session picture are included). Needs a signed-in desktop.
/// </summary>
internal static class Screenshots
{
    /// <param name="system">light or dark: Windows is set to this theme; the screens follow it (Mica and title
    /// bar included) and only that theme is taken. null: both themes, forced on the content.</param>
    public static async Task<int> Run(string folder, string? system = null)
    {
        Directory.CreateDirectory(folder);
        var failures = 0;
        var report = new List<string>();
        var themes = system switch { "light" => new[] { ElementTheme.Light }, "dark" => [ElementTheme.Dark], _ => [ElementTheme.Light, ElementTheme.Dark] };
        foreach (var theme in themes)
        {
            foreach (var screen in DemoFixtures.Screens)
            {
                var name = $"{screen}-{theme.ToString().ToLowerInvariant()}.png";
                try
                {
                    var size = await Shoot(screen, system is null ? theme : ElementTheme.Default, "en", Path.Combine(folder, name));
                    report.Add($"PASS {name} {size}");
                }
                catch (Exception failure)
                {
                    failures++;
                    report.Add($"FAIL {name} {failure.GetType().Name}: {failure.Message}");
                }
            }
        }
        foreach (var language in system is "dark" ? [] : new[] { "de", "fr", "it" })
        {
            foreach (var screen in new[] { "remote", "devices", "account", "settings", "session", "consent" })
            {
                var name = $"{screen}-light-{language}.png";
                try
                {
                    var size = await Shoot(screen, system is null ? ElementTheme.Light : ElementTheme.Default, language, Path.Combine(folder, name));
                    report.Add($"PASS {name} {size}");
                }
                catch (Exception failure)
                {
                    failures++;
                    report.Add($"FAIL {name} {failure.GetType().Name}: {failure.Message}");
                }
            }
        }
        Translator.Shared.Choice = "en";
        report.Add($"RESULT {report.Count(line => line.StartsWith("PASS", StringComparison.Ordinal))}/{report.Count} screens");
        await File.WriteAllLinesAsync(Path.Combine(folder, "screenshots.txt"), report);
        return failures == 0 ? 0 : 1;
    }

    private static async Task<string> Shoot(string screen, ElementTheme theme, string language, string path)
    {
        Translator.Shared.Choice = language;
        var app = await DemoFixtures.Screen(screen);
        Window window;
        Action close;
        switch (screen)
        {
            case "consent":
                window = Plain(560, 660, L("Someone wants to connect"));
                window.Content = Themed(ConsentWindow.Standalone(app), theme);
                close = window.Close;
                break;
            case "session" or "session-chat" or "session-files":
                window = Plain(1360, 860, L("Computer %@", Format.RemoteId(app.Remote.Peer)) + " · Mixel Remote");
                var tool = screen switch { "session-chat" => "chat", "session-files" => "files", _ => "" };
                window.Content = Themed(SessionWindow.Standalone(app, window, tool), theme);
                close = window.Close;
                break;
            case "tray":
                window = Plain(420, 360, "Mixel Remote");
                window.Content = Themed(TrayMenu(app), theme);
                close = window.Close;
                break;
            default:
                var main = new MainWindow(app);
                ((FrameworkElement)main.Content).RequestedTheme = theme;
                window = main;
                close = main.CloseForQuit;
                break;
        }
        Ui.FollowTheme(window);
        window.Activate();
        await Task.Delay(900);
        if (window is MainWindow shown)
        {
            shown.RenderNow();
            await Task.Delay(300);
            await Extra(screen, shown);
        }
        // Let the session picture arrive in the swap chain.
        if (screen.StartsWith("session", StringComparison.Ordinal)) app.Session.ShowSample(VideoFrame.Sample(1280, 800, 9));
        await Task.Delay(900);
        var size = await Capture(WinRT.Interop.WindowNative.GetWindowHandle(window), path);
        app.Session.Clear();
        close();
        await Task.Delay(200);
        return size;
    }

    /// <summary>Opens the dialog or menu a screen shows.</summary>
    private static async Task Extra(string screen, MainWindow window)
    {
        var root = window.Root.XamlRoot;
        switch (screen)
        {
            case "deploy-link":
            {
                var page = (DevicesPage)window.PageOf(AppSection.Devices);
                _ = page.ShowDeployLink(new Models.DeployLinkCreated
                {
                    Token = "mrt_7Hq2Kp9vXw4LmN8s",
                    WindowsCommand = "$t='mrt_7Hq2Kp9vXw4LmN8s'; $f=Join-Path $env:TEMP 'Mixel-Remote-Setup.exe'; Invoke-WebRequest -UseBasicParsing 'https://store-storage.mixel.ch/…/Mixel-Remote-Windows-x64.exe' -OutFile $f; Start-Process $f '/S' -Wait; …",
                    MacosCommand = "curl -fsSL https://remote.mixel.ch/install/macos.sh | sudo sh",
                    LinuxCommand = "curl -fsSL https://remote.mixel.ch/install/linux.sh | sudo sh",
                });
                break;
            }
            case "invite":
                _ = ((AccountPage)window.PageOf(AppSection.Account)).ShowInvite();
                break;
            case "devices-manage":
                if (Find<DropDownButton>(window.Root) is { Flyout: { } flyout } button)
                {
                    flyout.ShouldConstrainToRootBounds = true;
                    flyout.ShowAt(button);
                }
                break;
        }
        _ = root;
        await Task.Delay(600);
    }

    private static T? Find<T>(DependencyObject parent) where T : DependencyObject
    {
        for (var index = 0; index < VisualTreeHelper.GetChildrenCount(parent); index++)
        {
            var child = VisualTreeHelper.GetChild(parent, index);
            if (child is T found) return found;
            if (Find<T>(child) is { } deeper) return deeper;
        }
        return null;
    }

    private static Window Plain(int width, int height, string title)
    {
        var window = new Window { Title = title };
        if (Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()) window.SystemBackdrop = new MicaBackdrop();
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32(width, height));
        return window;
    }

    private static FrameworkElement Themed(UIElement content, ElementTheme theme)
    {
        var host = new Grid { RequestedTheme = theme };
        host.Children.Add(content);
        return host;
    }

    /// <summary>The notification-area menu as it reads (Windows draws the real menu outside the app).</summary>
    private static Grid TrayMenu(AppModel app)
    {
        var entries = TrayIcon.EntriesFor(app);
        var tip = TrayIcon.TipFor(app);
        var list = new StackPanel { Spacing = 2, Padding = new Thickness(6) };
        foreach (var entry in entries)
        {
            if (entry is not { } item)
            {
                list.Children.Add(new MenuFlyoutSeparator());
                continue;
            }
            list.Children.Add(new TextBlock { Text = item.Text, Padding = new Thickness(12, 6, 24, 6), Opacity = item.Enabled ? 1 : 0.55 });
        }
        var grid = new Grid { Padding = new Thickness(20) };
        var stack = new StackPanel { Spacing = 12 };
        stack.Children.Add(new TextBlock { Text = tip, Style = Ui.Style("CaptionTextBlockStyle") });
        stack.Children.Add(new Border { Child = list, CornerRadius = new CornerRadius(8), BorderThickness = new Thickness(1), BorderBrush = new SolidColorBrush(Windows.UI.Color.FromArgb(40, 128, 128, 128)), HorizontalAlignment = HorizontalAlignment.Left });
        grid.Children.Add(stack);
        return grid;
    }

    /// <summary>The window as Windows draws it, saved as PNG; returns "width×height".</summary>
    internal static async Task<string> Capture(nint hwnd, string path)
    {
        Native.GetWindowRect(hwnd, out var rect);
        var (width, height) = (rect.Right - rect.Left, rect.Bottom - rect.Top);
        var screen = Native.GetDC(0);
        var memory = Native.CreateCompatibleDC(screen);
        var bitmap = Native.CreateCompatibleBitmap(screen, width, height);
        var old = Native.SelectObject(memory, bitmap);
        byte[] pixels;
        try
        {
            if (!Native.PrintWindow(hwnd, memory, 2)) throw new InvalidOperationException("PrintWindow failed");
            var info = new Native.BITMAPINFOHEADER { biSize = 40, biWidth = width, biHeight = -height, biPlanes = 1, biBitCount = 32 };
            pixels = new byte[width * height * 4];
            if (Native.GetDIBits(memory, bitmap, 0, (uint)height, pixels, ref info, 0) == 0) throw new InvalidOperationException("GetDIBits failed");
        }
        finally
        {
            Native.SelectObject(memory, old);
            Native.DeleteObject(bitmap);
            Native.DeleteDC(memory);
            Native.ReleaseDC(0, screen);
        }
        for (var index = 3; index < pixels.Length; index += 4) pixels[index] = 255;
        if (pixels.Where((sender, index) => index % 4 != 3).All(value => value == 0)) throw new InvalidOperationException("the window drew nothing");
        using var stream = new Windows.Storage.Streams.InMemoryRandomAccessStream();
        var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
        encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Ignore, (uint)width, (uint)height, 96, 96, pixels);
        await encoder.FlushAsync();
        var bytes = new byte[stream.Size];
        await stream.GetInputStreamAt(0).ReadAsync(bytes.AsBuffer(), (uint)stream.Size, Windows.Storage.Streams.InputStreamOptions.None);
        await File.WriteAllBytesAsync(path, bytes);
        return $"{width}×{height}";
    }
}
