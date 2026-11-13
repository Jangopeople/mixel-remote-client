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
/// The session on the technician's side, in its own window: the toolbar (what the other computer offers is
/// shown, nothing else), the remote screen, and the tools (chat, files, clipboard, help) beside it.
/// </summary>
internal sealed class SessionWindow
{
    private readonly AppModel app;
    private Window? window;
    private SessionView? view;
    private bool closing;

    public SessionWindow(AppModel app) => this.app = app;

    public bool Visible => window?.AppWindow.IsVisible == true;
    public nint Handle => window is null ? 0 : WinRT.Interop.WindowNative.GetWindowHandle(window);

    public void Present()
    {
        if (window is null)
        {
            window = new Window { Title = "Mixel Remote" };
            window.AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"));
            window.AppWindow.Resize(new Windows.Graphics.SizeInt32(1360, 880));
            if (Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()) window.SystemBackdrop = new MicaBackdrop();
            view = new SessionView(app, window);
            window.Content = view;
            Ui.FollowTheme(window);
            window.Activated += (sender, args) =>
            {
                if (args.WindowActivationState == WindowActivationState.Deactivated) view.Screen.ReleaseKeyboard();
            };
            // Closing the window ends the session (asked first while it is running).
            window.AppWindow.Closing += async (sender, args) =>
            {
                if (closing) return;
                args.Cancel = true;
                if (!app.Remote.InSession)
                {
                    window.AppWindow.Hide();
                    return;
                }
                if (await Dialogs.Confirm(window.Content.XamlRoot, L("End session?"), L("The other computer is disconnected."), L("End session"))) await app.Session.End();
            };
        }
        window.Title = L("Computer %@", Format.RemoteId(app.Remote.Peer)) + " · Mixel Remote";
        if (!window.AppWindow.IsVisible)
        {
            window.AppWindow.Show();
            window.Activate();
            view?.Screen.FocusScreen();
        }
    }

    public void Dismiss()
    {
        if (window is null || !window.AppWindow.IsVisible) return;
        view?.Screen.ReleaseKeyboard();
        if (window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen) window.AppWindow.SetPresenter(AppWindowPresenterKind.Overlapped);
        window.AppWindow.Hide();
    }

    public void Close()
    {
        if (window is null) return;
        closing = true;
        window.Close();
    }

    /// <summary>For screenshots: the session inside a given window.</summary>
    public static UIElement Standalone(AppModel app, Window host, string tool) => new SessionView(app, host, tool);
}

internal sealed class SessionView : Grid
{
    private readonly AppModel app;
    private readonly Window window;
    private readonly Grid toolbar = new() { Padding = new Thickness(14, 8, 14, 8), ColumnSpacing = 10 };
    private readonly ToolPanel tools;
    private readonly Border notices = new() { HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(12), Padding = new Thickness(10), CornerRadius = new CornerRadius(8) };
    private readonly StackPanel waiting;
    private bool pending;
    private bool closed;
    private TextBlock? detailLine;

    public SessionView(AppModel app, Window window, string tool = "")
    {
        this.app = app;
        this.window = window;
        Screen = new RemoteScreen(app.Session);
        tools = new ToolPanel(app, Screen) { Width = 320, Visibility = Visibility.Collapsed };
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
        waiting.IsHitTestVisible = false;
        stage.Children.Add(waiting);
        notices.Background = new SolidColorBrush(Windows.UI.Color.FromArgb(0xA6, 0, 0, 0));
        notices.IsHitTestVisible = false;
        stage.Children.Add(notices);
        body.Children.Add(stage);
        Grid.SetColumn(tools, 1);
        body.Children.Add(tools);
        Grid.SetRow(body, 1);
        Children.Add(body);
        Screen.Attach(WinRT.Interop.WindowNative.GetWindowHandle(window), app.Session.CtrlAltDel, () => toolbar.Focus(FocusState.Keyboard), ToggleFullScreen, () => _ = app.Session.End());
        app.PropertyChanged += OnApp;
        app.Session.PropertyChanged += OnSession;
        // A closed window has no AppWindow any more: stop following the models (the screenshot and demo
        // windows close while their session stays; the app's own session window only hides).
        window.Closed += (_, _) =>
        {
            closed = true;
            app.PropertyChanged -= OnApp;
            app.Session.PropertyChanged -= OnSession;
            app.Session.PrintJobArrived -= OnPrintJob;
            Translator.Shared.Changed -= OnLanguage;
        };
        Loaded += (_, _) =>
        {
            Translator.Shared.Changed -= OnLanguage;
            Translator.Shared.Changed += OnLanguage;
        };
        Unloaded += (_, _) => Translator.Shared.Changed -= OnLanguage;
        // A print job of the other computer: the print jobs (Clipboard tool) open by themselves.
        app.Session.PrintJobArrived += OnPrintJob;
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
        if (change.PropertyName == nameof(SessionModel.Fps)) { if (detailLine is not null && !closed) detailLine.Text = Detail(); }
        else Rebuild();
    }

