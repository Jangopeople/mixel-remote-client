using Microsoft.Graphics.Canvas;
using Microsoft.Graphics.Canvas.UI.Xaml;
using Microsoft.UI;
using Microsoft.UI.Input;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media;
using MixelRemote.Models;
using MixelRemote.ViewModels;
using Windows.Foundation;
using Windows.Graphics.DirectX;

namespace MixelRemote.App.Session;

/// <summary>
/// The remote screen: each picture is uploaded to Direct3D (Win2D swap chain in a SwapChainPanel) and drawn
/// letterboxed; mouse, wheel and keyboard go to the other computer while the person may control it.
/// </summary>
internal sealed class RemoteScreen : Grid
{
    private readonly SessionModel session;
    private readonly CanvasSwapChainPanel panel = new();
    private readonly CursorGrid input = new() { Background = new SolidColorBrush(Colors.Transparent), IsTabStop = true, UseSystemFocusVisuals = false };
    private readonly CanvasDevice device;
    private readonly HashSet<ushort> held = [];
    private readonly Dictionary<uint, byte> pressed = [];
    private CanvasSwapChain? chain;
    private VideoFrame? last;
    private Rect picture;
    private double wheelX, wheelY;
    private (DateTimeOffset At, Point Where, byte Button, byte Count) lastClick;
    private KeyboardHook? hook;
    private Point pointer;
    private CanvasBitmap? bitmap;
    private VideoFrame? bitmapFrame;

    /// <summary>Fit the picture into the window (default), or show it pixel for pixel, panned with the mouse.</summary>
    public bool FitToWindow { get; private set; } = true;

    public void ToggleFit()
    {
        FitToWindow = !FitToWindow;
        if (last is not null) Draw(last);
    }

