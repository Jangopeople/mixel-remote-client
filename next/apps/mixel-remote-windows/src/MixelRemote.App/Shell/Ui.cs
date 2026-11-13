using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI.Text;

namespace MixelRemote.App.Shell;

/// <summary>
/// Mixel's building blocks for the screens: cards on the window background, a title with an icon, the
/// red main button, message lines, status dots. Colours come from the Fluent theme resources of the
/// window's current theme (light or dark, following Windows), so text is never white on white.
/// </summary>
internal static class Ui
{
    /// <summary>The theme the screen being built uses (set by the window before it builds).</summary>
    public static ElementTheme Theme { get; set; } = ElementTheme.Light;

    public static Brush Brush(string key)
    {
        var resources = Application.Current.Resources;
        var names = Theme == ElementTheme.Dark ? new[] { "Dark", "Default" } : ["Light"];
        foreach (var dictionary in Dictionaries(resources))
        {
            foreach (var name in names)
            {
                if (dictionary.ThemeDictionaries.TryGetValue(name, out var themed) && themed is ResourceDictionary theme && theme.TryGetValue(key, out var value) && value is Brush brush)
                {
                    return brush;
                }
            }
        }
        return resources.TryGetValue(key, out var plain) && plain is Brush found ? found : new SolidColorBrush(Microsoft.UI.Colors.Gray);
    }

    private static IEnumerable<ResourceDictionary> Dictionaries(ResourceDictionary root)
    {
        yield return root;
        foreach (var merged in root.MergedDictionaries)
        {
            foreach (var inner in Dictionaries(merged)) yield return inner;
        }
    }

    /// <summary>The window's title bar follows the content's theme (light or dark like Windows).</summary>
    public static void FollowTheme(Window window)
    {
        var hwnd = WinRT.Interop.WindowNative.GetWindowHandle(window);
        void Apply()
        {
            if (window.Content is FrameworkElement root) Native.DarkTitleBar(hwnd, root.ActualTheme == ElementTheme.Dark);
        }
        if (window.Content is FrameworkElement content)
        {
            content.ActualThemeChanged += (sender, args) => Apply();
            content.Loaded += (sender, args) => Apply();
        }
        Apply();
    }

    public static Style? Style(string key) => Application.Current.Resources.TryGetValue(key, out var value) ? value as Style : null;

    public static Brush Secondary => Brush("TextFillColorSecondaryBrush");
    public static Brush BrandBrush => Brush("MixelBrand");

    public static TextBlock Text(string text, bool secondary = false, double size = 0, bool bold = false, bool wrap = true)
    {
        var block = new TextBlock { Text = text, TextWrapping = wrap ? TextWrapping.Wrap : TextWrapping.NoWrap, IsTextSelectionEnabled = false };
        if (secondary) block.Foreground = Secondary;
        if (size > 0) block.FontSize = size;
        if (bold) block.FontWeight = FontWeights.SemiBold;
        if (!wrap) block.TextTrimming = TextTrimming.CharacterEllipsis;
        return block;
    }

    public static TextBlock Selectable(string text, double size = 0, bool mono = false)
    {
        var block = Text(text, size: size);
        block.IsTextSelectionEnabled = true;
        if (mono) block.FontFamily = new FontFamily("Cascadia Mono, Consolas");
        return block;
    }

    public static TextBlock PageTitle(string text) => new() { Text = text, Style = Style("TitleTextBlockStyle"), TextWrapping = TextWrapping.Wrap };
    public static TextBlock Heading(string text) => new() { Text = text, Style = Style("BodyStrongTextBlockStyle"), TextWrapping = TextWrapping.Wrap };
    public static TextBlock Caption(string text) => new() { Text = text, Style = Style("CaptionTextBlockStyle"), Foreground = Secondary, TextWrapping = TextWrapping.Wrap };

    public static FontIcon Icon(string glyph, double size = 16, Brush? brush = null)
    {
        var icon = new FontIcon { Glyph = glyph, FontSize = size };
        if (brush is not null) icon.Foreground = brush;
        AutomationProperties.SetAccessibilityView(icon, Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
        return icon;
    }

    /// <summary>A card title: icon in Mixel red and a heading (a header for Narrator).</summary>
    public static StackPanel CardTitle(string text, string glyph)
    {
        var heading = Heading(text);
        heading.FontSize = 16;
        AutomationProperties.SetHeadingLevel(heading, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level2);
        var row = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 10 };
        row.Children.Add(Icon(glyph, 18, BrandBrush));
        row.Children.Add(heading);
        return row;
    }

    /// <summary>A rounded panel on the window background (Mixel's card).</summary>
    public static Border Card(params UIElement?[] children)
    {
        var stack = new StackPanel { Spacing = 12 };
        foreach (var child in children) if (child is not null) stack.Children.Add(child);
        return new Border
        {
            Child = stack,
            Padding = new Thickness(20),
            CornerRadius = new CornerRadius(8),
            Background = Brush("CardBackgroundFillColorDefaultBrush"),
            BorderBrush = Brush("CardStrokeColorDefaultBrush"),
            BorderThickness = new Thickness(1),
        };
    }

    public static StackPanel Column(double spacing, params UIElement?[] children)
    {
        var stack = new StackPanel { Spacing = spacing };
        foreach (var child in children) if (child is not null) stack.Children.Add(child);
        return stack;
    }

    public static StackPanel Row(double spacing, params UIElement?[] children)
    {
        var stack = new StackPanel { Orientation = Orientation.Horizontal, Spacing = spacing };
        foreach (var child in children) if (child is not null) stack.Children.Add(child);
        return stack;
    }