    private void OnPrintJob(object? sender, EventArgs change)
    {
        tools.Tool = "clipboard";
        tools.Visibility = Visibility.Visible;
        Rebuild();
    }

    private void Rebuild()
    {
        if (pending || closed) return;
        pending = true;
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            pending = false;
            Render();
        });
    }

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
        var session = app.Session;
        waiting.Visibility = session.Frame is null ? Visibility.Visible : Visibility.Collapsed;
        ((TextBlock)waiting.Children[1]).Text = app.Remote.Phase == "outgoing" ? L("Waiting for the other person to accept your request.") : L("Connecting securely…");
        var lines = new[] { session.Notice, session.MeetingNotice, session.Error, session.RecordingText }.Where(line => line.Length > 0).ToList();
        notices.Visibility = lines.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        notices.Child = Ui.Column(4, [.. lines.Select(line => (UIElement)new TextBlock { Text = line, Foreground = new SolidColorBrush(line == session.Error ? Windows.UI.Color.FromArgb(255, 0xFF, 0xB4, 0xBD) : Microsoft.UI.Colors.White), TextWrapping = TextWrapping.Wrap, MaxWidth = 620 })]);
        tools.Rebuild();
    }

    private string Detail()
    {
        var detail = app.Remote.Control ? L("View and control") : L("View only");
        if (app.Session.Fps > 0 && app.Session.Frame is { } frame) detail += $" · {frame.Width} × {frame.Height} · {app.Session.Fps} fps";
        return detail;
    }

    private void ToggleFullScreen()
    {
        var full = window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen;
        window.AppWindow.SetPresenter(full ? AppWindowPresenterKind.Overlapped : AppWindowPresenterKind.FullScreen);
        Rebuild();
    }

    private void BuildToolbar()
    {
        var remote = app.Remote;
        var session = app.Session;
        toolbar.Children.Clear();
        toolbar.ColumnDefinitions.Clear();
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        toolbar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var detail = Detail();
        detailLine = Ui.Caption(detail);
        var title = Ui.Column(1, Ui.Text(L("Computer %@", Format.RemoteId(remote.Peer)), bold: true, wrap: false),
            Ui.Row(6, Ui.Icon(remote.Control ? "\uE962" : "\uE890", 12, Ui.Secondary), detailLine));
        AutomationProperties.SetName(title, L("Computer %@", Format.RemoteId(remote.Peer)) + ", " + detail);
        toolbar.Children.Add(title);

        var actions = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6, HorizontalAlignment = HorizontalAlignment.Right };
        if (remote.Control && remote.Displays > 1)
        {
            var displays = Enumerable.Range(0, remote.Displays).Select(index =>
                (index.ToString(System.Globalization.CultureInfo.InvariantCulture), remote.DisplayAll && index == remote.Displays - 1 ? L("All screens") : L("Display %d", index + 1)));
            var choice = Ui.Choice(displays, remote.Display.ToString(System.Globalization.CultureInfo.InvariantCulture), value => { if (ushort.TryParse(value, out var index)) session.SelectDisplay(index); }, 130);
            AutomationProperties.SetName(choice, L("Display"));
            actions.Children.Add(choice);
        }
        var busy = app.Busy;
        void Toggle(string action, string glyph, string name, bool on, Action toggle)
        {
            if (!remote.Offers(action)) return;
            var state = on ? L("on") : L("off");
            var button = Ui.IconButton(glyph, $"{name}: {state}", toggle, !busy, on);
            AutomationProperties.SetName(button, name);
            AutomationProperties.SetItemStatus(button, state);
            actions.Children.Add(button);
        }
        void Tool(string glyph, string name, Action run, bool enabled = true) => actions.Children.Add(Ui.IconButton(glyph, name, run, enabled && !busy));
        Toggle("privacy", "\uE7B3", L("Privacy screen"), session.Privacy, session.TogglePrivacy);
        Toggle("block", "\uE8F8", L("Block input"), session.BlockInput, session.ToggleBlockInput);
        Toggle("pointer", "\uE7C9", L("Show pointer"), session.PointerMode, session.TogglePointer);
        if (remote.Offers("lock")) Tool("\uE72E", L("Lock computer"), session.LockNow);
        Toggle("lockend", "\uE823", L("Lock when done"), session.LockWhenDone, session.ToggleLockWhenDone);
        if (remote.Offers("sas")) Tool("\uE92E", L("Ctrl+Alt+Del") + " (Ctrl+Alt+End)", session.CtrlAltDel);
        if (remote.Offers("restart")) Tool("\uE777", session.RestartArmed ? L("Click again to restart") : L("Restart computer"), session.RestartComputer);
        Toggle("sound", session.Sound ? "\uE767" : "\uE74F", L("Sound"), session.Sound, session.ToggleSound);
        if (remote.Recording is { } recording)
        {
            Tool(recording.Running ? "\uE71A" : "\uE7C8", recording.Running ? L("Stop recording") : L("Record"),
                () => _ = recording.Running ? session.StopRecording() : session.Record(), !session.RecordingPending && recording.Phase != "saving" && session.Frame is not null);
        }
        if (remote.Phase == "active" && !remote.Switched) Tool("\uE8FA", L("Invite a colleague"), () => _ = InviteColleague());
        if (session.CanSwitchSides) Tool("\uE8AB", L("Switch sides"), () => _ = session.SwitchSides());
        else if (remote.Offers("meet")) Tool("\uE7F4", L("Show my screen"), () => _ = session.ShowMyScreen());
        Tool("\uE895", L("Renew connection"), () => _ = session.Renew());
        if (session.CanDraw)
        {
            actions.Children.Add(new AppBarSeparator());
            var pen = Ui.IconButton("\uE70F", L("Draw on the screen") + ": " + (session.Drawing ? L("on") : L("off")), () => { session.Drawing = !session.Drawing; Rebuild(); }, true, session.Drawing);
            actions.Children.Add(pen);
            if (session.Drawing)
            {
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
                actions.Children.Add(options);
                Tool("\uE7A7", L("Undo drawing"), session.UndoDrawing, session.Strokes.Count > 0);
                Tool("\uE74D", L("Clear drawing"), session.ClearDrawing, session.Strokes.Count > 0);
            }
        }
        actions.Children.Add(new AppBarSeparator());
        void Panel(string key, string glyph, string name)
        {
            var on = tools.Visibility == Visibility.Visible && tools.Tool == key;
            actions.Children.Add(Ui.IconButton(glyph, name, () =>
            {
                tools.Visibility = on ? Visibility.Collapsed : Visibility.Visible;
                tools.Tool = key;
                Rebuild();
            }, true, on));
        }
        Panel("chat", "\uE8BD", L("Chat"));
        Panel("files", "\uE8B7", L("Files"));
        Panel("clipboard", "\uE77F", L("Clipboard"));
        Panel("help", "\uE897", L("AI help"));
        actions.Children.Add(Ui.IconButton(Screen.FitToWindow ? "\uE9A6" : "\uE740", Screen.FitToWindow ? L("Actual size") : L("Fit to window"), () => { Screen.ToggleFit(); Rebuild(); }));
        var full = window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen;
        actions.Children.Add(Ui.IconButton(full ? "\uE73F" : "\uE740", (full ? L("Exit full screen") : L("Full screen")) + " (Ctrl+Alt+Enter)", ToggleFullScreen));
        var scroller = new ScrollViewer { Content = actions, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto, VerticalScrollBarVisibility = ScrollBarVisibility.Disabled, HorizontalScrollMode = ScrollMode.Enabled, VerticalScrollMode = ScrollMode.Disabled };
        Grid.SetColumn(scroller, 1);
        toolbar.Children.Add(scroller);
        var end = Ui.Primary(L("End session"), () => _ = session.End(), !session.Stopping, "\uE711");
        ToolTipService.SetToolTip(end, L("End session") + " (Ctrl+Alt+Q)");
        Grid.SetColumn(end, 2);
        toolbar.Children.Add(end);
    }

    private async Task InviteColleague()
    {
        var remote = app.Remote;
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
                if (await app.Session.MeetingLink() is { } room)
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
                app.Session.Error = L("Enter a nine-digit connection ID");
                return false;
            }
            await app.Session.Invite(null, id, control);
            return true;
        });
    }
}
