using MixelRemote.Models;

namespace MixelRemote.ViewModels;

/// <summary>What the regular update check does with a newer version.</summary>
public enum UpdateAction
{
    /// <summary>Nothing to do (no update, already installing, or it becomes silent in a moment).</summary>
    Nothing,
    /// <summary>Install now, nothing is interrupted.</summary>
    Install,
    /// <summary>A session runs here (or ended a moment ago): ready, installs after it.</summary>
    WaitForSession,
    /// <summary>A failed install is retried later (back-off).</summary>
    BackOff,
    /// <summary>The update would sign the person out (no "stay signed in") or they turned automatic updates off:
    /// show a clear "Update available" prompt with an "Update now" button instead of staying silent for ever.</summary>
    Prompt,
}

/// <summary>
/// The decision of the regular update check as a pure function (tested without an app): when to install by itself,
/// when to wait for a session, when to retry a failed install, and when to ask the person.
/// </summary>
public static class UpdatePolicy
{
    private static readonly TimeSpan FirstRetry = TimeSpan.FromMinutes(5);
    private static readonly TimeSpan LongestRetry = TimeSpan.FromHours(4);

    /// <summary>Waiting time after the n-th failed install in a row: 5 min, 10, 20, 40 ... at most 4 hours.</summary>
    public static TimeSpan BackOff(int failures)
    {
        if (failures <= 0) return TimeSpan.Zero;
        var minutes = FirstRetry.TotalMinutes * Math.Pow(2, Math.Min(failures - 1, 10));
        return TimeSpan.FromMinutes(Math.Min(minutes, LongestRetry.TotalMinutes));
    }

    public static UpdateAction Decide(UpdateInfo? update, bool autoUpdate, bool installing, int failures, DateTimeOffset? lastFailure, DateTimeOffset now)
    {
        if (update is not { Installable: true } || installing) return UpdateAction.Nothing;
        if (update.WaitsForSession) return UpdateAction.WaitForSession;
        if (!autoUpdate) return UpdateAction.Prompt;
        if (!update.KeepsSignIn) return UpdateAction.Prompt;
        if (!update.Silent) return UpdateAction.Nothing;
        if (failures > 0 && lastFailure is { } at && now - at < BackOff(failures)) return UpdateAction.BackOff;
        return UpdateAction.Install;
    }

    /// <summary>"version|failures|unix seconds" as kept in the preferences while an install may not have finished.</summary>
    public static string Encode(string version, int failures, DateTimeOffset at) => $"{version}|{failures}|{at.ToUnixTimeSeconds()}";

    /// <summary>
    /// The failures that still count at start-up. An attempt for a version that is not the running one (or a
    /// newer one) did not finish: the installer failed after the app had quit. An attempt for the running version
    /// or an older one finished: nothing counts.
    /// </summary>
    public static (int Failures, DateTimeOffset? At) Restore(string? stored, string running)
    {
        var parts = stored?.Split('|');
        if (parts is not { Length: 3 } || !int.TryParse(parts[1], out var failures) || !long.TryParse(parts[2], out var seconds) || failures <= 0)
            return (0, null);
        if (Compare(parts[0], running) <= 0) return (0, null);
        return (failures, DateTimeOffset.FromUnixTimeSeconds(seconds));
    }

    private static int Compare(string a, string b)
    {
        static int[] Parts(string value) => [.. value.Split('.').Select(part => int.TryParse(new string(part.TakeWhile(char.IsDigit).ToArray()), out var n) ? n : 0)];
        var (x, y) = (Parts(a), Parts(b));
        for (var index = 0; index < Math.Max(x.Length, y.Length); index++)
        {
            var delta = (index < x.Length ? x[index] : 0) - (index < y.Length ? y[index] : 0);
            if (delta != 0) return delta;
        }
        return 0;
    }
}
