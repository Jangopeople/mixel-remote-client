using System.ComponentModel;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Pages;
using MixelRemote.App.Session;
using MixelRemote.ViewModels;
using Windows.System;

namespace MixelRemote.App.Shell;

/// <summary>
/// The main window ("Pro Console"): session tabs in the title bar (Devices, one tab per session, "+"); under
/// the Devices tab Mixel's sidebar (Devices, Sessions, Reports, Settings, views, groups, this computer, the
/// account) and the page beside it; under a session tab the session itself. Connecting never leaves the
/// Devices list; a ready session waits behind an "Open" notice unless Settings say otherwise. Mica on
/// Windows 11, the theme follows Windows (or Settings > Appearance). Closing the window keeps Mixel Remote
/// running in the notification area.
/// </summary>
internal sealed partial class MainWindow : Window
{
    private readonly AppModel app;
    private readonly Grid root = new();
    private readonly Grid titleBar = new() { Height = 44 };
    private readonly StackPanel tabs = new() { Orientation = Orientation.Horizontal, Spacing = 2, VerticalAlignment = VerticalAlignment.Bottom };
    private readonly Border dragArea = new() { Background = new SolidColorBrush(Colors.Transparent), MinWidth = 48 };
    private readonly ColumnDefinition captionInset = new() { Width = new GridLength(140) };
    private readonly Grid body = new();
    private readonly Grid consoleLayer = new();
    private readonly Grid sessionHost = new();
    private readonly StackPanel sidebarItems = new() { Spacing = 2, Margin = new Thickness(10, 0, 10, 0) };
    private readonly StackPanel footer = new() { Spacing = 0 };
    private readonly Border sidebarBox = new() { Width = 248 };
    private readonly Border pageHost = new();
    private readonly Grid content = new();
    private readonly Border hosted = new() { Visibility = Visibility.Collapsed, Padding = new Thickness(20, 9, 20, 9), BorderThickness = new Thickness(0, 0, 0, 1) };
    private readonly StackPanel notices = new() { Spacing = 10, HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(18) };
    private readonly Dictionary<AppSection, PageView> pages = [];
    private readonly Dictionary<uint, SessionView> sessionViews = [];
    private readonly Dictionary<uint, PopOutWindow> popOuts = [];
    private bool allowClose;
    private bool closed;
    private bool pending;