    public RemoteScreen(SessionModel session)
    {
        this.session = session;
        device = CanvasDevice.GetSharedDevice();
        Background = new SolidColorBrush(Colors.Black);
        Children.Add(panel);
        Children.Add(input);
        AutomationProperties.SetName(input, L("Remote screen"));
        AutomationProperties.SetHelpText(input, L("Click to control the remote computer. Ctrl+Alt+Home gives the keyboard back."));
        SizeChanged += (_, _) => Resize();
        panel.CompositionScaleChanged += (_, _) => Resize();
        session.FrameArrived += OnFrame;
        session.DrawingChanged += OnDrawing;
        session.PropertyChanged += OnSession;
        input.PointerEntered += (_, _) => { inside = true; UpdateCursor(); };
        input.PointerExited += (_, _) => { inside = false; UpdateCursor(); };
        input.PointerMoved += (sender, e) => Moved(e);
        input.PointerPressed += (sender, e) => Pressed(e);
        input.PointerReleased += (sender, e) => Released(e);
        input.PointerCaptureLost += (_, _) => ReleaseButtons();
        input.PointerWheelChanged += (sender, e) => Wheel(e);
        input.GotFocus += (_, _) => hook?.Start();
        input.LostFocus += (_, _) => ReleaseKeyboard();
        // Keys reach the remote computer through the hook; nothing typed here acts on this window.
        input.KeyDown += (sender, e) => e.Handled = true;
        input.KeyUp += (sender, e) => e.Handled = true;
        AllowDrop = true;
        DragOver += (_, e) =>
        {
            if (session.Remote.Files && e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems))
            {
                e.AcceptedOperation = Windows.ApplicationModel.DataTransfer.DataPackageOperation.Copy;
            }
        };
        Drop += async (_, e) =>
        {
            if (!session.Remote.Files || !e.DataView.Contains(Windows.ApplicationModel.DataTransfer.StandardDataFormats.StorageItems)) return;
            var items = await e.DataView.GetStorageItemsAsync();
            var file = items.OfType<Windows.Storage.StorageFile>().FirstOrDefault();
            if (file is not null && !string.IsNullOrEmpty(file.Path)) await session.SendFile(file.Path);
        };
        Unloaded += (_, _) => ReleaseKeyboard();
    }

    /// <summary>Wires the keyboard hook to this window (needs its handle).</summary>
    public void Attach(nint window, Action ctrlAltDel, Action release, Action fullScreen, Action end)
    {
        hook = new KeyboardHook(window, (scan, extended, down) =>
        {
            // Only the focused session takes the keyboard; a session in the background never swallows a key.
            if (!session.Controllable || !session.Focused) return false;
            if (KeyMap.Usage(scan, extended) is not { } usage) return true;
            var ctrl = held.Contains(KeyMap.Control) || held.Contains(228);
            var alt = held.Contains(KeyMap.Alt) || held.Contains(230);
            if (down && ctrl && alt && !KeyMap.Modifiers.Contains(usage))
            {
                Action? shortcut = usage switch { KeyMap.End => ctrlAltDel, 74 => release, 40 => fullScreen, 20 => end, _ => null };
                if (shortcut is not null)
                {
                    DispatcherQueue.TryEnqueue(() => shortcut());
                    return true;
                }
            }
            if (down)
            {
                held.Add(usage);
                var cmd = held.Contains(KeyMap.Windows) || held.Contains(231);
                if ((ctrl || cmd) && !alt)
                {
                    if (usage == 25)
                    {
                        _ = session.PasteToRemote(usage, true);
                        return true;
                    }
                    if (usage is 6 or 27)
                    {
                        session.Send(new InputEvent.Key(usage, true));
                        session.PullClipboardSoon();
                        return true;
                    }
                }
            }
            else if (!held.Remove(usage))
            {
                return true;
            }
            session.Send(new InputEvent.Key(usage, down));
            return true;
        });
    }

    private bool active = true;

    /// <summary>A session tab behind another one keeps the newest picture but draws nothing (four sessions drew four swap chains, 4 x 45 ms per picture, and starved the window).</summary>
    public void SetActive(bool value)
    {
        if (active == value) return;
        active = value;
        if (value && last is not null) Shell.Perf.Time("Draw frame", () => Draw(last));
    }

    private void OnFrame(object? sender, VideoFrame frame)
    {
        if (!active) { last = frame; return; }
        Shell.Perf.Time("Draw frame", () => Draw(frame));
    }
    private void OnDrawing(object? sender, EventArgs args) { if (last is not null) Draw(last); }
    private void OnSession(object? sender, System.ComponentModel.PropertyChangedEventArgs change)
    {
        if (change.PropertyName == nameof(SessionModel.PointerMode)) UpdateCursor();
    }

    private bool inside;
    private bool firstDrawn;
    private bool cursorHidden;

    /// <summary>The host's cursor is in the picture (no host does this today; see <see cref="PointerPolicy"/>).</summary>
    public bool HostDrawsCursor { get; set; }

    /// <summary>Exactly one pointer over the picture: the local cursor, a crosshair in "Show pointer" mode, or none when the host's is drawn.</summary>
    private void UpdateCursor()
    {
        var wanted = PointerPolicy.For(session.PointerMode, HostDrawsCursor);
        var shape = wanted == PointerPolicy.LocalCursor.Cross
            ? InputSystemCursorShape.Cross
            : session.Remote.CursorShape switch
            {
                "ibeam" => InputSystemCursorShape.IBeam,
                "pointing" => InputSystemCursorShape.Hand,
                "resize_ew" => InputSystemCursorShape.SizeWestEast,
                "resize_ns" => InputSystemCursorShape.SizeNorthSouth,
                "resize_nwse" => InputSystemCursorShape.SizeNorthwestSoutheast,
                "resize_nesw" => InputSystemCursorShape.SizeNortheastSouthwest,
                "crosshair" => InputSystemCursorShape.Cross,
                "not_allowed" => InputSystemCursorShape.UniversalNo,
                "wait" => InputSystemCursorShape.Wait,
                "grab" => InputSystemCursorShape.SizeAll,
                _ => InputSystemCursorShape.Arrow,
            };
        input.Use(shape);
        var hide = inside && wanted == PointerPolicy.LocalCursor.Hidden;
        if (hide == cursorHidden) return;
        cursorHidden = hide;
        _ = Shell.Native.ShowCursor(!hide);
    }

    /// <summary>Stops listening to the session (the screen is leaving its window).</summary>
    public void Detach()
    {
        session.FrameArrived -= OnFrame;
        session.DrawingChanged -= OnDrawing;
        session.PropertyChanged -= OnSession;
        ReleaseKeyboard();
        if (cursorHidden)
        {
            cursorHidden = false;
            _ = Shell.Native.ShowCursor(true);
        }
    }

    public void FocusScreen() => input.Focus(FocusState.Programmatic);

    /// <summary>Lets go of every key and button (focus lost, window left, session ended).</summary>
    public void ReleaseKeyboard()
    {
        hook?.Stop();
        if (held.Count > 0 || pressed.Count > 0) session.Send(new InputEvent.ReleaseAll());
        held.Clear();
        pressed.Clear();
    }

    private void Resize()
    {
        var scale = panel.CompositionScaleX;
        if (ActualWidth < 1 || ActualHeight < 1) return;
        if (chain is null)
        {
            chain = new CanvasSwapChain(device, (float)ActualWidth, (float)ActualHeight, (float)(96 * scale));
            panel.SwapChain = chain;
        }
        else
        {
            chain.ResizeBuffers((float)ActualWidth, (float)ActualHeight, (float)(96 * scale));
        }
        if (last is not null) Draw(last);
    }

    private void Draw(VideoFrame frame)
    {
        last = frame;
        if (chain is null)
        {
            // First picture, or the Direct3D device was lost: (re)create the swap chain, which draws it.
            if (ActualWidth >= 1 && ActualHeight >= 1) Resize();
            return;
        }
        var (w, h) = ((double)ActualWidth, (double)ActualHeight);
        var fit = FitToWindow ? Math.Min(w / frame.Width, h / frame.Height) : 1 / Math.Max(0.5, panel.CompositionScaleX);
        var (pw, ph) = (frame.Width * fit, frame.Height * fit);
        // Larger than the window (actual size): the mouse position pans the picture.
        var x = pw <= w ? (w - pw) / 2 : -(pw - w) * Math.Clamp(pointer.X / Math.Max(1, w), 0, 1);
        var y = ph <= h ? (h - ph) / 2 : -(ph - h) * Math.Clamp(pointer.Y / Math.Max(1, h), 0, 1);
        picture = new Rect(x, y, pw, ph);
        try
        {
            var clock = System.Diagnostics.Stopwatch.StartNew();
            double copied = 0, drawn;
            if (!ReferenceEquals(bitmapFrame, frame) || bitmap is null)
            {
                bitmap?.Dispose();
                bitmap = frame.IsPackedBgra
                    ? CanvasBitmap.CreateFromBytes(device, frame.ToBgraCopy(), frame.Width, frame.Height, DirectXPixelFormat.B8G8R8A8UIntNormalized, 96f, CanvasAlphaMode.Ignore)
                    : CanvasBitmap.CreateFromBytes(device, frame.ToRgba(), frame.Width, frame.Height, DirectXPixelFormat.R8G8B8A8UIntNormalized);
                bitmapFrame = frame;
                copied = clock.Elapsed.TotalMilliseconds;
            }
            using (var drawing = chain.CreateDrawingSession(Colors.Black))
            {
                drawing.DrawImage(bitmap, picture, new Rect(0, 0, frame.Width, frame.Height), 1, fit >= 1 ? CanvasImageInterpolation.NearestNeighbor : CanvasImageInterpolation.Linear);
                DrawStrokes(drawing);
            }
            drawn = clock.Elapsed.TotalMilliseconds - copied;
            chain.Present();
            Shell.Perf.LabPicture(copied, drawn, clock.Elapsed.TotalMilliseconds - copied - drawn);
            if (!firstDrawn) { firstDrawn = true; Shell.Perf.LabMark("first picture presented"); }
        }
        catch (Exception failure) when (device.IsDeviceLost(failure.HResult))
        {
            App.Log("Direct3D device lost; the picture resumes with the next frame");
            chain = null;
            bitmap = null;
        }
    }

    /// <summary>The technician's own drawing over the picture, as the other computer shows it.</summary>
    private void DrawStrokes(Microsoft.Graphics.Canvas.CanvasDrawingSession drawing)
    {
        double[] pen = [3, 5, 8, 12], highlight = [14, 20, 28, 36];
        foreach (var stroke in session.Strokes)
        {
            if (stroke.Points.Count == 0) continue;
            var (r, g, b) = SessionModel.Palette[Math.Min(stroke.Color, SessionModel.Palette.Length - 1)];
            var color = Windows.UI.Color.FromArgb(stroke.Tool == "highlighter" ? (byte)110 : (byte)255, r, g, b);
            var step = Math.Clamp((int)stroke.Width, 1, 4) - 1;
            var width = (float)Math.Max(1, (stroke.Tool == "highlighter" ? highlight[step] : pen[step]) * picture.Height / 1080);
            System.Numerics.Vector2 At((ushort X, ushort Y) point) => new((float)(picture.X + point.X / 65535.0 * picture.Width), (float)(picture.Y + point.Y / 65535.0 * picture.Height));
            var style = new Microsoft.Graphics.Canvas.Geometry.CanvasStrokeStyle { StartCap = Microsoft.Graphics.Canvas.Geometry.CanvasCapStyle.Round, EndCap = Microsoft.Graphics.Canvas.Geometry.CanvasCapStyle.Round, LineJoin = Microsoft.Graphics.Canvas.Geometry.CanvasLineJoin.Round };
            if (stroke.Tool == "arrow" && stroke.Points.Count > 1)
            {
                var (from, to) = (At(stroke.Points[0]), At(stroke.Points[^1]));
                drawing.DrawLine(from, to, color, width, style);
                var angle = Math.Atan2(to.Y - from.Y, to.X - from.X);
                var head = width * 4;
                foreach (var side in new[] { -0.5, 0.5 })
                {
                    var end = new System.Numerics.Vector2((float)(to.X - head * Math.Cos(angle + side)), (float)(to.Y - head * Math.Sin(angle + side)));
                    drawing.DrawLine(to, end, color, width, style);
                }
                continue;
            }
            for (var index = 1; index < stroke.Points.Count; index++) drawing.DrawLine(At(stroke.Points[index - 1]), At(stroke.Points[index]), color, width, style);
            if (stroke.Points.Count == 1) drawing.FillCircle(At(stroke.Points[0]), width / 2, color);
        }
    }

    /// <summary>The picture as JPEG base64 (at most 1280 wide) for the AI assistant.</summary>
    public string? Snapshot()
    {
        if (last is not { } frame) return null;
        var scale = Math.Min(1, 1280.0 / frame.Width);
        var (width, height) = ((int)(frame.Width * scale), (int)(frame.Height * scale));
        using var source = CanvasBitmap.CreateFromBytes(device, frame.ToRgba(), frame.Width, frame.Height, DirectXPixelFormat.R8G8B8A8UIntNormalized);
        using var target = new CanvasRenderTarget(device, width, height, 96);
        using (var drawing = target.CreateDrawingSession()) drawing.DrawImage(source, new Rect(0, 0, width, height));
        using var stream = new Windows.Storage.Streams.InMemoryRandomAccessStream();
        target.SaveAsync(stream, CanvasBitmapFileFormat.Jpeg, 0.72f).AsTask().GetAwaiter().GetResult();
        var bytes = new byte[stream.Size];
        using var reader = new Windows.Storage.Streams.DataReader(stream.GetInputStreamAt(0));
        reader.LoadAsync((uint)stream.Size).AsTask().GetAwaiter().GetResult();
        reader.ReadBytes(bytes);
        return Convert.ToBase64String(bytes);
    }

    private (double X, double Y)? PicturePoint(PointerRoutedEventArgs e, bool clamp = false)
    {
        if (picture.Width < 1 || picture.Height < 1) return null;
        var point = e.GetCurrentPoint(input).Position;
        var x = (point.X - picture.X) / picture.Width;
        var y = (point.Y - picture.Y) / picture.Height;
        if (!clamp && (x < 0 || y < 0 || x > 1 || y > 1)) return null;
        return (Math.Clamp(x, 0, 1), Math.Clamp(y, 0, 1));
    }

    private void Moved(PointerRoutedEventArgs e)
    {
        if (session.Drawing)
        {
            if (pressed.ContainsKey(0) && PicturePoint(e, clamp: true) is { } at) session.ContinueStroke(at.X, at.Y);
            return;
        }
        if (!FitToWindow && last is not null)
        {
            pointer = e.GetCurrentPoint(input).Position;
            Draw(last);
        }
        if (!session.Focused || (!session.Controllable && !session.PointerMode)) return;
        if (PicturePoint(e, clamp: pressed.Count > 0) is { } point) session.MoveTo(point.X, point.Y);
    }

    private static byte? Button(PointerPointProperties properties) => properties.PointerUpdateKind switch
    {
        PointerUpdateKind.LeftButtonPressed or PointerUpdateKind.LeftButtonReleased => 1,
        PointerUpdateKind.MiddleButtonPressed or PointerUpdateKind.MiddleButtonReleased => 2,
        PointerUpdateKind.RightButtonPressed or PointerUpdateKind.RightButtonReleased => 3,
        PointerUpdateKind.XButton1Pressed or PointerUpdateKind.XButton1Released => 4,
        PointerUpdateKind.XButton2Pressed or PointerUpdateKind.XButton2Released => 5,
        _ => null,
    };

    private void Pressed(PointerRoutedEventArgs e)
    {
        input.Focus(FocusState.Pointer);
        if (session.Drawing)
        {
            if (PicturePoint(e) is { } at && e.GetCurrentPoint(input).Properties.IsLeftButtonPressed)
            {
                pressed[0] = 1;
                input.CapturePointer(e.Pointer);
                session.BeginStroke(at.X, at.Y);
            }
            e.Handled = true;
            return;
        }
        if (!session.Focused || !session.Controllable || session.PointerMode || PicturePoint(e) is not { } point) return;
        var properties = e.GetCurrentPoint(input).Properties;
        if (Button(properties) is not { } button) return;
        session.MoveTo(point.X, point.Y);
        var now = DateTimeOffset.UtcNow;
        var where = e.GetCurrentPoint(input).Position;
        var near = Math.Abs(where.X - lastClick.Where.X) < 4 && Math.Abs(where.Y - lastClick.Where.Y) < 4;
        var clicks = (byte)(button == lastClick.Button && near && now - lastClick.At < TimeSpan.FromMilliseconds(500) ? Math.Min(3, lastClick.Count + 1) : 1);
        lastClick = (now, where, button, clicks);
        pressed[e.Pointer.PointerId * 8 + button] = clicks;
        input.CapturePointer(e.Pointer);
        session.Send(new InputEvent.ButtonClick(button, true, clicks));
        e.Handled = true;
    }

    private void Released(PointerRoutedEventArgs e)
    {
        if (session.Drawing || pressed.ContainsKey(0))
        {
            if (pressed.Remove(0)) session.EndStroke();
            input.ReleasePointerCaptures();
            e.Handled = true;
            return;
        }
        var properties = e.GetCurrentPoint(input).Properties;
        if (Button(properties) is not { } button || !pressed.Remove(e.Pointer.PointerId * 8 + button, out var clicks)) return;
        if (PicturePoint(e, clamp: true) is { } point) session.MoveTo(point.X, point.Y);
        session.Send(new InputEvent.ButtonClick(button, false, clicks));
        if (pressed.Count == 0) input.ReleasePointerCaptures();
        e.Handled = true;
    }

    private void ReleaseButtons()
    {
        if (pressed.Count == 0) return;
        pressed.Clear();
        session.Send(new InputEvent.ReleaseAll());
    }

    private void Wheel(PointerRoutedEventArgs e)
    {
        if (!session.Controllable || PicturePoint(e) is null) return;
        var properties = e.GetCurrentPoint(input).Properties;
        var steps = properties.MouseWheelDelta / 120.0;
        if (properties.IsHorizontalMouseWheel) wheelX = Math.Clamp(wheelX + steps, -120, 120);
        else wheelY = Math.Clamp(wheelY + steps, -120, 120);
        var (horizontal, vertical) = ((short)Math.Truncate(wheelX), (short)Math.Truncate(wheelY));
        wheelX -= horizontal;
        wheelY -= vertical;
        if (horizontal != 0 || vertical != 0) session.Send(new InputEvent.Wheel(horizontal, vertical));
        e.Handled = true;
    }
}

/// <summary>A grid whose mouse cursor the screen sets (the cursor property is protected).</summary>
internal sealed class CursorGrid : Grid
{
    public void Use(InputSystemCursorShape shape) => ProtectedCursor = InputSystemCursor.Create(shape);
}
