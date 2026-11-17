using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using MixelRemote.App.Shell;
using MixelRemote.ViewModels;
using static MixelRemote.Support.T;

namespace MixelRemote.App.Session;

/// <summary>
/// A session in its own window ("Window" in the session toolbar). Closing the window puts the session back
/// into its tab (it does not end it); "Back into a tab" does the same from the toolbar.
/// </summary>
internal sealed class PopOutWindow
{
    private readonly AppModel app;
    private readonly uint peer;
    private readonly Window window;
    private readonly SessionView view;
    private bool closing;

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
            // The close button docks the session back into its tab.
            args.Cancel = true;
            app.Workspace.WindowClosed(peer);
        };
        window.Closed += (_, _) =>
        {
            app.Workspace.WindowDeactivated(peer);
            view.Detach();
        };
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
