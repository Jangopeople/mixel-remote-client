namespace MixelRemote.Support;

/// <summary>
/// Keys travel as USB HID usages (page 7), the same numbers the desktop and Apple apps send. Windows
/// reports scan codes (set 1, positions independent of the keyboard layout) plus an "extended" flag; this
/// table turns them into HID usages, so a Swiss German keyboard types the same keys on the other computer.
/// </summary>
public static class KeyMap
{
    private static readonly Dictionary<ushort, ushort> Plain = new()
    {
        [0x1E] = 4, [0x30] = 5, [0x2E] = 6, [0x20] = 7, [0x12] = 8, [0x21] = 9, [0x22] = 10, [0x23] = 11, [0x17] = 12, [0x24] = 13,
        [0x25] = 14, [0x26] = 15, [0x32] = 16, [0x31] = 17, [0x18] = 18, [0x19] = 19, [0x10] = 20, [0x13] = 21, [0x1F] = 22, [0x14] = 23,
        [0x16] = 24, [0x2F] = 25, [0x11] = 26, [0x2D] = 27, [0x15] = 28, [0x2C] = 29,
        [0x02] = 30, [0x03] = 31, [0x04] = 32, [0x05] = 33, [0x06] = 34, [0x07] = 35, [0x08] = 36, [0x09] = 37, [0x0A] = 38, [0x0B] = 39,
        [0x1C] = 40, [0x01] = 41, [0x0E] = 42, [0x0F] = 43, [0x39] = 44, [0x0C] = 45, [0x0D] = 46, [0x1A] = 47, [0x1B] = 48, [0x2B] = 49,
        [0x27] = 51, [0x28] = 52, [0x29] = 53, [0x33] = 54, [0x34] = 55, [0x35] = 56, [0x3A] = 57,
        [0x3B] = 58, [0x3C] = 59, [0x3D] = 60, [0x3E] = 61, [0x3F] = 62, [0x40] = 63, [0x41] = 64, [0x42] = 65, [0x43] = 66, [0x44] = 67,
        [0x57] = 68, [0x58] = 69, [0x46] = 71, [0x45] = 72,
        [0x37] = 85, [0x4A] = 86, [0x4E] = 87, [0x4F] = 89, [0x50] = 90, [0x51] = 91, [0x4B] = 92, [0x4C] = 93, [0x4D] = 94,
        [0x47] = 95, [0x48] = 96, [0x49] = 97, [0x52] = 98, [0x53] = 99, [0x56] = 100,
        [0x64] = 104, [0x65] = 105, [0x66] = 106, [0x67] = 107, [0x68] = 108, [0x69] = 109, [0x6A] = 110, [0x6B] = 111,
        [0x1D] = 224, [0x2A] = 225, [0x38] = 226, [0x36] = 229, [0x73] = 135, [0x7D] = 137, [0x79] = 138, [0x7B] = 139, [0x70] = 136,
    };

    private static readonly Dictionary<ushort, ushort> Extended = new()
    {
        [0x1C] = 88, [0x35] = 84, [0x37] = 70, [0x38] = 230, [0x1D] = 228, [0x5B] = 227, [0x5C] = 231, [0x5D] = 101,
        [0x52] = 73, [0x47] = 74, [0x49] = 75, [0x53] = 76, [0x4F] = 77, [0x51] = 78, [0x4D] = 79, [0x4B] = 80, [0x50] = 81, [0x48] = 82,
        [0x45] = 83, [0x46] = 72,
    };

    /// <summary>The HID usage of a Windows scan code; null for keys that are not sent.</summary>
    public static ushort? Usage(ushort scanCode, bool extended)
    {
        // NumLock is the extended 0x45; Pause the plain 0x45 (after an E1 prefix), Ctrl+Break the extended 0x46.
        if (extended && Extended.TryGetValue(scanCode, out var special)) return special;
        if (!extended && Plain.TryGetValue(scanCode, out var usage)) return usage;
        return null;
    }

    /// <summary>Modifier keys (HID usage).</summary>
    public static readonly IReadOnlySet<ushort> Modifiers = new HashSet<ushort> { 224, 225, 226, 227, 228, 229, 230, 231 };

    public const ushort Control = 224;
    public const ushort Shift = 225;
    public const ushort Alt = 226;
    public const ushort Windows = 227;
    public const ushort End = 77;
    public const ushort Delete = 76;
}
