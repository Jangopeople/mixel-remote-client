using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Shell;
using MixelRemote.Models;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Session;


/// <summary>
/// One session in the "Pro Console" look, in its tab or in its own window: the session toolbar (device,
/// monitors, Ctrl+Alt+Del, picture quality, Files, Chat, Draw, Record, Report, more, full screen, own window /
/// back into a tab, End), the remote screen and the tool panel.
/// </summary>
internal sealed class SessionView : Grid
{
    private readonly AppModel app;
    private readonly Window window;
    private readonly uint peer;
    private readonly SessionModel session;
    private readonly bool inWindow;
    private readonly Border recBadge = new() { HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(14), Padding = new Thickness(10, 5, 10, 5), CornerRadius = new CornerRadius(14), Visibility = Visibility.Collapsed, IsHitTestVisible = false };
    private readonly TextBlock recText = new() { FontFamily = new FontFamily("Cascadia Mono, Consolas"), FontWeight = Microsoft.UI.Text.FontWeights.Bold, FontSize = 12, Foreground = new SolidColorBrush(Microsoft.UI.Colors.White) };
    private Microsoft.UI.Dispatching.DispatcherQueueTimer? recTimer;
    private readonly Grid toolbar = new() { Padding = new Thickness(14, 8, 14, 8), ColumnSpacing = 10 };
    private readonly ToolPanel tools;
    private readonly Border notices = new() { HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(12), Padding = new Thickness(10), CornerRadius = new CornerRadius(8) };
    private readonly StackPanel waiting;
    private readonly Button retry;
    private readonly Border drawBar = new() { HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Top, Margin = new Thickness(12), Padding = new Thickness(6), CornerRadius = new CornerRadius(8), BorderThickness = new Thickness(1), Visibility = Visibility.Collapsed };
    private bool pending;
    private bool closed;
    private bool shown = true;
    private bool stale;
    private TextBlock? detailLine;
    private TextBlock? fpsText;

