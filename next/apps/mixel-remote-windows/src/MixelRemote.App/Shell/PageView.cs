using System.ComponentModel;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Shell;

/// <summary>
/// A screen built in code from the view models. It rebuilds when a model it watches changes (coalesced to
/// one rebuild per frame); text boxes are kept across rebuilds (see <see cref="Keep"/>), so typing, the
/// caret and the focus survive the engine's status updates.
/// </summary>
internal abstract class PageView : Grid
{
    private readonly List<INotifyPropertyChanged> watched = [];
    private bool pending;
    private bool active = true;

    protected PageView(AppModel app)
    {
        App = app;
        Watch(app);
        Watch(app.Session);
        ActualThemeChanged += (_, _) => Rebuild();
        // Language changes reach only pages on screen (a closed window's pages must not be touched).
        Loaded += (_, _) =>
        {
            Translator.Shared.Changed -= OnLanguage;
            Translator.Shared.Changed += OnLanguage;
            Rebuild();
        };
        Unloaded += (_, _) => Translator.Shared.Changed -= OnLanguage;
    }

    protected AppModel App { get; }

    private void OnLanguage(object? sender, EventArgs change) => Rebuild();

    private void OnWatchedChanged(object? sender, PropertyChangedEventArgs change)
    {
        if (sender is SessionModel && change.PropertyName is { } name && SessionModel.PerPictureProperties.Contains(name)) return;
        Perf.Time($"changed {sender?.GetType().Name}.{change.PropertyName}", () => { });
        Rebuild();
    }

    protected void Watch(INotifyPropertyChanged model)
    {
        watched.Add(model);
        model.PropertyChanged += OnWatchedChanged;
    }

    /// <summary>Stops watching all models so a closed tab or window is garbage-collected.</summary>
    public void Detach()
    {
        active = false;
        foreach (var model in watched) model.PropertyChanged -= OnWatchedChanged;
        watched.Clear();
        Translator.Shared.Changed -= OnLanguage;
    }

    /// <summary>Hidden pages do not rebuild (the window shows one page at a time).</summary>
    public bool Active
    {
        get => active;
        set
        {
            active = value;
            if (value) Rebuild();
        }
    }

    public void Rebuild()
    {
        if (pending || !active) return;
        pending = true;
        DispatcherQueue.TryEnqueue(DispatcherQueuePriority.Low, () =>
        {
            pending = false;
            Render();
        });
    }

    /// <summary>Builds now (screenshots, first show).</summary>
    private static long renderWindow; private static int renderCount;
    public void Render()
    {
        // Diagnostics: a page rebuilt more than 8 times in a second is logged once per second (runaway re-rendering).
        var second = Environment.TickCount64 / 1000;
        if (second != renderWindow) { if (renderCount > 8) MixelRemote.App.App.Log($"render storm: {renderCount} page builds in one second (last {GetType().Name})"); renderWindow = second; renderCount = 0; }
        renderCount++;
        try
        {
            Ui.Theme = ActualTheme == ElementTheme.Default ? ElementTheme.Light : ActualTheme;
            var focused = XamlRoot is null ? null : FocusManager.GetFocusedElement(XamlRoot) as Control;
            // Kept inputs leave the old screen first (while it is still in the tree), so they can join the new one.
            foreach (UIElement child in Children) Release(child);
            foreach (var element in kept.Values) Detach(element);
            Children.Clear();
            Perf.Time("Build " + GetType().Name, () => Children.Add(Build()));
            if (focused is not null && kept.ContainsValue(focused))
            {
                // Keyboard focus keeps the next digit in this field. Programmatic focus selects the text, so the next digit replaces it.
                focused.Focus(FocusState.Keyboard);
                if (focused is TextBox text)
                {
                    text.SelectionStart = text.Text.Length;
                    text.SelectionLength = 0;
                }
            }
        }
        catch (Exception failure)
        {
            // A screen that cannot be drawn must not end the app (and with it this computer's reachability).
            MixelRemote.App.App.Log($"{GetType().Name}: {failure}");
        }
    }

    protected abstract UIElement Build();

    private readonly Dictionary<string, FrameworkElement> kept = [];

    /// <summary>An input that lives as long as the page (detached from its last place before reuse).</summary>
    public T Keep<T>(string name, Func<T> create) where T : FrameworkElement
    {
        if (!kept.TryGetValue(name, out var found) || found is not T box)
        {
            box = create();
            kept[name] = box;
        }
        Detach(box);
        return box;
    }

    /// <summary>A kept input as it is now, without taking it out of the screen (focus it, read it).</summary>
    public T? Peek<T>(string name) where T : FrameworkElement => kept.TryGetValue(name, out var found) ? found as T : null;

    /// <summary>Takes the kept inputs out of the old screen wherever they are (panels, borders, scroll and content controls).</summary>
    private void Release(UIElement node)
    {
        switch (node)
        {
            case Panel panel:
                for (var index = panel.Children.Count - 1; index >= 0; index--)
                {
                    if (panel.Children[index] is FrameworkElement child && kept.ContainsValue(child)) panel.Children.RemoveAt(index);
                    else Release(panel.Children[index]);
                }
                break;
            case Border { Child: { } inner } border:
                if (inner is FrameworkElement element && kept.ContainsValue(element)) border.Child = null;
                else Release(inner);
                break;
            case ContentControl { Content: UIElement content } control:
                if (content is FrameworkElement element2 && kept.ContainsValue(element2)) control.Content = null;
                else Release(content);
                break;
        }
    }

    private static void Detach(FrameworkElement element)
    {
        var parent = element.Parent ?? Microsoft.UI.Xaml.Media.VisualTreeHelper.GetParent(element);
        switch (parent)
        {
            case Panel panel: panel.Children.Remove(element); break;
            case Border border: border.Child = null; break;
            case ContentControl control: control.Content = null; break;
            case ContentPresenter presenter: presenter.Content = null; break;
        }
    }

    /// <summary>A scrolling page with a title, content cards and the page's width limit.</summary>
    protected static ScrollViewer Page(string title, string subtitle, double maxWidth, params UIElement?[] content)
    {
        var stack = new StackPanel { Spacing = 16, Padding = new Thickness(32, 24, 32, 32), MaxWidth = maxWidth, HorizontalAlignment = HorizontalAlignment.Left };
        stack.Children.Add(Ui.PageTitle(title));
        if (subtitle.Length > 0) stack.Children.Add(Ui.Text(subtitle, secondary: true));
        foreach (var item in content) if (item is not null) stack.Children.Add(item);
        return new ScrollViewer { Content = stack, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled };
    }

    protected XamlRoot Root => XamlRoot;
}
