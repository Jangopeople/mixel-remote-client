using System.Text.Json.Nodes;

namespace MixelRemote.Engine;

/// <summary>
/// A scripted engine for tests, the screenshots of every screen and the demo mode. Each command answers
/// with the JSON set for it (or null); every call is recorded.
/// </summary>
public sealed class MockEngine : IEngine
{
    public sealed record Call(string Command, string Args)
    {
        /// <summary>The arguments as a JSON object (for checks in tests).</summary>
        public JsonObject Object => JsonNode.Parse(Args) as JsonObject ?? [];
    }

    private readonly Lock gate = new();
    private readonly Dictionary<string, (string? Json, string? Failure)> replies = [];
    private readonly Dictionary<string, Func<string, string>> handlers = [];
    private readonly List<Call> recorded = [];
    private readonly Queue<byte[]> frames = new();
    private readonly List<string> inputs = [];

    public MockEngine(string platform = "windows", bool controllerOnly = false)
    {
        Info = new EngineInfo("2.0.0", platform, controllerOnly);
    }

    public EngineInfo Info { get; }

    /// <summary>Answer <paramref name="command"/> with this JSON.</summary>
    public void Reply(string command, string json)
    {
        lock (gate) { replies[command] = (json, null); }
    }

    /// <summary>Answer with this value, encoded as the engine would (snake_case keys).</summary>
    public void ReplyValue<T>(string command, T value) => Reply(command, Json.Encode(value));

    public void Fail(string command, string message)
    {
        lock (gate) { replies[command] = (null, message); }
    }

    /// <summary>A reply computed from the arguments; throw <see cref="EngineFailure"/> to fail.</summary>
    public void Handle(string command, Func<string, string> handler)
    {
        lock (gate) { handlers[command] = handler; }
    }

    public void QueueFrame(byte[] frame)
    {
        lock (gate) { frames.Enqueue(frame); }
    }

    public IReadOnlyList<Call> Calls
    {
        get { lock (gate) { return [.. recorded]; } }
    }

    public IReadOnlyList<Call> CallsOf(string command) => [.. Calls.Where(call => call.Command == command)];

    public IReadOnlyList<string> SentInput
    {
        get { lock (gate) { return [.. inputs]; } }
    }

    public Task<string> CallAsync(string command, string args)
    {
        (string? Json, string? Failure) reply;
        Func<string, string>? handler;
        lock (gate)
        {
            recorded.Add(new Call(command, args));
            replies.TryGetValue(command, out reply);
            handlers.TryGetValue(command, out handler);
        }
        if (handler is not null)
        {
            try { return Task.FromResult(handler(args)); }
            catch (EngineFailure failure) { return Task.FromException<string>(failure); }
        }
        if (reply.Failure is not null) return Task.FromException<string>(new EngineFailure(reply.Failure));
        return Task.FromResult(reply.Json ?? "null");
    }

    public Task<byte[]> NextVideoAsync(string session, bool compressed)
    {
        lock (gate) { return Task.FromResult(frames.Count > 0 ? frames.Dequeue() : []); }
    }

    public Task<bool> InputAsync(string session, string json)
    {
        lock (gate) { inputs.Add(json); }
        return Task.FromResult(true);
    }

    public Task ShutdownAsync()
    {
        lock (gate) { recorded.Add(new Call("shutdown", "{}")); }
        return Task.CompletedTask;
    }
}