    public SessionView(AppModel app, Window window, uint peer = 0, bool inWindow = false, string tool = "")
    {
        this.app = app;
        this.window = window;
        this.peer = peer;
        this.inWindow = inWindow;
        // The view belongs to one session (by the device's Remote ID) and reads only that one.
        session = app.SessionFor(peer) ?? app.Session;
        Screen = new RemoteScreen(session);
        tools = new ToolPanel(app, Screen, session) { Width = 320, Visibility = Visibility.Collapsed };
        if (tool.Length > 0)
        {
            tools.Tool = tool;
            tools.Visibility = Visibility.Visible;
        }
        RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        Children.Add(toolbar);
        var body = new Grid();
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        body.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var stage = new Grid();
        stage.Children.Add(Screen);
        waiting = Ui.Column(12, new ProgressRing { IsActive = true, Width = 40, Height = 40, Foreground = new SolidColorBrush(Microsoft.UI.Colors.White) }, new TextBlock { Foreground = new SolidColorBrush(Microsoft.UI.Colors.White), Opacity = 0.85 });
        waiting.HorizontalAlignment = HorizontalAlignment.Center;
        waiting.VerticalAlignment = VerticalAlignment.Center;
        // Only the Retry button of a session that could not reconnect takes clicks; the rest of the stack lets them through.
        retry = new Button { Content = L("Retry"), Padding = new Thickness(18, 7, 18, 7), HorizontalAlignment = HorizontalAlignment.Center, Visibility = Visibility.Collapsed, Background = Ui.BrandBrush, Foreground = new SolidColorBrush(Microsoft.UI.Colors.White) };
        AutomationProperties.SetName(retry, L("Retry"));
        ToolTipService.SetToolTip(retry, L("Ask the remote computer again"));
        retry.Click += (_, _) => _ = session.Retry();
        waiting.Children.Add(retry);
        stage.Children.Add(waiting);
        notices.Background = new SolidColorBrush(Windows.UI.Color.FromArgb(0xA6, 0, 0, 0));
        notices.IsHitTestVisible = false;
        stage.Children.Add(notices);
        stage.Children.Add(drawBar);
        recBadge.Background = new SolidColorBrush(Windows.UI.Color.FromArgb(0xC0, 0, 0, 0));
        recBadge.Child = Ui.Row(6, new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 8, Height = 8, Fill = new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x4D, 0x5E)), VerticalAlignment = VerticalAlignment.Center }, recText);
        AutomationProperties.SetName(recBadge, L("Recording video"));
        stage.Children.Add(recBadge);
        body.Children.Add(stage);
        Grid.SetColumn(tools, 1);
        body.Children.Add(tools);
        Grid.SetRow(body, 1);
        Children.Add(body);
        Screen.Attach(WinRT.Interop.WindowNative.GetWindowHandle(window), session.CtrlAltDel, () => toolbar.Focus(FocusState.Keyboard), ToggleFullScreen, () => _ = ConfirmEnd(app.Workspace.Tab(peer)?.Name ?? ""));
        SizeChanged += (_, _) => { if ((ActualWidth < 1240) != compact) Rebuild(); };
        app.PropertyChanged += OnApp;
        session.PropertyChanged += OnSession;
        // A closed window has no AppWindow any more: stop following the models (the screenshot and demo
        // windows close while their session stays; the app's own session window only hides).
        window.Closed += OnWindowClosed;
        Loaded += (_, _) =>
        {
            Translator.Shared.Changed -= OnLanguage;
            Translator.Shared.Changed += OnLanguage;
        };
        Unloaded += (_, _) => Translator.Shared.Changed -= OnLanguage;
        // A print job of the other computer: the print jobs (Clipboard tool) open by themselves.
        session.PrintJobArrived += OnPrintJob;
        ActualThemeChanged += (_, _) => Rebuild();
        Loaded += (_, _) => Render();
    }

    public RemoteScreen Screen { get; }

    private void OnLanguage(object? sender, EventArgs change) => Rebuild();

    private void OnApp(object? sender, System.ComponentModel.PropertyChangedEventArgs change)
    {
        if (change.PropertyName is nameof(AppModel.Remote) or nameof(AppModel.Busy)) Rebuild();
    }

    private void OnSession(object? sender, System.ComponentModel.PropertyChangedEventArgs change)
    {
        if (change.PropertyName == nameof(SessionModel.Fps)) { if (fpsText is not null && !closed) { fpsText.Text = L("%d fps", session.Fps); AutomationProperties.SetItemStatus(fpsText, fpsText.Text); } }
        else Rebuild();
    }

    private void OnPrintJob(object? sender, EventArgs change)
    {
        tools.Tool = "clipboard";
        tools.Visibility = Visibility.Visible;
        Rebuild();
    }

    /// <summary>
    /// A session in a background tab is not drawn again at every status; it catches up when it comes to the front.
    /// </summary>
    public void SetShown(bool visible)
    {
        shown = visible;
        Visibility = visible ? Visibility.Visible : Visibility.Collapsed;
        Screen.SetActive(visible);
        if (visible && stale)
        {
            stale = false;
            Render();
        }
    }

    private void Rebuild()
    {
        if (pending || closed) return;
        if (!shown)
        {
            stale = true;
            return;
        }
        pending = true;
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            // Rebuilding the toolbar replaces its buttons: an open menu (More, quality, draw options) would vanish under the pointer
            // within a second. Wait until it is closed.
            if (FlyoutOpen())
            {
                var wait = DispatcherQueue.CreateTimer();
                wait.Interval = TimeSpan.FromMilliseconds(300);
                wait.IsRepeating = false;
                wait.Tick += (_, _) => { pending = false; Rebuild(); };
                wait.Start();
                return;
            }
            pending = false;
            Render();
        });
    }

    private bool FlyoutOpen() =>
        XamlRoot is { } root && Microsoft.UI.Xaml.Media.VisualTreeHelper.GetOpenPopupsForXamlRoot(root).Any(popup => popup.Child is FlyoutPresenter or MenuFlyoutPresenter);

    public void Render()
    {
        if (closed) return;
        try { RenderNow(); }
        catch (Exception failure) { MixelRemote.App.App.Log($"session window: {failure}"); }
    }

    private void RenderNow()
    {
        Ui.Theme = ActualTheme == ElementTheme.Default ? ElementTheme.Light : ActualTheme;
        toolbar.Background = Ui.Brush("LayerFillColorDefaultBrush");
        BuildToolbar();
        BuildDrawBar();
        waiting.Visibility = session.Frame is null ? Visibility.Visible : Visibility.Collapsed;
        ((TextBlock)waiting.Children[1]).Text = session.WaitingText();
        ((ProgressRing)waiting.Children[0]).IsActive = !session.ReconnectFailed;
        ((ProgressRing)waiting.Children[0]).Visibility = session.ReconnectFailed ? Visibility.Collapsed : Visibility.Visible;
        retry.Visibility = session.ReconnectFailed ? Visibility.Visible : Visibility.Collapsed;
        var lines = new[] { session.ModeNotice(), session.ReconnectingNotice(), session.PasswordWarning(), session.Notice, session.MeetingNotice, session.Error, session.RecordingText }.Where(line => line.Length > 0).ToList();
        notices.Visibility = lines.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        notices.Child = Ui.Column(4, [.. lines.Select(line => (UIElement)new TextBlock { Text = line, Foreground = new SolidColorBrush(line == session.Error ? Windows.UI.Color.FromArgb(255, 0xFF, 0xB4, 0xBD) : Microsoft.UI.Colors.White), TextWrapping = TextWrapping.Wrap, MaxWidth = 620 })]);
        UpdateRecording();
        tools.Rebuild();
    }

    private void OnWindowClosed(object sender, WindowEventArgs args)
    {
        closed = true;
        window.Closed -= OnWindowClosed;
        app.PropertyChanged -= OnApp;
        session.PropertyChanged -= OnSession;
        session.PrintJobArrived -= OnPrintJob;
        Translator.Shared.Changed -= OnLanguage;
        recTimer?.Stop();
        tools.Detach();
    }

    private void UpdateRecording()
    {
        var since = session.RecordingSince;
        var recording = since is not null && session.Remote.Recording?.Running == true;
        var needsTimer = recording || session.Remote.PasswordSession is not null || session.Remote.Reconnecting;
        if (needsTimer)
        {
            if (recTimer is null)
            {
                // One tick a second: the recording time and the support-password warnings (5 and 1 minute before the end).
                recTimer = DispatcherQueue.CreateTimer();
                recTimer.Interval = TimeSpan.FromSeconds(1);
                recTimer.Tick += (_, _) =>
                {
                    ShowRecordingTime();
                    var warning = session.PasswordWarning() + session.ReconnectingNotice();
                    if (warning != lastWarning)
                    {
                        lastWarning = warning;
                        Rebuild();
                    }
                };
            }
            if (!recTimer.IsRunning) recTimer.Start();
        }
        else
        {
            recTimer?.Stop();
        }
        if (!recording)
        {
            recBadge.Visibility = Visibility.Collapsed;
            return;
        }
        recBadge.Visibility = Visibility.Visible;
        recSince = since;
        ShowRecordingTime();
    }

    private DateTimeOffset? recSince;
    private string lastWarning = "";

    private void ShowRecordingTime()
    {
        if (recSince is not { } since) return;
        var seconds = Math.Max(0, (int)(DateTimeOffset.UtcNow - since).TotalSeconds);
        recText.Text = $"REC {seconds / 60:00}:{seconds % 60:00}";
    }

    /// <summary>Stops following the models (the view leaves its window).</summary>
    public void Detach()
    {
        closed = true;
        window.Closed -= OnWindowClosed;
        app.PropertyChanged -= OnApp;
        session.PropertyChanged -= OnSession;
        session.PrintJobArrived -= OnPrintJob;
        Translator.Shared.Changed -= OnLanguage;
        recTimer?.Stop();
        tools.Detach();
        app.Assistant.Screenshot = () => Task.FromResult<string?>(null);
        Screen.Detach();
    }

    /// <summary>For screenshots: the session inside a given window.</summary>
    public static UIElement Standalone(AppModel app, Window host, uint peer, string tool, bool inWindow = false) => new SessionView(app, host, peer, inWindow, tool);

    /// <summary>The drawing tools float over the remote picture while drawing is on, so the toolbar keeps its width.</summary>
    private void BuildDrawBar()
    {
        drawBar.Visibility = session.Drawing ? Visibility.Visible : Visibility.Collapsed;
        if (!session.Drawing)
        {
            drawBar.Child = null;
            return;
        }
        var bar = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        void Tool(string glyph, string name, Action run, bool enabled = true) => bar.Children.Add(Ui.IconButton(glyph, name, run, enabled));
        var flyout = new MenuFlyout();
        foreach (var (key, label) in new[] { ("pen", L("Pen")), ("highlighter", L("Highlighter")), ("arrow", L("Arrow")) })
        {
            var item = new ToggleMenuFlyoutItem { Text = label, IsChecked = session.DrawTool == key };
            item.Click += (sender, args) => { session.DrawTool = key; Rebuild(); };
            flyout.Items.Add(item);
        }
        flyout.Items.Add(new MenuFlyoutSeparator());
        string[] colours = [L("Red"), L("Yellow"), L("Green"), L("Blue"), L("White")];
        for (byte index = 0; index < colours.Length; index++)
        {
            var chosen = index;
            var (r, g, b) = SessionModel.Palette[index];
            var item = new ToggleMenuFlyoutItem { Text = colours[index], IsChecked = session.DrawColor == index, Icon = new FontIcon { Glyph = "\uE91F", Foreground = new SolidColorBrush(Windows.UI.Color.FromArgb(255, r, g, b)) } };
            item.Click += (sender, args) => { session.DrawColor = chosen; Rebuild(); };
            flyout.Items.Add(item);
        }
        flyout.Items.Add(new MenuFlyoutSeparator());
        for (byte width = 1; width <= 4; width++)
        {
            var chosen = width;
            var item = new ToggleMenuFlyoutItem { Text = L("Size %d", width), IsChecked = session.DrawWidth == width };
            item.Click += (sender, args) => { session.DrawWidth = chosen; Rebuild(); };
            flyout.Items.Add(item);
        }
        var options = new DropDownButton { Content = Ui.Icon("\uE790", 16), Flyout = flyout, Padding = new Thickness(9, 7, 9, 7) };
        AutomationProperties.SetName(options, L("Drawing tool, colour and size"));
        ToolTipService.SetToolTip(options, L("Drawing tool, colour and size"));
        bar.Children.Add(options);
        Tool("\uE7A7", L("Undo drawing"), session.UndoDrawing, session.Strokes.Count > 0);
        Tool("\uE74D", L("Clear drawing"), session.ClearDrawing, session.Strokes.Count > 0);
        Tool("\uE711", L("Stop drawing"), () => { session.Drawing = false; Rebuild(); });
        AutomationProperties.SetName(bar, L("Draw on the screen"));
        drawBar.Child = bar;
        drawBar.Background = Ui.Brush("AcrylicInAppFillColorDefaultBrush");
        drawBar.BorderBrush = Ui.Brush("SurfaceStrokeColorDefaultBrush");
    }

    private string Detail()
    {
        var detail = session.Remote.Control ? L("View and control") : session.Remote.ControlPending ? L("Connecting control…") : L("View only");
        if (session.Fps > 0 && session.Frame is { } frame) detail += $" · {frame.Width} × {frame.Height} · {session.Fps} fps";
        return detail;
    }

    private void ToggleFullScreen()
    {
        var full = window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen;
        window.AppWindow.SetPresenter(full ? AppWindowPresenterKind.Overlapped : AppWindowPresenterKind.FullScreen);
        Rebuild();
    }

    /// <summary>A flat toolbar button: icon and text, or the icon alone in a narrow window.</summary>
    private Button Tool(string glyph, string text, Action run, bool enabled = true, bool on = false, bool danger = false)
    {
        UIElement content = compact
            ? Ui.Icon(glyph, 16, danger ? new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x4D, 0x5E)) : null)
            : Ui.Row(7, Ui.Icon(glyph, 15, danger ? new SolidColorBrush(Windows.UI.Color.FromArgb(255, 0xFF, 0x4D, 0x5E)) : null),
                new TextBlock { Text = text, FontSize = 13, VerticalAlignment = VerticalAlignment.Center });
        var button = new Button
        {
            Content = content, IsEnabled = enabled, Padding = new Thickness(compact ? 9 : 10, 6, compact ? 9 : 12, 6), BorderThickness = new Thickness(0),
            Background = on ? Ui.Brush("MixelBrandSoft") : new SolidColorBrush(Microsoft.UI.Colors.Transparent),
        };
        if (on) button.Foreground = Ui.BrandBrush;
        AutomationProperties.SetName(button, text);
        if (on || danger) AutomationProperties.SetItemStatus(button, on ? T.L("on") : "");
        ToolTipService.SetToolTip(button, text);
        button.Click += (_, _) => run();
        return button;
    }

    private bool compact;

    private void BuildToolbar()
    {
        var remote = session.Remote;
        var tab = peer != 0 ? app.Workspace.Tab(peer) : null;
        var name = tab?.Name ?? L("Computer %@", Format.RemoteId(remote.Peer));
        var os = tab?.Os ?? "";
        compact = ActualWidth > 0 && ActualWidth < 1240;
        toolbar.Children.Clear();
        toolbar.ColumnDefinitions.Clear();
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        // Who: the system tile, the name, and the ID with "E2E encrypted · Unattended".
        var facts = new List<string> { Format.RemoteId(remote.Peer), L("E2E encrypted") };
        facts.Add(remote.UnattendedSession || (session.RequestedUnattended && !remote.UnattendedRefused) ? L("Unattended") : L("Attended"));
        if (!remote.Control) facts.Add(remote.ControlPending ? L("Connecting control…") : L("View only"));
        detailLine = Ui.Caption(string.Join(" · ", facts));
        detailLine.FontSize = 12;
        var title = Ui.Column(1, Ui.Text(name, bold: true, wrap: false), Ui.Row(5, Ui.Icon("", 11, Ui.Secondary), detailLine));
        var who = Ui.Row(10, Ui.OsTile(os, null, 34), title);
        AutomationProperties.SetName(who, name + ", " + string.Join(", ", facts));
        toolbar.Children.Add(who);

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2, HorizontalAlignment = HorizontalAlignment.Right };
        var busy = app.Busy;
        if (remote.Control && remote.Displays > 1)
        {
            var displays = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2, Padding = new Thickness(2) };
            for (var index = 0; index < remote.Displays; index++)
            {
                var all = remote.DisplayAll && index == remote.Displays - 1;
                var chosen = (ushort)index;
                var label = all ? L("All") : (index + 1).ToString(System.Globalization.CultureInfo.InvariantCulture);
                var button = Tool(all ? "" : "", all ? L("All screens") : L("Display %d", index + 1), () => session.SelectDisplay(chosen), true, remote.Display == index);
                button.Content = new TextBlock { Text = label, FontSize = 13, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold };
                button.MinWidth = 34;
                displays.Children.Add(button);
            }
            actions.Children.Add(new Border { Child = displays, BorderThickness = new Thickness(1), BorderBrush = Ui.Brush("MixelHairline"), CornerRadius = new CornerRadius(8), Margin = new Thickness(0, 0, 6, 0) });
        }
        if (remote.Offers("sas")) actions.Children.Add(Tool("", L("Ctrl+Alt+Del"), session.CtrlAltDel, !busy));
        actions.Children.Add(QualityButton());
        actions.Children.Add(Ui.Hairline(vertical: true));
        void Panel(string key, string glyph, string label)
        {
            var on = tools.Visibility == Visibility.Visible && tools.Tool == key;
            actions.Children.Add(Tool(glyph, label, () =>
            {
                tools.Visibility = on ? Visibility.Collapsed : Visibility.Visible;
                tools.Tool = key;
                Rebuild();
            }, true, on));
        }
        if (remote.Files) Panel("files", "", L("Files"));
        Panel("chat", "", L("Chat"));
        if (session.CanDraw) actions.Children.Add(Tool("", L("Draw"), () => { session.Drawing = !session.Drawing; Rebuild(); }, true, session.Drawing));
        if (remote.Recording is { } recording)
        {
            actions.Children.Add(Tool(recording.Running ? "" : "", recording.Running ? L("Stop") : L("Record"),
                () => _ = recording.Running ? session.StopRecording() : session.Record(),
                !session.RecordingPending && recording.Phase != "saving" && session.Frame is not null, recording.Running, recording.Running));
        }
        Panel("help", "", L("Report"));
        actions.Children.Add(More());
        actions.Children.Add(Ui.Hairline(vertical: true));
        var full = window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen;
        actions.Children.Add(Tool(full ? "" : "", (full ? L("Exit full screen") : L("Full screen")) + " (Ctrl+Alt+Enter)", ToggleFullScreen));
        if (peer != 0)
        {
            actions.Children.Add(inWindow
                ? Tool("", L("Back into a tab"), () => app.Workspace.Dock(peer))
                : Tool("", L("Window"), () => app.Workspace.PopOut(peer)));
        }
        foreach (var separator in actions.Children.OfType<Border>().Where(border => border.Width == 1)) separator.Margin = new Thickness(6, 4, 6, 4);
        var scroller = new ScrollViewer { Content = actions, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Enabled, VerticalScrollMode = ScrollMode.Disabled };
        Grid.SetColumn(scroller, 1);
        toolbar.Children.Add(scroller);
        var end = new Button
        {
            Content = Ui.Row(6, Ui.Icon("", 13, new SolidColorBrush(Microsoft.UI.Colors.White)), compact ? null : new TextBlock { Text = L("End"), FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, Foreground = new SolidColorBrush(Microsoft.UI.Colors.White) }),
            Background = Ui.BrandBrush, Padding = new Thickness(12, 7, 12, 7), IsEnabled = !session.Stopping,
        };
        AutomationProperties.SetName(end, L("End session"));
        ToolTipService.SetToolTip(end, L("End session") + " (Ctrl+Alt+Q)");
        end.Click += (_, _) => _ = ConfirmEnd(name);
        Grid.SetColumn(end, 2);
        toolbar.Children.Add(end);
    }

    private async Task ConfirmEnd(string name)
    {
        if (await Dialogs.Confirm(window.Content.XamlRoot, L("End the session with %@?", name), L("The connection ends for both sides."), L("End session"))) await session.End();
    }

    /// <summary>Signal bars and "30 fps": a popover with the picture's facts (the picture adapts to the line by itself).</summary>
    private Button QualityButton()
    {
        var bars = session.Fps >= 20 ? 3 : session.Fps >= 8 ? 2 : session.Fps > 0 ? 1 : 0;
        var barsRow = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 2, VerticalAlignment = VerticalAlignment.Center };
        for (var index = 0; index < 3; index++)
        {
            barsRow.Children.Add(new Microsoft.UI.Xaml.Shapes.Rectangle
            {
                Width = 3, Height = 5 + index * 4, VerticalAlignment = VerticalAlignment.Bottom,
                Fill = index < bars ? Ui.Brush("MixelSuccess") : Ui.Brush("TextFillColorTertiaryBrush"),
            });
        }
        var fps = new TextBlock { Text = L("%d fps", session.Fps), FontSize = 13, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center };
        fpsText = fps;
        var modePicker = new RadioButtons { MaxColumns = 3, SelectedIndex = session.QualityMode switch { "speed" => 1, "quality" => 2, _ => 0 } };
        modePicker.Items.Add(L("Auto"));
        modePicker.Items.Add(L("Optimize speed"));
        modePicker.Items.Add(L("Optimize quality"));
        modePicker.SelectionChanged += (_, _) =>
        {
            var chosen = modePicker.SelectedIndex switch { 1 => "speed", 2 => "quality", _ => "auto" };
            if (chosen != session.QualityMode) session.SetQualityMode(chosen);
        };
        var connPath = session.Remote.ConnectionPath is { Length: > 0 } p
            ? $"{(p == "relay" ? L("Relay") : L("Direct"))} · {(session.Remote.ConnectionProtocol ?? "udp").ToUpperInvariant()}"
            : "—";
        var bitrateText = session.Remote.BitrateKbps is { } kbps
            ? (kbps >= 1000 ? $"{kbps / 1000.0:0.0} Mbps" : $"{kbps} kbps")
            : "—";
        var rttText = session.Remote.RttMs is { } rtt ? $"{rtt} ms" : "—";
        var facts = Ui.Column(8,
            Ui.Heading(L("Connection & picture")),
            Ui.Split(Ui.Text(L("Connection"), secondary: true), Ui.Text(connPath)),
            Ui.Split(Ui.Text(L("Round-trip time"), secondary: true), Ui.Text(rttText)),
            Ui.Split(Ui.Text(L("Bitrate"), secondary: true), Ui.Text(bitrateText)),
            Ui.Split(Ui.Text(L("Pictures per second"), secondary: true), Ui.Text(L("%d fps", session.Fps))),
            Ui.Split(Ui.Text(L("Resolution"), secondary: true), Ui.Text(session.Frame is { } frame ? $"{frame.Width} × {frame.Height}" : "—")),
            Ui.Split(Ui.Text(L("Video decoding"), secondary: true), Ui.Text(L("Mixel engine"))),
            Ui.Split(Ui.Text(L("Received"), secondary: true), Ui.Text(Format.Bytes(session.Remote.Received))),
            modePicker,
            Ui.Caption(L("The picture adapts to the line by itself.")));
        facts.Width = 280;
        var button = new Button
        {
            Content = Ui.Row(8, barsRow, fps), Padding = new Thickness(10, 6, 10, 6), BorderThickness = new Thickness(1), BorderBrush = Ui.Brush("MixelHairline"),
            Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), Flyout = new Flyout { Content = facts }, Margin = new Thickness(4, 0, 4, 0),
        };
        AutomationProperties.SetName(button, L("Picture quality"));
        AutomationProperties.SetItemStatus(button, L("%d fps", session.Fps));
        ToolTipService.SetToolTip(button, L("Picture quality"));
        return button;
    }

    /// <summary>"..." : privacy screen, block input, show pointer, sound, lock, restart, clipboard, colleague, sides, renew, fit.</summary>
    private Button More()
    {
        var remote = session.Remote;
        var menu = new MenuFlyout();
        void Item(string text, Action run, bool enabled = true)
        {
            var item = new MenuFlyoutItem { Text = text, IsEnabled = enabled };
            item.Click += (_, _) => run();
            menu.Items.Add(item);
        }
        void Toggle(string action, string text, bool on, Action run)
        {
            if (!remote.Offers(action)) return;
            var item = new ToggleMenuFlyoutItem { Text = text, IsChecked = on, IsEnabled = !app.Busy };
            item.Click += (_, _) => run();
            menu.Items.Add(item);
        }
        Toggle("privacy", L("Privacy screen"), session.Privacy, session.TogglePrivacy);
        Toggle("block", L("Block input"), session.BlockInput, session.ToggleBlockInput);
        Toggle("pointer", L("Show pointer"), session.PointerMode, session.TogglePointer);
        Toggle("sound", L("Sound"), session.Sound, session.ToggleSound);
        Toggle("voice", L("Voice call"), session.Voice, session.ToggleVoice);
        if (remote.Offers("lock")) Item(L("Lock computer"), session.LockNow, !app.Busy);
        Toggle("lockend", L("Lock when done"), session.LockWhenDone, session.ToggleLockWhenDone);
        if (remote.Offers("restart")) Item(session.RestartArmed ? L("Click again to restart") : L("Restart computer"), session.RestartComputer, !app.Busy);
        menu.Items.Add(new MenuFlyoutSeparator());
        Item(L("Clipboard, send text and print jobs"), () =>
        {
            tools.Tool = "clipboard";
            tools.Visibility = Visibility.Visible;
            Rebuild();
        });
        if (remote.Phase == "active" && !remote.Switched) Item(L("Invite a colleague"), () => _ = InviteColleague());
        if (session.CanSwitchSides) Item(L("Switch sides"), () => _ = session.SwitchSides());
        else if (remote.Offers("meet")) Item(L("Show my screen"), () => _ = session.ShowMyScreen());
        Item(L("Renew connection"), () => _ = session.Renew());
        Item(Screen.FitToWindow ? L("Actual size") : L("Fit to window"), () => { Screen.ToggleFit(); Rebuild(); });
        var more = new Button { Content = Ui.Icon("", 16), Flyout = menu, Padding = new Thickness(9, 6, 9, 6), BorderThickness = new Thickness(0), Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent) };
        AutomationProperties.SetName(more, L("More"));
        ToolTipService.SetToolTip(more, L("More"));
        return more;
    }

    private async Task InviteColleague()
    {
        var remote = session.Remote;
        var device = new TextBox { PlaceholderText = "123 456 789", MaxWidth = 220, HorizontalAlignment = HorizontalAlignment.Left };
        var control = false;
        var linkLine = new StackPanel();
        var join = remote.PeerFeatures.Contains("join");
        var content = Ui.Column(12,
            Ui.Text(join
                ? L("Your colleague gets the invitation in Mixel Remote and joins this session. The person at the computer is asked first, unless unattended access is set up.")
                : L("The other computer cannot take a second technician directly. Use a meeting link instead."), secondary: true),
            join ? Ui.Field(L("Or the Remote ID of their computer"), device) : null,
            join ? Ui.Check(L("Allow viewing and control"), false, value => control = value) : null,
            remote.InviteMessage is { } message ? Ui.Message(Translator.Shared.Translate(message), error: false) : null,
            Ui.Link(L("Use a meeting link instead"), async () =>
            {
                if (await session.MeetingLink() is { } room)
                {
                    linkLine.Children.Clear();
                    linkLine.Children.Add(Ui.Field(L("Meeting link"), Ui.Split(Ui.Selectable(room.Invite, mono: true), Ui.IconButton("\uE8C8", L("Copy"), () => app.CopyText(room.Invite)))));
                }
            }),
            linkLine);
        await Dialogs.Show(window.Content.XamlRoot, L("Invite a colleague"), content, join ? L("Send invitation") : null, async () =>
        {
            if (Format.ParseRemoteId(device.Text) is not { } id)
            {
                session.Error = L("Enter a nine-digit connection ID");
                return false;
            }
            await session.Invite(null, id, control);
            return true;
        });
    }
}
