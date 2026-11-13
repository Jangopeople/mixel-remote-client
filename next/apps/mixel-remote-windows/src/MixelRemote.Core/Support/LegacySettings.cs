using System.Text;
using System.Text.Json;

namespace MixelRemote.Support;

/// <summary>
/// Moves the small settings of Mixel Remote 0.1 (the Tauri app kept them in its web view's local storage)
/// into this app's preferences once, so an update to 2.0 keeps them: language, unattended sharing, this
/// computer's registration, Wake-on-LAN addresses, automatic updates and the AI server. The device key, the
/// remembered sign-in, start with Windows and the always-on service are kept by the engine and Windows
/// themselves and need no import.
///
/// Chromium stores local storage in LevelDB: each record holds the key "_&lt;origin&gt;\0\x01&lt;name&gt;" and the
/// value with a one-byte encoding mark (1: Latin-1, 0: UTF-16LE). The newest record of a key wins; the
/// reader looks at the write-ahead log and the table files, newest first, and accepts a value only when
/// it is the JSON (or word) the 0.1 app wrote.
/// </summary>
public static class LegacySettings
{
    public const string DoneKey = "mixel.remote.imported-0.1.v1";

    /// <summary>The keys the 0.1 app used (same names as here).</summary>
    public static readonly string[] Keys =
    [
        "mixel.remote.language.v1", "mixel.remote.unattended.v1", "mixel.remote.this-computer.v1",
        "mixel.remote.macs.v1", "mixel.remote.autoupdate.v1", "mixel.remote.ai.v1",
    ];

    /// <summary>The 0.1 app's local storage folder.</summary>
    public static string DefaultFolder(string localAppData) =>
        Path.Combine(localAppData, "ch.mixel.remote.desktop", "EBWebView", "Default", "Local Storage", "leveldb");

    /// <summary>Imports once; returns the keys taken over.</summary>
    public static IReadOnlyList<string> Import(string folder, IPreferences preferences)
    {
        if (preferences.Get(DoneKey) is not null) return [];
        var found = Read(folder);
        var taken = new List<string>();
        foreach (var (key, value) in found)
        {
            if (preferences.Get(key) is not null) continue;
            preferences.Set(key, value);
            taken.Add(key);
        }
        preferences.Set(DoneKey, "1");
        return taken;
    }

    public static Dictionary<string, string> Read(string folder)
    {
        var result = new Dictionary<string, string>();
        if (!Directory.Exists(folder)) return result;
        var files = Directory.EnumerateFiles(folder)
            .Where(path => path.EndsWith(".log", StringComparison.OrdinalIgnoreCase) || path.EndsWith(".ldb", StringComparison.OrdinalIgnoreCase))
            .Select(path => new FileInfo(path))
            .Where(file => file.Length is > 0 and < 32 * 1024 * 1024)
            // Newest first; inside a file the last record wins.
            .OrderByDescending(file => file.LastWriteTimeUtc)
            .ThenByDescending(file => file.Name, StringComparer.Ordinal);
        foreach (var file in files)
        {
            byte[] bytes;
            try { bytes = File.ReadAllBytes(file.FullName); }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { continue; }
            foreach (var key in Keys)
            {
                if (result.ContainsKey(key)) continue;
                if (LastValue(bytes, key) is { } value) result[key] = value;
            }
        }
        return result;
    }

    internal static string? LastValue(byte[] bytes, string key)
    {
        var marker = Encoding.Latin1.GetBytes("\0\u0001" + key);
        var span = bytes.AsSpan();
        for (var at = span.LastIndexOf(marker); at >= 0; at = at == 0 ? -1 : span[..at].LastIndexOf(marker))
        {
            var after = at + marker.Length;
            // The value follows the key directly (table files) or after its length (log records).
            for (var skip = 0; skip <= 5 && after + skip < bytes.Length; skip++)
            {
                if (Value(bytes, after + skip, key) is { } value) return value;
            }
        }
        return null;
    }

    private static string? Value(byte[] bytes, int start, string key)
    {
        var mark = bytes[start];
        if (mark is not (0 or 1)) return null;
        var text = mark == 1
            ? Encoding.Latin1.GetString(bytes, start + 1, Math.Min(64 * 1024, bytes.Length - start - 1))
            : Encoding.Unicode.GetString(bytes, start + 1, Math.Min(128 * 1024, (bytes.Length - start - 1) & ~1));
        if (key is "mixel.remote.language.v1")
        {
            foreach (var language in new[] { "en", "de", "fr", "it" })
            {
                if (text.StartsWith(language, StringComparison.Ordinal) && (text.Length == 2 || !char.IsAsciiLetter(text[2]))) return language;
            }
            return null;
        }
        if (key is "mixel.remote.autoupdate.v1")
        {
            return text.StartsWith("off", StringComparison.Ordinal) ? "off" : text.StartsWith("on", StringComparison.Ordinal) ? "on" : null;
        }
        if (text.Length == 0 || text[0] != '{') return null;
        // The longest prefix that is one JSON object.
        var depth = 0;
        var quoted = false;
        for (var index = 0; index < text.Length; index++)
        {
            var c = text[index];
            if (quoted)
            {
                if (c == '\\') index++;
                else if (c == '"') quoted = false;
                continue;
            }
            if (c == '"') quoted = true;
            else if (c == '{' || c == '[') depth++;
            else if (c == '}' || c == ']')
            {
                depth--;
                if (depth == 0)
                {
                    var candidate = text[..(index + 1)];
                    try
                    {
                        using var document = JsonDocument.Parse(candidate);
                        return document.RootElement.ValueKind == JsonValueKind.Object ? candidate : null;
                    }
                    catch (JsonException)
                    {
                        return null;
                    }
                }
            }
            else if (char.IsControl(c) && c != '\t') return null;
        }
        return null;
    }
}