    public MainWindow(AppModel app)
    {
        this.app = app;
        Title = "Mixel Remote";
        Handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"));
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1280, 820));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = 960;
            presenter.PreferredMinimumHeight = 600;
        }
        var mica = Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported();
        if (mica) SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        AppWindow.TitleBar.ButtonBackgroundColor = Colors.Transparent;
        AppWindow.TitleBar.ButtonInactiveBackgroundColor = Colors.Transparent;
        AppWindow.Closing += (sender, args) =>
        {
            if (allowClose) return;
            args.Cancel = true;
            AppWindow.Hide();
            app.AppActive = false;
        };
        AppWindow.Changed += (sender, args) =>
        {
            if (args.DidPresenterChange || args.DidSizeChange) Refresh();
        };

        pages[AppSection.Remote] = new RemotePage(app);
        pages[AppSection.Devices] = new DevicesPage(app);
        pages[AppSection.Sessions] = new SessionsPage(app);
        pages[AppSection.Reports] = new ReportsPage(app);
        pages[AppSection.Account] = new AccountPage(app);
        pages[AppSection.Settings] = new SettingsPage(app);
        foreach (var page in pages.Values)
        {
            page.Active = false;
            content.Children.Add(page);
        }

        // Title bar: tabs on the left, the free part drags the window, the caption buttons keep their room.
        titleBar.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        titleBar.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        titleBar.ColumnDefinitions.Add(captionInset);
        Grid.SetColumn(dragArea, 1);
        titleBar.Children.Add(tabs);
        titleBar.Children.Add(dragArea);

        // Console: sidebar | page.
        consoleLayer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        consoleLayer.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var sidebar = new Grid();
        sidebar.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        sidebar.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebar.Children.Add(new ScrollViewer { Content = sidebarItems, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled, VerticalScrollBarVisibility = ScrollBarVisibility.Auto });
        Grid.SetRow(footer, 1);
        sidebar.Children.Add(footer);
        sidebarBox.Child = sidebar;
        consoleLayer.Children.Add(sidebarBox);
        var pageStack = new Grid();
        pageStack.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        pageStack.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        pageStack.Children.Add(hosted);
        Grid.SetRow(content, 1);
        pageStack.Children.Add(content);
        pageHost.Child = pageStack;
        Grid.SetColumn(pageHost, 1);
        consoleLayer.Children.Add(pageHost);

        body.Children.Add(consoleLayer);
        body.Children.Add(sessionHost);
        body.Children.Add(notices);
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.Children.Add(titleBar);
        Grid.SetRow(body, 1);
        root.Children.Add(body);
        if (!mica) root.Background = Ui.Brush("MixelChrome");
        Content = root;
        SetTitleBar(dragArea);
        Ui.FollowTheme(this);
        ApplyAppearance();

        // Keyboard: Ctrl+1…4 open Devices, Sessions, Reports and Settings; Ctrl+T "+" (connect by ID); Ctrl+W ends the open session tab.
        var sections = new[] { AppSection.Devices, AppSection.Sessions, AppSection.Reports, AppSection.Settings };
        var keys = new[] { VirtualKey.Number1, VirtualKey.Number2, VirtualKey.Number3, VirtualKey.Number4 };
        for (var index = 0; index < keys.Length; index++)
        {
            var section = sections[index];
            var accelerator = new KeyboardAccelerator { Key = keys[index], Modifiers = VirtualKeyModifiers.Control };
            accelerator.Invoked += (sender, args) =>
            {
                ShowSection(section);
                args.Handled = true;
            };
            root.KeyboardAccelerators.Add(accelerator);
        }
        var plus = new KeyboardAccelerator { Key = VirtualKey.T, Modifiers = VirtualKeyModifiers.Control };
        plus.Invoked += (sender, args) => { ConnectById(); args.Handled = true; };
        root.KeyboardAccelerators.Add(plus);

        app.Workspace.OpenWindow = OpenPopOut;
        app.Workspace.FocusWindow = peer => { if (popOuts.TryGetValue(peer, out var window)) window.Show(); };
        app.Workspace.CloseWindow = ClosePopOut;
        root.ActualThemeChanged += (_, _) => Refresh();
        root.SizeChanged += (_, _) => UpdateInset();
        Translator.Shared.Changed += OnLanguage;
        app.PropertyChanged += OnChanged;
        app.Workspace.PropertyChanged += OnChanged;
        app.Settings.PropertyChanged += OnSettings;
        app.Devices.PropertyChanged += OnChanged;
        Activated += (_, args) =>
        {
            // The tab in front is the focused session while this window is the active one.
            if (args.WindowActivationState != WindowActivationState.Deactivated) app.Workspace.MainActivated();
            app.AppActive = args.WindowActivationState != WindowActivationState.Deactivated && AppWindow.IsVisible;
        };
        Closed += (_, _) =>
        {
            closed = true;
            Translator.Shared.Changed -= OnLanguage;
            app.PropertyChanged -= OnChanged;
            app.Workspace.PropertyChanged -= OnChanged;
            app.Settings.PropertyChanged -= OnSettings;
            app.Devices.PropertyChanged -= OnChanged;
            foreach (var view in sessionViews.Values) view.Detach();
            foreach (var window in popOuts.Values) window.Close();
        };
        Refresh();
    }

    public nint Handle { get; }
    public FrameworkElement Root => root;

    private void OnLanguage(object? sender, EventArgs change) => Refresh();

    private void OnSettings(object? sender, PropertyChangedEventArgs change)
    {
        if (change.PropertyName == nameof(SettingsModel.Appearance)) ApplyAppearance();
        if (change.PropertyName is nameof(SettingsModel.UpdatePrompt) or nameof(SettingsModel.Installing)) Refresh();
    }

    private void OnChanged(object? sender, PropertyChangedEventArgs change)
    {
        if (change.PropertyName is nameof(AppModel.Section) or nameof(AppModel.Remote) or nameof(AppModel.Account) or nameof(AppModel.Progress) or nameof(AppModel.Error)
            or nameof(AppModel.Busy) or nameof(WorkspaceModel.Tabs) or nameof(WorkspaceModel.Selected) or nameof(WorkspaceModel.Attempt)
            or nameof(WorkspaceModel.ReadyNotices) or nameof(WorkspaceModel.Windows) or nameof(DevicesModel.Devices) or nameof(DevicesModel.View)) Refresh();
    }

    /// <summary>Settings > Appearance: System, Light or Dark.</summary>
    private void ApplyAppearance()
    {
        root.RequestedTheme = app.Settings.Appearance switch { "light" => ElementTheme.Light, "dark" => ElementTheme.Dark, _ => ElementTheme.Default };
        Refresh();
    }

    private void UpdateInset()
    {
        var scale = root.XamlRoot?.RasterizationScale ?? 1;
        var inset = AppWindow.TitleBar.RightInset / scale;
        if (inset > 0 && Math.Abs(captionInset.Width.Value - inset) > 0.5) captionInset.Width = new GridLength(inset);
    }

    private void ShowSection(AppSection section)
    {
        app.Workspace.Selected = WorkspaceTab.Console;
        app.Section = section;
    }

    /// <summary>"+" in the title bar: the cursor goes into "Connect by ID".</summary>
    private void ConnectById()
    {
        ShowSection(AppSection.Devices);
        ((DevicesPage)pages[AppSection.Devices]).FocusConnectById();
    }

    private bool FullScreen => AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen;

    /// <summary>A coalesced rebuild of the chrome.</summary>
    private void Refresh()
    {
        if (closed || pending) return;
        pending = true;
        DispatcherQueue.TryEnqueue(Microsoft.UI.Dispatching.DispatcherQueuePriority.Low, () =>
        {
            pending = false;
            if (closed) return;
            try { Perf.Time("RefreshNow", RefreshNow); }
            catch (Exception failure) { MixelRemote.App.App.Log($"main window: {failure}"); }
        });
    }

    private void RefreshNow()
    {
        if (closed) return;
        Ui.Theme = root.ActualTheme == ElementTheme.Default ? ElementTheme.Light : root.ActualTheme;
        var dark = Ui.Theme == ElementTheme.Dark;
        AppWindow.TitleBar.ButtonForegroundColor = dark ? Colors.White : Colors.Black;
        AppWindow.TitleBar.ButtonInactiveForegroundColor = dark ? Windows.UI.Color.FromArgb(255, 0x9A, 0x9A, 0xA0) : Windows.UI.Color.FromArgb(255, 0x70, 0x70, 0x78);
        if (SystemBackdrop is null) root.Background = Ui.Brush("MixelChrome");
        sidebarBox.Background = SystemBackdrop is null ? Ui.Brush("MixelChrome") : new SolidColorBrush(Colors.Transparent);
        pageHost.Background = Ui.Brush("MixelPaper");
        pageHost.BorderBrush = Ui.Brush("MixelHairline");
        pageHost.BorderThickness = new Thickness(1, 1, 0, 0);
        pageHost.CornerRadius = new CornerRadius(8, 0, 0, 0);
        var workspace = app.Workspace;
        var session = workspace.Selected.Peer is { } peer && workspace.Tab(peer) is not null && !workspace.IsInWindow(peer) ? peer : (uint?)null;
        if (workspace.Selected.Peer is not null && session is null) workspace.Selected = WorkspaceTab.Console;
        var hideChrome = session is not null && FullScreen;
        titleBar.Visibility = hideChrome ? Visibility.Collapsed : Visibility.Visible;
        // A pop-out window the person closed (or whose session ended) goes away; the session is back in its tab.
        foreach (var gone in popOuts.Keys.Where(key => !workspace.IsInWindow(key)).ToList()) ClosePopOut(gone);
        BuildHostedBanner();
        BuildTabs();
        BuildSidebar();
        BuildNotices();
        foreach (var (section, page) in pages)
        {
            var shown = session is null && section == app.Section;
            page.Visibility = shown ? Visibility.Visible : Visibility.Collapsed;
            page.Active = shown;
        }
        consoleLayer.Visibility = session is null ? Visibility.Visible : Visibility.Collapsed;
        sessionHost.Visibility = session is null ? Visibility.Collapsed : Visibility.Visible;
        ShowSessionView(session);
        UpdateInset();
    }

    /// <summary>A technician is connected to this computer: always visible, one click to the details.</summary>
    private void BuildHostedBanner()
    {
        var remote = app.Remote;
        var show = remote.Phase == "active" && remote.Sharing && app.Section != AppSection.Remote;
        hosted.Visibility = show ? Visibility.Visible : Visibility.Collapsed;
        if (!show) return;
        hosted.Background = Ui.Brush("MixelActiveRow");
        hosted.BorderBrush = Ui.Brush("MixelHairline");
        var message = Ui.Row(10, Ui.Icon("\uE95A", 16, Ui.BrandBrush), Ui.Text(L("A technician is connected to this computer."), bold: true, wrap: false));
        hosted.Child = Ui.Split(message, Ui.Row(8, Ui.Plain(L("Show"), () => ShowSection(AppSection.Remote)), Ui.Primary(L("End session"), () => _ = app.Session.End())));
        AutomationProperties.SetName(hosted, L("A technician is connected to this computer."));
        AutomationProperties.SetLiveSetting(hosted, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
    }

    /// <summary>The session of the open tab; sessions that have no tab any more lose their view.</summary>
    private void ShowSessionView(uint? shown)
    {
        var live = workspaceTabs();
        foreach (var peer in sessionViews.Keys.ToList())
        {
            if (live.Contains(peer) && !app.Workspace.IsInWindow(peer)) continue;
            DropSessionView(peer);
        }
        if (shown is { } open && !sessionViews.ContainsKey(open))
        {
            Perf.LabMark("session view creating");
            var view = new SessionView(app, this, open);
            Perf.LabMark("session view created");
            sessionViews[open] = view;
            sessionHost.Children.Add(view);
            view.Screen.FocusScreen();
        }
        foreach (var (peer, view) in sessionViews)
        {
            var visible = peer == shown;
            view.SetShown(visible);
            if (!visible) view.Screen.ReleaseKeyboard();
        }
    }

    private HashSet<uint> workspaceTabs() => [.. app.Workspace.Tabs.Select(tab => tab.Peer)];

    private void DropSessionView(uint peer)
    {
        if (!sessionViews.Remove(peer, out var view)) return;
        view.Detach();
        sessionHost.Children.Remove(view);
        if (FullScreen) AppWindow.SetPresenter(AppWindowPresenterKind.Overlapped);
    }

    private void OpenPopOut(uint peer)
    {
        DropSessionView(peer);
        if (!popOuts.TryGetValue(peer, out var window))
        {
            window = new PopOutWindow(app, peer);
            popOuts[peer] = window;
        }
        window.Show();
        Refresh();
    }

    private void ClosePopOut(uint peer)
    {
        if (popOuts.Remove(peer, out var window)) window.Close();
        Refresh();
    }

    /// <summary>The window to put a file dialog over: the open pop-out window, or this one.</summary>
    public nint DialogOwner => popOuts.Values.FirstOrDefault()?.Handle ?? Handle;

    public void Show()
    {
        AppWindow.Show();
        if (AppWindow.Presenter is OverlappedPresenter { State: OverlappedPresenterState.Minimized } presenter) presenter.Restore();
        Activate();
        Native.SetForegroundWindow(Handle);
    }

    /// <summary>Renders the page of a section now (screenshots).</summary>
    public void RenderNow()
    {
        RefreshNow();
        if (app.Workspace.Selected.IsConsole) pages[app.Section].Render();
    }

    public PageView PageOf(AppSection section) => pages[section];

    public void CloseForQuit()
    {
        allowClose = true;
        Close();
    }

    /// <summary>When the engine cannot start: one window that says so.</summary>
    public static void ShowFatal(string message)
    {
        var window = new Window { Title = "Mixel Remote" };
        var text = Ui.Column(16, Ui.PageTitle("Mixel Remote"), Ui.Text(message), Ui.Plain(L("Close"), () => Application.Current.Exit()));
        text.Padding = new Thickness(32);
        window.Content = text;
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32(560, 300));
        window.Activate();
    }
}
