using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using MixelRemote.App.Shell;
using MixelRemote.ViewModels;

namespace MixelRemote.App.Pages;

/// <summary>Sessions: every running session, with Open, own window / back into a tab, and End.</summary>
internal sealed class SessionsPage : PageView
{
    public SessionsPage(AppModel app) : base(app) => Watch(app.Workspace);

    protected override UIElement Build()
    {
        var workspace = App.Workspace;
        var title = new TextBlock { Text = L("Sessions"), FontSize = 26, FontWeight = Microsoft.UI.Text.FontWeights.Bold };
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetHeadingLevel(title, Microsoft.UI.Xaml.Automation.Peers.AutomationHeadingLevel.Level1);
        var stack = new StackPanel { Spacing = 14, Padding = new Thickness(28, 22, 28, 28), MaxWidth = 1000, HorizontalAlignment = HorizontalAlignment.Left };
        stack.Children.Add(title);
        stack.Children.Add(Ui.Text(workspace.Tabs.Count == 0 ? L("No session is running.") : L("%d sessions", workspace.Tabs.Count), secondary: true));
        if (workspace.Tabs.Count == 0)
        {
            stack.Children.Add(Ui.Text(L("Connect to a device under Devices; its session appears here and as a tab in the title bar."), secondary: true));
            stack.Children.Add(Ui.Plain(L("Go to Devices"), () => App.Section = AppSection.Devices));
        }
        foreach (var tab in workspace.Tabs)
        {
            var peer = tab.Peer;
            var inWindow = workspace.IsInWindow(peer);
            var info = Ui.Column(2, Ui.Text(tab.Name, bold: true, wrap: false),
                Ui.Text(Format.RemoteId(peer) + " · " + (inWindow ? L("In its own window") : L("In a tab")), secondary: true, wrap: false));
            var left = Ui.Row(12, Ui.OsTile(tab.Os, tab.State == "ready" ? "MixelSuccess" : "MixelWarning", 36), info);
            var actions = Ui.Row(8,
                Ui.Primary(L("Open"), () => workspace.Show(peer)),
                inWindow ? Ui.Plain(L("Move back into a tab"), () => workspace.Dock(peer)) : Ui.Plain(L("Open in its own window"), () => workspace.PopOut(peer)),
                Ui.Plain(L("End session"), () => _ = End(tab)));
            var card = new Border
            {
                Child = Ui.Split(left, actions), Padding = new Thickness(14), CornerRadius = new CornerRadius(12), BorderThickness = new Thickness(1),
                Background = Ui.Brush("MixelPaper"), BorderBrush = Ui.Brush("MixelHairline"),
            };
            Microsoft.UI.Xaml.Automation.AutomationProperties.SetName(card, L("Session with %@", tab.Name));
            stack.Children.Add(card);
        }
        return new Border { Child = new ScrollViewer { Content = stack, HorizontalScrollBarVisibility = ScrollBarVisibility.Disabled }, Background = Ui.Brush("MixelPaper") };
    }

    private async Task End(SessionTab tab)
    {
        if (await Dialogs.Confirm(Root, L("End the session with %@?", tab.Name), L("The connection ends for both sides."), L("End session"))) await App.Workspace.End(tab.Peer);
    }
}
