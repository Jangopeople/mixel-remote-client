using System.ComponentModel;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Pages;
using MixelRemote.ViewModels;
using Windows.System;

namespace MixelRemote.App.Shell;

/// <summary>
/// The main window: Mixel's sidebar with the four sections and this computer's connection, and the page
/// beside it. Mica on Windows 11, the theme follows Windows. Closing it keeps Mixel Remote running in the
/// notification area.
/// </summary>
internal sealed class MainWindow : Window
{
    private readonly AppModel app;
    private readonly Grid root = new();
    private readonly StackPanel sidebarItems = new() { Spacing = 4, Margin = new Thickness(10, 0, 10, 0) };
    private readonly StackPanel connection = new() { Spacing = 4, Margin = new Thickness(16) };
    private readonly Grid content = new();
    private readonly Dictionary<AppSection, PageView> pages = [];
    private bool allowClose;

    public MainWindow(AppModel app)
    {
        this.app = app;
        Title = "Mixel Remote";
        Handle = WinRT.Interop.WindowNative.GetWindowHandle(this);
        AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"));
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1180, 780));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = 900;
            presenter.PreferredMinimumHeight = 600;
        }
        if (Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()) SystemBackdrop = new MicaBackdrop();
        else root.Background = Ui.Brush("MixelPage");
        AppWindow.Closing += (sender, args) =>
        {
            if (allowClose) return;
            args.Cancel = true;
            AppWindow.Hide();
        };

        pages[AppSection.Remote] = new RemotePage(app);
        pages[AppSection.Devices] = new DevicesPage(app);
        pages[AppSection.Account] = new AccountPage(app);
        pages[AppSection.Settings] = new SettingsPage(app);
        foreach (var page in pages.Values)
        {
            page.Active = false;
            content.Children.Add(page);
        }

        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(232) });
        root.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        var sidebar = new Grid();
        sidebar.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebar.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        sidebar.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        sidebar.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var brand = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10, Margin = new Thickness(18, 18, 16, 20) };
        brand.Children.Add(new Image { Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-mark.png"))), Width = 28, Height = 28 });
        brand.Children.Add(new TextBlock { Text = "Mixel Remote", FontSize = 17, FontWeight = Microsoft.UI.Text.FontWeights.SemiBold, VerticalAlignment = VerticalAlignment.Center });
        AutomationProperties.SetName(brand, "Mixel Remote");
        sidebar.Children.Add(brand);
        Grid.SetRow(sidebarItems, 1);
        sidebar.Children.Add(sidebarItems);
        var footer = new Border { BorderThickness = new Thickness(0, 1, 0, 0), Child = connection };
        Grid.SetRow(footer, 3);
        sidebar.Children.Add(footer);
        root.Children.Add(sidebar);
        var pageHost = new Border { Child = content, CornerRadius = new CornerRadius(8, 0, 0, 0), BorderThickness = new Thickness(1, 1, 0, 0) };
        Grid.SetColumn(pageHost, 1);
        root.Children.Add(pageHost);
        Content = root;

        // Keyboard: Ctrl+1…4 open the sections.
        var keys = new[] { VirtualKey.Number1, VirtualKey.Number2, VirtualKey.Number3, VirtualKey.Number4 };
        for (var index = 0; index < keys.Length; index++)
        {
            var section = (AppSection)index;
            var accelerator = new KeyboardAccelerator { Key = keys[index], Modifiers = VirtualKeyModifiers.Control };
            accelerator.Invoked += (sender, args) =>
            {
                app.Section = section;
                args.Handled = true;
            };
            root.KeyboardAccelerators.Add(accelerator);
        }
        root.ActualThemeChanged += (_, _) => Refresh();
        Translator.Shared.Changed += (_, _) => Refresh();
        app.PropertyChanged += OnChanged;
        Refresh();
    }

    public nint Handle { get; }
    public FrameworkElement Root => root;

    private void OnChanged(object? sender, PropertyChangedEventArgs change)
    {
        if (change.PropertyName is nameof(AppModel.Section) or nameof(AppModel.Remote) or nameof(AppModel.Account)) Refresh();
    }

    private static string Glyph(AppSection section) => section switch
    {
        AppSection.Remote => "\uE8AB",
        AppSection.Devices => "\uE977",
        AppSection.Account => "\uE77B",
        _ => "\uE713",
    };

    public static string SectionTitle(AppSection section) => section switch
    {
        AppSection.Remote => L("Remote control"),
        AppSection.Devices => L("Devices"),
        AppSection.Account => L("Account"),
        _ => L("Settings"),
    };

    private void Refresh()
    {
        Ui.Theme = root.ActualTheme == ElementTheme.Default ? ElementTheme.Light : root.ActualTheme;
        if (SystemBackdrop is null) root.Background = Ui.Brush("MixelPage");
        ((Border)root.Children[1]).Background = Ui.Brush("LayerFillColorDefaultBrush");
        ((Border)root.Children[1]).BorderBrush = Ui.Brush("CardStrokeColorDefaultBrush");
        ((Border)((Grid)root.Children[0]).Children[2]).BorderBrush = Ui.Brush("DividerStrokeColorDefaultBrush");
        sidebarItems.Children.Clear();
        foreach (var section in Enum.GetValues<AppSection>()) sidebarItems.Children.Add(Item(section));
        foreach (var (section, page) in pages)
        {
            var shown = section == app.Section;
            page.Visibility = shown ? Visibility.Visible : Visibility.Collapsed;
            page.Active = shown;
        }
        connection.Children.Clear();
        var remote = app.Remote;
        connection.Children.Add(Ui.Status(remote.Connected ? Ui.Badge.Online : remote.Phase == "error" ? Ui.Badge.Attention : Ui.Badge.Offline,
            remote.Connected ? L("Online") : remote.Phase == "error" ? L("Connection problem") : L("Offline")));
        if (app.SignedIn && remote.Host is { } host)
        {
            var id = Ui.Selectable(Format.RemoteId(host), 18);
            id.FontWeight = Microsoft.UI.Text.FontWeights.SemiBold;
            AutomationProperties.SetName(id, L("Connection ID") + " " + Format.RemoteId(host));
            connection.Children.Add(id);
        }
        else if (!app.SignedIn)
        {
            connection.Children.Add(Ui.Link(L("Sign in"), () => app.Section = AppSection.Account));
        }
        connection.Children.Add(Ui.Caption("Mixel Remote " + app.Info.Version));
    }

    private Button Item(AppSection section)
    {
        var selected = app.Section == section;
        var row = Ui.Row(12, Ui.Icon(Glyph(section), 16, selected ? Ui.BrandBrush : null), new TextBlock
        {
            Text = SectionTitle(section),
            FontWeight = selected ? Microsoft.UI.Text.FontWeights.SemiBold : Microsoft.UI.Text.FontWeights.Normal,
            Foreground = selected ? Ui.BrandBrush : Ui.Brush("TextFillColorPrimaryBrush"),
        });
        var button = new Button
        {
            Content = row,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(12, 9, 12, 9),
            Background = selected ? Ui.Brush("MixelBrandSoft") : new SolidColorBrush(Colors.Transparent),
            BorderThickness = new Thickness(0),
        };
        AutomationProperties.SetName(button, SectionTitle(section));
        AutomationProperties.SetAutomationId(button, "section-" + section.ToString().ToLowerInvariant());
        if (selected) AutomationProperties.SetItemStatus(button, L("Selected"));
        ToolTipService.SetToolTip(button, $"{SectionTitle(section)} (Ctrl+{(int)section + 1})");
        button.Click += (_, _) => app.Section = section;
        return button;
    }

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
        Refresh();
        pages[app.Section].Render();
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
