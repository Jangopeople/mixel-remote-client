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

    // Lab builds only (LAB): the UI side of "QA connect" marks (same clock as the engine's, on stderr) and per-picture stage times.
    private static double copyMs, drawMs, presentMs;
    private static int pictures;
    private static long labSecond;

    [Conditional("LAB")]
    public static void LabMark(string step)
    {
        var clock = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() % 100_000_000;
        Console.Error.WriteLine($"QA connect UI {step} (clock {clock})");
    }

    /// <summary>One picture's UI cost: bitmap copy, drawing into the swap chain, present. Once a second, written to MIXEL_QA_VIDEO_LOG.draw.</summary>
    [Conditional("LAB")]
    public static void LabPicture(double copy, double draw, double present)
    {
        (copyMs, drawMs, presentMs, pictures) = (copyMs + copy, drawMs + draw, presentMs + present, pictures + 1);
        var second = Environment.TickCount64 / 1000;
        if (second == labSecond) return;
        labSecond = second;
        if (Environment.GetEnvironmentVariable("MIXEL_QA_VIDEO_LOG") is { Length: > 0 } path && pictures > 0)
        {
            try { File.WriteAllText(path + ".draw", $"drawn/s={pictures} copy={copyMs / pictures:0.0}ms draw={drawMs / pictures:0.0}ms present={presentMs / pictures:0.0}ms"); }
            catch (IOException) { }
        }
        (copyMs, drawMs, presentMs, pictures) = (0, 0, 0, 0);
    }
}
