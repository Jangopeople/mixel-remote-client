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
        Translator.Shared.Changed += (_, _) => Rebuild();
        ActualThemeChanged += (_, _) => Rebuild();
        Loaded += (_, _) => Rebuild();
    }

    protected AppModel App { get; }

    protected void Watch(INotifyPropertyChanged model)
    {
        watched.Add(model);
        model.PropertyChanged += (_, _) => Rebuild();
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
    public void Render()
    {
        Ui.Theme = ActualTheme == ElementTheme.Default ? ElementTheme.Light : ActualTheme;
        var focused = XamlRoot is null ? null : FocusManager.GetFocusedElement(XamlRoot) as Control;
        Children.Clear();
        Children.Add(Build());
        if (focused is not null && kept.ContainsValue(focused)) focused.Focus(FocusState.Programmatic);
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
        if (box.Parent is Panel panel) panel.Children.Remove(box);
        else if (box.Parent is Border border) border.Child = null;
        else if (box.Parent is ContentControl control) control.Content = null;
        return box;
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
