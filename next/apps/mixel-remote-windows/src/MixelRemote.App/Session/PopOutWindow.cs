using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Shell;
using MixelRemote.ViewModels;
using static MixelRemote.Support.T;

namespace MixelRemote.App.Session;

/// <summary>
/// A session in its own window ("Window" in the session toolbar). Closing the window ends the session after
/// "End session with ...?" (the same as on the Mac); "Back into a tab" in the toolbar docks it.
/// </summary>
internal sealed class PopOutWindow
{
    private readonly AppModel app;
    private readonly uint peer;
    private readonly Window window;
    private readonly SessionView view;
    private bool closing;
    private bool asking;

    public PopOutWindow(AppModel app, uint peer)
    {
        this.app = app;
        this.peer = peer;
        window = new Window { Title = Title() };
        window.AppWindow.SetIcon(Path.Combine(AppContext.BaseDirectory, "Assets", "mixel-remote.ico"));
        window.AppWindow.Resize(new Windows.Graphics.SizeInt32(1360, 880));
        if (Microsoft.UI.Composition.SystemBackdrops.MicaController.IsSupported()) window.SystemBackdrop = new MicaBackdrop();
        view = new SessionView(app, window, peer, inWindow: true);
        window.Content = view;
        Ui.FollowTheme(window);
        window.Activated += (sender, args) =>
        {
            // Only the window that is active gets keyboard, mouse, clipboard and toolbar actions of its session.
            if (args.WindowActivationState == WindowActivationState.Deactivated)
            {
                view.Screen.ReleaseKeyboard();
                app.Workspace.WindowDeactivated(peer);
            }
            else app.Workspace.WindowActivated(peer);
        };
        window.AppWindow.Closing += (sender, args) =>
        {
            if (closing) return;
            // The close button ends the session after a confirmation; the Dock button is what moves it back into a tab.
            args.Cancel = true;
            _ = AskToEnd();
        };
        window.Closed += (_, _) =>
        {
            app.Workspace.WindowDeactivated(peer);
            view.Detach();
        };
    }

    private async Task AskToEnd()
    {
        if (asking) return;
        var decision = app.Workspace.WindowCloseRequested(peer);
        if (!decision.Confirm)
        {
            // Its session is gone already: nothing to ask, nothing to dock.
            app.Workspace.WindowGone(peer);
            Close();
            return;
        }
        asking = true;
        try
        {
            if (await Dialogs.Confirm(window.Content.XamlRoot, decision.Title, decision.Text, decision.Action)) await app.Workspace.ConfirmWindowClose(peer);
        }
        catch (Exception failure) { MixelRemote.App.App.Log($"session window close: {failure}"); }
        finally { asking = false; }
    }

    private string Title() => L("%@ — Mixel Remote", app.Workspace.Tab(peer)?.Name ?? MixelRemote.Support.Format.RemoteId(peer));

    public nint Handle => WinRT.Interop.WindowNative.GetWindowHandle(window);

    public void Show()
    {
        window.Title = Title();
        window.AppWindow.Show();
        window.Activate();
        view.Screen.FocusScreen();
    }

    public void Close()
    {
        closing = true;
        if (window.AppWindow.Presenter.Kind == AppWindowPresenterKind.FullScreen) window.AppWindow.SetPresenter(AppWindowPresenterKind.Overlapped);
        window.Close();
    }
}
