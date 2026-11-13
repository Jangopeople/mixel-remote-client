using System.Globalization;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace MixelRemote.Support;

/// <summary>
/// Mixel Remote in English, German, French or Italian: the computer's language, or the one chosen in
/// Settings. Screen texts are English keys (<see cref="T.L"/>); German, French and Italian come from the
/// same tables as the desktop and Apple apps (texts.json, built by scripts/make-texts.py, which fails on a
/// missing translation). Texts that come from the engine (status lines, error messages) are translated
/// with the desktop app's tables and patterns. Device names, chat messages and typed values are never
/// translated.
/// </summary>
public sealed class Translator
{
    public static readonly string[] Supported = ["en", "de", "fr", "it"];
    public static Translator Shared { get; } = new();

    private sealed class Table
    {
        public Dictionary<string, string> Texts { get; set; } = [];
        public List<List<string>> Patterns { get; set; } = [];
    }

    private readonly Dictionary<string, Table> tables = [];
    private readonly Dictionary<string, List<(Regex Pattern, string Target)>> compiled = [];
    private readonly Lock gate = new();
    private string choice = "auto";

    public Translator()
    {
        using var stream = typeof(Translator).Assembly.GetManifestResourceStream("MixelRemote.texts.json");
        if (stream is null) return;
        var loaded = JsonSerializer.Deserialize<Dictionary<string, Table>>(stream, new JsonSerializerOptions { PropertyNameCaseInsensitive = true });
        if (loaded is not null) tables = loaded;
    }

    /// <summary>Raised after the language changed (screens redraw).</summary>
    public event EventHandler? Changed;

    /// <summary>auto, en, de, fr or it.</summary>
    public string Choice
    {
        get => choice;
        set
        {
            var next = value == "auto" || Supported.Contains(value) ? value : "auto";
            if (next == choice) return;
            choice = next;
            Changed?.Invoke(this, EventArgs.Empty);
        }
    }

    /// <summary>The language shown now.</summary>
    public string Language
    {
        get
        {
            if (choice != "auto") return choice;
            var system = CultureInfo.CurrentUICulture.TwoLetterISOLanguageName.ToLowerInvariant();
            return Supported.Contains(system) ? system : "en";
        }
    }

    public CultureInfo Culture => Language switch
    {
        "de" => CultureInfo.GetCultureInfo("de-CH"),
        "fr" => CultureInfo.GetCultureInfo("fr-CH"),
        "it" => CultureInfo.GetCultureInfo("it-CH"),
        _ => CultureInfo.GetCultureInfo("en-GB"),
    };

    /// <summary>Whether every language has a text for this key (used by the tests).</summary>
    public bool Knows(string key, string language) => tables.TryGetValue(language, out var table) && table.Texts.ContainsKey(key);

    /// <summary>An engine text in the shown language (unknown texts stay English).</summary>
    public string Translate(string text) => Translate(text, Language);

    public string Translate(string text, string language)
    {
        var core = text.Trim();
        if (language == "en" || core.Length == 0 || core.Length > 600 || !tables.TryGetValue(language, out var table)) return text;
        if (table.Texts.TryGetValue(core, out var hit)) return text.Replace(core, hit, StringComparison.Ordinal);
        foreach (var (pattern, target) in Patterns(language, table))
        {
            if (pattern.IsMatch(core)) return text.Replace(core, pattern.Replace(core, target), StringComparison.Ordinal);
        }
        return text;
    }

    private List<(Regex, string)> Patterns(string language, Table table)
    {
        lock (gate)
        {
            if (compiled.TryGetValue(language, out var ready)) return ready;
            ready = [];
            foreach (var pair in table.Patterns)
            {
                if (pair.Count != 2) continue;
                try { ready.Add((new Regex(pair[0], RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(50)), pair[1])); }
                catch (ArgumentException) { }
            }
            compiled[language] = ready;
            return ready;
        }
    }

    /// <summary>A screen text: the table of the shown language, else English. %@ and %d take the arguments in order.</summary>
    public string Screen(string key, object?[] args)
    {
        var text = Translate(key, Language);
        return args.Length == 0 ? text : Fill(text, args, Culture);
    }

    internal static string Fill(string text, object?[] args, IFormatProvider culture)
    {
        var result = new StringBuilder(text.Length + 16);
        var next = 0;
        for (var index = 0; index < text.Length; index++)
        {
            if (text[index] == '%' && index + 1 < text.Length && (text[index + 1] == '@' || text[index + 1] == 'd'))
            {
                var value = next < args.Length ? args[next++] : null;
                result.Append(value is IFormattable formattable && text[index + 1] == 'd' ? formattable.ToString("N0", culture).Replace('’', '\'') : Convert.ToString(value, culture));
                index++;
            }
            else
            {
                result.Append(text[index]);
            }
        }
        return result.ToString();
    }
}

/// <summary>Short names for the screens: <c>L("Devices")</c>, <c>L("Page %d · %d devices.", 1, 4)</c>.</summary>
public static class T
{
    public static string L(string key, params object?[] args) => Translator.Shared.Screen(key, args);
}
