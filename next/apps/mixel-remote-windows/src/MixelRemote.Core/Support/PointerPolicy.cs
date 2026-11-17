namespace MixelRemote.Support;

/// <summary>How the pointer over the remote picture is shown: always exactly one.</summary>
/// <remarks>
/// The hosts leave their own cursor out of the picture (Windows, Linux and, since 2.0.17, Mac hosts), so the
/// technician's cursor is the one pointer. Should a host ever draw its cursor into the picture, the local
/// cursor is hidden over the picture instead, so the two never show together. "Show pointer" mode sends
/// the pointer to the host, which marks it in the picture; the local cursor is then a crosshair.
/// </remarks>
public static class PointerPolicy
{
    public enum LocalCursor { Arrow, Cross, Hidden }

    public static LocalCursor For(bool pointerMode, bool hostDrawsCursor) =>
        hostDrawsCursor ? LocalCursor.Hidden : pointerMode ? LocalCursor.Cross : LocalCursor.Arrow;

    /// <summary>How many pointers the person sees over the picture: the local cursor, plus the host's when it is in the picture.</summary>
    public static int PointersShown(bool pointerMode, bool hostDrawsCursor) =>
        (For(pointerMode, hostDrawsCursor) == LocalCursor.Hidden ? 0 : 1) + (hostDrawsCursor ? 1 : 0);
}
