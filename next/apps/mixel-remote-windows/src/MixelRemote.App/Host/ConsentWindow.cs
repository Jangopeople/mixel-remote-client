using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Shell;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Host;

/// <summary>
/// The person at this computer decides about a connection request: which screen, and what the technician
/// may do. Nothing is allowed before Allow. Shown on top of everything, also when Mixel Remote sits in the
/// notification area.
/// </summary>
internal sealed class ConsentWindow
{
    private readonly AppModel app;
    private Window? window;
    private ConsentView? view;
    private bool closing;

    public ConsentWindow(AppModel app) => this.app = app;

    public void Present()
    {
        if (window is null)
        {
            window = new Window { Title = L("Someone wants to connect") };
            window.AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"));
            if (window.AppWindow.Presenter is OverlappedPresenter presenter)
            {
                presenter.IsAlwaysOnTop = true;
                presenter.IsMaximizable = false;
                presenter.IsMinimizable = false;
            }
            if (Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()) window.SystemBackdrop = new MicaBackdrop();
            view = new ConsentView(app);
            window.Content = view;
            Ui.FollowTheme(window);
            window.AppWindow.Resize(new Windows.Graphics.SizeInt32(560, 660));
            var area = DisplayArea.GetFromWindowId(window.AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
            window.AppWindow.Move(new Windows.Graphics.PointInt32(area.X + (area.Width - 560) / 2, area.Y + (area.Height - 660) / 2));
            // Closing the window declines the request.
            window.AppWindow.Closing += (sender, args) =>
            {
                if (closing) return;
                args.Cancel = true;
                if (app.Host.Asking) _ = app.Host.Decide(accept: false);
                window.AppWindow.Hide();
            };
        }
        view?.Rebuild();
        window.AppWindow.Show();
        window.Activate();
        Native.SetForegroundWindow(WinRT.Interop.WindowNative.GetWindowHandle(window));
    }

    public void Dismiss() => window?.AppWindow.Hide();

    public void Close()
    {
        if (window is null) return;
        closing = true;
        window.Close();
    }

    /// <summary>For screenshots: the view inside any window.</summary>
    public static UIElement Standalone(AppModel app) => new ConsentView(app);
}

internal sealed class ConsentView : PageView
{
    public ConsentView(AppModel app) : base(app) => Watch(app.Host);

    protected override UIElement Build()
    {
        var host = App.Host;
        var mark = new Image { Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-mark.png"))), Width = 40, Height = 40 };
        var title = Ui.Row(12, mark, Ui.Column(2,
            new TextBlock { Text = L("Someone wants to connect"), Style = Ui.Style("SubtitleTextBlockStyle") },
            Ui.Text(L("Device %@ requests to connect. Its enrolled identity has been verified with Mixel.", Format.RemoteId(App.Remote.Peer)), secondary: true)));
        ((StackPanel)title.Children[1]).MaxWidth = 440;
        UIElement screen;
        if (host.Sources.Count == 0)
        {
            screen = Ui.Message(L("No screen of this computer can be shared right now."), error: false)!;
        }
        else
        {
            screen = Ui.Column(8,
                Ui.Field(L("Screen to share"), Ui.Row(8, Ui.Choice(host.Sources.Select(source => (source.Id, source.Name)), host.Source, value => { host.Source = value ?? ""; Rebuild(); }),
                    Ui.IconButton("\uE72C", L("Refresh"), () => _ = host.LoadSources()))),
                host.Selected?.FullDisplay == true ? Ui.Check(L("I confirm sharing this whole display"), host.ConfirmDisplay, value => host.ConfirmDisplay = value) : null);
        }
        var grants = Ui.Column(8,
            Grant("\uE962", L("Keyboard & mouse"), host.AllowControl, value => host.AllowControl = value, host.ControlAvailable),
            Grant("\uE77F", L("Clipboard"), host.AllowClipboard, value => host.AllowClipboard = value),
            Grant("\uE8B7", L("Files"), host.AllowFiles, value => host.AllowFiles = value),
            Grant("\uE8BD", L("Chat"), host.AllowChat, value => host.AllowChat = value),
            Grant("\uE7C8", L("Allow recording of this session"), host.AllowRecording, value => host.AllowRecording = value));
        var decline = Ui.Plain(L("Decline"), () => _ = host.Decide(accept: false));
        var allow = Ui.Primary(host.AllowControl && host.ControlAvailable ? L("Allow with control") : L("Allow view only"), () => _ = host.Decide(accept: true), host.CanAccept);
        var buttons = Ui.Row(8, decline, allow);
        buttons.HorizontalAlignment = HorizontalAlignment.Right;
        var stack = Ui.Column(18, title, screen, grants, Ui.Message(host.Error), Ui.Message(App.Error), buttons);
        stack.Padding = new Thickness(28);
        return new ScrollViewer { Content = stack };
    }

    private UIElement Grant(string glyph, string text, bool value, Action<bool> changed, bool enabled = true)
    {
        var box = Ui.Check(text, value, next => { changed(next); Rebuild(); }, enabled);
        return Ui.Row(10, Ui.Icon(glyph, 16, Ui.Secondary), box);
    }
}
