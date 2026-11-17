using System.Diagnostics;

namespace MixelRemote.App.Shell;

/// <summary>UI-thread diagnostics: time spent per kind of work, logged once per second when the UI thread is busy (a hot UI thread freezes the window).</summary>
public static class Perf
{
    private static readonly Dictionary<string, (int Count, double Ms)> totals = [];
    private static long window;

    public static void Time(string name, Action work)
    {
        var watch = Stopwatch.StartNew();
        try { work(); }
        finally { Add(name, watch.Elapsed.TotalMilliseconds); }
    }

    private static void Add(string name, double ms)
    {
        var second = Environment.TickCount64 / 1000;
        if (second != window)
        {
            var sum = totals.Values.Sum(item => item.Ms);
            if (sum > 300) MixelRemote.App.App.Log("ui busy: " + string.Join(", ", totals.Select(item => $"{item.Key} x{item.Value.Count} {item.Value.Ms:0}ms")));
            totals.Clear();
            window = second;
        }
        totals[name] = totals.TryGetValue(name, out var found) ? (found.Count + 1, found.Ms + ms) : (1, ms);
    }
}
