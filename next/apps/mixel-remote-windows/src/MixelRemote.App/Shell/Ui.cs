using Microsoft.UI.Text;
using MixelRemote.Support;
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
    public static UIElement? Message(string text, bool error = true, double maxWidth = 640)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var row = Row(8, Icon(error ? "\uE7BA" : "\uE946", 14, error ? BrandBrush : Secondary), Text(text, secondary: !error));
        ((TextBlock)row.Children[1]).MaxWidth = maxWidth;
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

    // ---- The "Pro Console" building blocks ----

    public static Border Hairline(bool vertical = false) => new()
    {
        Background = Brush("MixelHairline"),
        Height = vertical ? double.NaN : 1,
        Width = vertical ? 1 : double.NaN,
        IsHitTestVisible = false,
    };

    /// <summary>A small rounded badge ("Unattended", "Update", "In session"): coloured text on a soft background.</summary>
    public static Border Pill(string text, string? glyph, string foreground, string background)
    {
        var fg = Brush(foreground);
        var row = Row(4);
        if (glyph is not null) row.Children.Add(Icon(glyph, 11, fg));
        row.Children.Add(new TextBlock { Text = text, FontSize = 12, FontWeight = FontWeights.SemiBold, Foreground = fg, VerticalAlignment = VerticalAlignment.Center });
        var pill = new Border { Child = row, Padding = new Thickness(7, 2, 7, 3), CornerRadius = new CornerRadius(6), Background = Brush(background), VerticalAlignment = VerticalAlignment.Center };
        AutomationProperties.SetName(pill, text);
        return pill;
    }

    /// <summary>The system's mark on a small tile; <paramref name="dot"/> is the status dot in its corner.</summary>
    public static FrameworkElement OsTile(string os, string? dotBrush, double size = 30)
    {
        var tile = new Grid { Width = size, Height = size };
        var face = new Border
        {
            CornerRadius = new CornerRadius(7), BorderThickness = new Thickness(1), Background = Brush("MixelPaper"), BorderBrush = Brush("MixelHairline"),
            Child = OsMark(os, size * 0.46),
        };
        tile.Children.Add(face);
        if (dotBrush is not null)
        {
            tile.Children.Add(new Microsoft.UI.Xaml.Shapes.Ellipse
            {
                Width = 9, Height = 9, Fill = Brush(dotBrush), Stroke = Brush("MixelPaper"), StrokeThickness = 1.5,
                HorizontalAlignment = HorizontalAlignment.Right, VerticalAlignment = VerticalAlignment.Bottom, Margin = new Thickness(0, 0, -3, -3),
            });
        }
        AutomationProperties.SetAccessibilityView(tile, Microsoft.UI.Xaml.Automation.Peers.AccessibilityView.Raw);
        return tile;
    }

    /// <summary>The system's own mark: four panes for Windows, a glyph for the others.</summary>
    public static FrameworkElement OsMark(string os, double size)
    {
        if (os.Equals("windows", StringComparison.OrdinalIgnoreCase))
        {
            var gap = Math.Max(1, size * 0.09);
            var side = (size - gap) / 2;
            var marks = new Grid { Width = size, Height = size, HorizontalAlignment = HorizontalAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
            var fill = Brush("TextFillColorPrimaryBrush");
            for (var index = 0; index < 4; index++)
            {
                marks.Children.Add(new Microsoft.UI.Xaml.Shapes.Rectangle
                {
                    Width = side, Height = side, Fill = fill, HorizontalAlignment = HorizontalAlignment.Left, VerticalAlignment = VerticalAlignment.Top,
                    Margin = new Thickness(index % 2 * (side + gap), index / 2 * (side + gap), 0, 0),
                });
            }
            return marks;
        }
        var icon = Icon(Format.OsGlyph(os), size, Brush("TextFillColorPrimaryBrush"));
        icon.HorizontalAlignment = HorizontalAlignment.Center;
        icon.VerticalAlignment = VerticalAlignment.Center;
        return icon;
    }

    /// <summary>A quick filter: a pill that is on or off.</summary>
    public static Button Chip(string text, bool on, Action action, string? dotBrush = null)
    {
        var row = Row(6);
        if (dotBrush is not null) row.Children.Add(new Microsoft.UI.Xaml.Shapes.Ellipse { Width = 7, Height = 7, Fill = Brush(dotBrush), VerticalAlignment = VerticalAlignment.Center });
        row.Children.Add(new TextBlock { Text = text, FontSize = 13, FontWeight = on ? FontWeights.SemiBold : FontWeights.Normal, Foreground = on ? BrandBrush : Brush("TextFillColorPrimaryBrush") });
        var button = new Button
        {
            Content = row, Padding = new Thickness(12, 5, 12, 5), CornerRadius = new CornerRadius(16), BorderThickness = new Thickness(1),
            Background = on ? Brush("MixelBrandSoft") : Brush("MixelPaper"), BorderBrush = on ? BrandBrush : Brush("MixelHairline"),
        };
        AutomationProperties.SetName(button, text);
        AutomationProperties.SetItemStatus(button, on ? T.L("on") : T.L("off"));
        button.Click += (_, _) => action();
        return button;
    }

    /// <summary>A flat text/icon button (no border) for the title bar and rows.</summary>
    public static Button Flat(UIElement content, string name, Action action, bool enabled = true)
    {
        var button = new Button { Content = content, IsEnabled = enabled, Background = new SolidColorBrush(Microsoft.UI.Colors.Transparent), BorderThickness = new Thickness(0), Padding = new Thickness(8, 5, 8, 5) };
        AutomationProperties.SetName(button, name);
        ToolTipService.SetToolTip(button, name);
        button.Click += (_, _) => action();
        return button;
    }

    /// <summary>A small uppercase column or section heading.</summary>
    public static TextBlock Label(string text)
    {
        var block = new TextBlock { Text = text.ToUpperInvariant(), FontSize = 11, FontWeight = FontWeights.SemiBold, CharacterSpacing = 60, Foreground = Secondary };
        AutomationProperties.SetHeadingLevel(block, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level2);
        return block;
    }

    /// <summary>A stable colour for a group's dot.</summary>
    public static Brush GroupColor(string name)
    {
        uint[] palette = [0x5B5BD6, 0x12A594, 0xE5800E, 0xB14FD6, 0x2F80ED, 0xD6409F, 0x7A8B19];
        uint hash = 5381;
        foreach (var value in System.Text.Encoding.UTF8.GetBytes(name)) hash = unchecked(hash * 33 + value);
        var color = palette[hash % palette.Length];
        return new SolidColorBrush(Windows.UI.Color.FromArgb(255, (byte)(color >> 16), (byte)(color >> 8), (byte)color));
    }
}
