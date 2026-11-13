using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;

namespace MixelRemote.Support;

/// <summary>Base of the view models: raises PropertyChanged for each changed property.</summary>
public abstract class Observable : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    protected bool Set<TValue>(ref TValue field, TValue value, [CallerMemberName] string name = "")
    {
        if (EqualityComparer<TValue>.Default.Equals(field, value)) return false;
        field = value;
        Raise(name);
        return true;
    }

    /// <summary>Lists and records with lists: compared by their JSON, so a re-read equal list is no change.</summary>
    protected bool SetList<TValue>(ref TValue field, TValue value, [CallerMemberName] string name = "")
    {
        if (JsonSerializer.Serialize(field) == JsonSerializer.Serialize(value)) return false;
        field = value;
        Raise(name);
        return true;
    }

    protected void Raise([CallerMemberName] string name = "") => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
}

/// <summary>Small settings the app remembers on this computer (the Apple apps keep them in UserDefaults).</summary>
public interface IPreferences
{
    string? Get(string key);
    void Set(string key, string? value);
}

/// <summary>Preferences in memory (tests, demo mode).</summary>
public sealed class MemoryPreferences : IPreferences
{
    private readonly Dictionary<string, string> values = [];
    public string? Get(string key) => values.GetValueOrDefault(key);
    public void Set(string key, string? value)
    {
        if (value is null) values.Remove(key);
        else values[key] = value;
    }
}

/// <summary>Preferences in a JSON file in the app's own folder (written atomically).</summary>
public sealed class FilePreferences(string path) : IPreferences
{
    private readonly Lock gate = new();
    private Dictionary<string, string>? values;

    private Dictionary<string, string> Load()
    {
        if (values is not null) return values;
        try { values = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(path)) ?? []; }
        catch (Exception error) when (error is IOException or JsonException or UnauthorizedAccessException) { values = []; }
        return values;
    }

    public string? Get(string key)
    {
        lock (gate) { return Load().GetValueOrDefault(key); }
    }

    public void Set(string key, string? value)
    {
        lock (gate)
        {
            var all = Load();
            if (value is null) all.Remove(key);
            else all[key] = value;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                var temporary = path + ".tmp";
                File.WriteAllText(temporary, JsonSerializer.Serialize(all));
                File.Move(temporary, path, overwrite: true);
            }
            catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
        }
    }
}