    /// <summary>Children left and right in one line.</summary>
    public static Grid Split(UIElement left, UIElement? right)
    {
        var grid = new Grid { ColumnSpacing = 12 };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.Children.Add(left);
        if (right is not null)
        {
            Grid.SetColumn((FrameworkElement)right, 1);
            ((FrameworkElement)right).VerticalAlignment = VerticalAlignment.Center;
            grid.Children.Add(right);
        }
        return grid;
    }

    /// <summary>The main action of a screen: filled Mixel red.</summary>
    public static Button Primary(string text, Action action, bool enabled = true, string? glyph = null)
    {
        var button = Plain(text, action, enabled, glyph);
        button.Style = Style("AccentButtonStyle");
        return button;
    }

    public static Button Plain(string text, Action action, bool enabled = true, string? glyph = null)
    {
        object content = text;
        if (glyph is not null) content = Row(8, Icon(glyph, 14), new TextBlock { Text = text });
        var button = new Button { Content = content, IsEnabled = enabled };
        AutomationProperties.SetName(button, text);
        button.Click += (_, _) => action();
        return button;
    }

    /// <summary>A button that shows only its icon; the name is the tooltip and the Narrator name.</summary>
    public static Button IconButton(string glyph, string name, Action action, bool enabled = true, bool on = false)
    {
        var button = new Button { Content = Icon(glyph, 16), IsEnabled = enabled, Padding = new Thickness(9, 7, 9, 7) };
        if (on) button.Style = Style("AccentButtonStyle");
        AutomationProperties.SetName(button, name);
        ToolTipService.SetToolTip(button, name);
        button.Click += (_, _) => action();
        return button;
    }

    public static HyperlinkButton Link(string text, Action action)
    {
        var link = new HyperlinkButton { Content = text, Padding = new Thickness(0) };
        link.Click += (_, _) => action();
        return link;
    }

    public static CheckBox Check(string text, bool value, Action<bool> changed, bool enabled = true)
    {
        var box = new CheckBox { Content = new TextBlock { Text = text, TextWrapping = TextWrapping.Wrap }, IsChecked = value, IsEnabled = enabled };
        AutomationProperties.SetName(box, text);
        box.Checked += (_, _) => changed(true);
        box.Unchecked += (_, _) => changed(false);
        return box;
    }

    public static ToggleSwitch Switch(string text, bool value, Action<bool> changed, bool enabled = true)
    {
        var toggle = new ToggleSwitch { Header = text, IsOn = value, IsEnabled = enabled, OnContent = T.L("On"), OffContent = T.L("Off") };
        AutomationProperties.SetName(toggle, text);
        toggle.Toggled += (_, _) => { if (toggle.IsOn != value) changed(toggle.IsOn); };
        return toggle;
    }

    /// <summary>A labelled field (the label is also the Narrator name).</summary>
    public static StackPanel Field(string label, FrameworkElement input)
    {
        AutomationProperties.SetName(input, label);
        return Column(4, Caption(label), input);
    }

    /// <summary>An error (Mixel red) or a notice line; nothing when empty.</summary>
    public static UIElement? Message(string text, bool error = true)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var row = Row(8, Icon(error ? "\uE7BA" : "\uE946", 14, error ? BrandBrush : Secondary), Text(text, secondary: !error));
        ((TextBlock)row.Children[1]).MaxWidth = 640;
        if (error) ((TextBlock)row.Children[1]).Foreground = BrandBrush;
        AutomationProperties.SetName(row, text);
        AutomationProperties.SetLiveSetting(row, Microsoft.UI.Xaml.Automation.Peers.AutomationLiveSetting.Polite);
        return row;
    }

    public enum Badge { Online, Offline, Unknown, Busy, Attention }

    /// <summary>A coloured dot with a word (online, offline, …).</summary>
    public static StackPanel Status(Badge kind, string text)
    {
        var color = kind switch
        {
            Badge.Online => Brush("MixelSuccess"),
            Badge.Busy => Brush("MixelWarning"),
            Badge.Attention => BrandBrush,
            _ => Brush("TextFillColorTertiaryBrush"),
        };
        var dot = new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 8, Height = 8, Fill = color, VerticalAlignment = VerticalAlignment.Center };
        var row = Row(6, dot, Text(text, secondary: true, wrap: false));
        AutomationProperties.SetName(row, text);
        return row;
    }

    public static TextBox Box(string placeholder, string text, Action<string> changed, double maxWidth = 0)
    {
        var box = new TextBox { PlaceholderText = placeholder, Text = text };
        if (maxWidth > 0) box.MaxWidth = maxWidth;
        box.TextChanged += (_, _) => changed(box.Text);
        return box;
    }

    public static ProgressRing Spinner(double size = 16) => new() { IsActive = true, Width = size, Height = size };

    /// <summary>A choice of values; <paramref name="changed"/> gets the chosen key.</summary>
    public static ComboBox Choice(IEnumerable<(string Key, string Label)> items, string? selected, Action<string?> changed, double minWidth = 200)
    {
        var box = new ComboBox { MinWidth = minWidth };
        var list = items.ToList();
        foreach (var (_, label) in list) box.Items.Add(label);
        box.SelectedIndex = Math.Max(0, list.FindIndex(item => item.Key == selected));
        box.SelectionChanged += (_, _) =>
        {
            if (box.SelectedIndex >= 0 && box.SelectedIndex < list.Count) changed(list[box.SelectedIndex].Key);
        };
        return box;
    }
}
