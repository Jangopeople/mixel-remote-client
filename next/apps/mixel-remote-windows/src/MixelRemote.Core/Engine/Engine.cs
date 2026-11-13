using System.Text.Json;
using System.Text.Json.Nodes;
using System.Text.Json.Serialization;

namespace MixelRemote.Engine;

/// <summary>
/// What the app needs from the Mixel Remote engine. The real engine (<see cref="NativeEngine"/>, Rust in
/// mixel_core.dll) and the test engine (<see cref="MockEngine"/>) both provide it, so every screen and view
/// model runs against either. Commands use the desktop app's names and arguments (remote_status,
/// saas_members, …); arguments and results are JSON. The picture and input have their own calls.
/// </summary>
public interface IEngine
{
    EngineInfo Info { get; }
    /// <summary>Runs one command; <paramref name="args"/> is a JSON object. Throws <see cref="EngineFailure"/>.</summary>
    Task<string> CallAsync(string command, string args);
    /// <summary>The next session picture ("MRF2" RGBA or "MRH1" H.264); empty when none is waiting.</summary>
    Task<byte[]> NextVideoAsync(string session, bool compressed);
    /// <summary>One input event (JSON as the desktop app sends it). false: the computer could not apply it.</summary>
    Task<bool> InputAsync(string session, string json);
    Task ShutdownAsync();
}

public sealed record EngineInfo(string Version, string Platform, bool ControllerOnly);

/// <summary>A command that failed; the message comes from the engine (English) and is shown translated.</summary>
public sealed class EngineFailure : Exception
{
    public EngineFailure(string message) : base(message) { }
    public EngineFailure() : base("The engine failed") { }
    public EngineFailure(string message, Exception inner) : base(message, inner) { }
    /// <summary>The message in the shown language.</summary>
    public string Translated => Support.Translator.Shared.Translate(Message);
}

/// <summary>JSON as the engine writes it: snake_case keys.</summary>
public static class Json
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        DictionaryKeyPolicy = null,
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        NumberHandling = JsonNumberHandling.Strict,
    };

    public static string Encode<T>(T value) => JsonSerializer.Serialize(value, Options);

    public static T? Decode<T>(string json) => JsonSerializer.Deserialize<T>(json, Options);

    /// <summary>Arguments from name/value pairs: <c>Args(("session", id), ("push", true))</c>.</summary>
    public static string Args(params (string Name, object? Value)[] values)
    {
        var node = new JsonObject();
        foreach (var (name, value) in values)
        {
            node[name] = value is null ? null : JsonSerializer.SerializeToNode(value, value.GetType(), Options);
        }
        return node.ToJsonString();
    }
}

public static class EngineExtensions
{
    /// <summary>Runs a command and decodes its result.</summary>
    public static async Task<T> RunAsync<T>(this IEngine engine, string command, string args = "{}")
    {
        var reply = await engine.CallAsync(command, args);
        try
        {
            var value = Json.Decode<T>(reply);
            // A list the engine has no rows for reads as an empty list, never as null.
            if (value is null && typeof(T).IsGenericType && typeof(T).GetGenericTypeDefinition() == typeof(List<>)) return (T)Activator.CreateInstance(typeof(T))!;
            return value!;
        }
        catch (JsonException)
        {
            throw new EngineFailure($"The app could not read the engine's answer to {command}");
        }
    }

    public static Task<T> RunAsync<T>(this IEngine engine, string command, params (string Name, object? Value)[] args) =>
        engine.RunAsync<T>(command, Json.Args(args));

    /// <summary>Runs a command whose result is not needed.</summary>
    public static Task PerformAsync(this IEngine engine, string command, params (string Name, object? Value)[] args) =>
        engine.CallAsync(command, Json.Args(args));

    public static Task<bool> SendAsync(this IEngine engine, Models.InputEvent input, string session) =>
        engine.InputAsync(session, input.ToJson());
}
