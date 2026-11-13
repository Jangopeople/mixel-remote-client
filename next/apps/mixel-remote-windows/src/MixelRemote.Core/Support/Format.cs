using System.Globalization;
using static MixelRemote.Support.T;

namespace MixelRemote.Support;

public static class Format
{
    /// <summary>A Remote ID in groups of three: 123 456 789.</summary>
    public static string RemoteId(uint? id)
    {
        if (id is not { } value) return "— — —";
        var digits = value.ToString(CultureInfo.InvariantCulture);
        return digits.Length == 9 ? $"{digits[..3]} {digits[3..6]} {digits[6..]}" : digits;
    }

    /// <summary>A typed Remote ID: nine digits, the first not 0 (spaces allowed). null otherwise.</summary>
    public static uint? ParseRemoteId(string text)
    {
        var digits = new string([.. text.Where(c => !char.IsWhiteSpace(c))]);
        if (digits.Length != 9 || !digits.All(char.IsAsciiDigit) || digits[0] == '0') return null;
        return uint.Parse(digits, CultureInfo.InvariantCulture);
    }

    public static DateTimeOffset? ParseDate(string? iso) =>
        iso is not null && DateTimeOffset.TryParse(iso, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var date) ? date : null;

    /// <summary>"Last seen 5 minutes ago" and similar, from an ISO 8601 time.</summary>
    public static string LastSeen(string? iso, DateTimeOffset? now = null)
    {
        if (ParseDate(iso) is not { } date) return "";
        var minutes = Math.Max(0, (int)Math.Round(((now ?? DateTimeOffset.UtcNow) - date).TotalMinutes));
        if (minutes < 2) return L("Last seen just now");
        if (minutes < 60) return L("Last seen %d minutes ago", minutes);
        var hours = (int)Math.Round(minutes / 60.0);
        if (hours < 24) return hours == 1 ? L("Last seen 1 hour ago") : L("Last seen %d hours ago", hours);
        var days = (int)Math.Round(hours / 24.0);
        return days == 1 ? L("Last seen yesterday") : L("Last seen %d days ago", days);
    }

    public static string Day(string? iso) =>
        ParseDate(iso) is { } date ? date.ToLocalTime().ToString("d MMM yyyy", Translator.Shared.Culture) : "";

    public static string Bytes(ulong count)
    {
        string[] units = ["bytes", "KB", "MB", "GB", "TB"];
        double value = count;
        var unit = 0;
        while (value >= 1000 && unit < units.Length - 1)
        {
            value /= 1000;
            unit++;
        }
        return unit == 0 ? $"{count} {units[0]}" : value.ToString(value < 10 ? "0.0" : "0", Translator.Shared.Culture) + " " + units[unit];
    }

    /// <summary>The operating system as people say it.</summary>
    public static string Os(string os) => os.ToLowerInvariant() switch
    {
        "macos" => "macOS",
        "windows" => "Windows",
        "linux" => "Linux",
        "ios" => "iPhone or iPad",
        "android" => "Android",
        "other" => L("phone or tablet"),
        _ => os,
    };

    /// <summary>A Segoe Fluent Icons glyph for the system.</summary>
    public static string OsGlyph(string os) => os.ToLowerInvariant() switch
    {
        "macos" => "\uE7F8",
        "windows" or "linux" => "\uE7F4",
        "ios" or "android" or "other" => "\uE8EA",
        _ => "\uE7F4",
    };

    /// <summary>The person's role in a company, translated.</summary>
    public static string Role(string role) => role switch
    {
        "owner" => L("Owner"),
        "admin" => L("Administrator"),
        "technician" => L("Technician"),
        "member" => L("Member"),
        _ => role,
    };

    public static string Plan(string plan) => plan switch
    {
        "personal" => "Personal",
        "solo" => "Solo",
        "team" => "Team",
        "business" => "Business",
        "enterprise" => "Enterprise",
        _ => plan.Length == 0 ? plan : char.ToUpperInvariant(plan[0]) + plan[1..],
    };
}
