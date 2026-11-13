using System.Text.Json;
using System.Text.RegularExpressions;
using MixelRemote.Engine;
using MixelRemote.Support;
using static MixelRemote.Support.T;

namespace MixelRemote.ViewModels;

/// <summary>
/// Help during a session, as in the 0.1 app: the company's own AI server explains the remote screen and
/// suggests steps, writes the service-desk note, and the note is saved as an internal note of an ISM ticket.
/// </summary>
public sealed partial class AssistantModel : Observable
{
    private const string Key = "mixel.remote.ai.v1";
    private readonly AppModel app;
    private string address = "";
    private string model = "";
    private List<string> models = [];
    private bool connected;
    private string status = "";
    private string answer = "";
    private string question = "";
    private string ticket = "";
    private string ticketStatus = "";
    private bool busy;
    private readonly List<string> log = [];
    private string? logSession;
    private DateTimeOffset started = DateTimeOffset.UtcNow;

    internal AssistantModel(AppModel app) => this.app = app;

    public string Address { get => address; set => Set(ref address, value); }
    public string Model { get => model; set => Set(ref model, value); }
    public List<string> Models { get => models; private set => SetList(ref models, value); }
    public bool Connected { get => connected; private set => Set(ref connected, value); }
    public string Status { get => status; private set => Set(ref status, value); }
    /// <summary>The answer or the note (editable before it is saved in a ticket).</summary>
    public string Answer { get => answer; set => Set(ref answer, value); }
    public string Question { get => question; set => Set(ref question, value); }
    public string Ticket { get => ticket; set => Set(ref ticket, value); }
    public string TicketStatus { get => ticketStatus; private set => Set(ref ticketStatus, value); }
    public bool Busy { get => busy; private set => Set(ref busy, value); }
    /// <summary>The picture the technician sees, as base64 JPEG (at most 1280 wide), from the session window.</summary>
    public Func<string?> Screenshot { get; set; } = () => null;

    private static string LanguageName => Translator.Shared.Language switch
    {
        "de" => "German",
        "fr" => "French",
        "it" => "Italian",
        _ => "English",
    };

    /// <summary>Finds the AI server (the saved one, this computer or the usual company server).</summary>
    public async Task Connect(bool announce)
    {
        if (!announce && app.Preferences.Get(Key) is { } saved)
        {
            try
            {
                using var document = JsonDocument.Parse(saved);
                Address = document.RootElement.TryGetProperty("address", out var a) ? a.GetString() ?? "" : "";
                Model = document.RootElement.TryGetProperty("model", out var m) ? m.GetString() ?? "" : "";
            }
            catch (JsonException) { }
        }
        if (announce) Status = L("Connecting…");
        try
        {
            using var server = JsonDocument.Parse(await app.Engine.CallAsync("ai_connect", Json.Args(("address", Address.Trim()), ("model", Model))));
            Address = server.RootElement.GetProperty("address").GetString() ?? "";
            Model = server.RootElement.GetProperty("model").GetString() ?? "";
            Models = [.. server.RootElement.GetProperty("models").EnumerateArray().Select(item => item.GetString() ?? "").Where(item => item.Length > 0)];
            Connected = true;
            app.Preferences.Set(Key, JsonSerializer.Serialize(new Dictionary<string, string> { ["address"] = Address, ["model"] = Model }));
            if (announce) Status = L("Connected to your AI server.");
        }
        catch (EngineFailure failure)
        {
            Connected = false;
            if (announce) Status = failure.Translated;
        }
    }

    private void Track()
    {
        if (app.Remote.Session != logSession)
        {
            logSession = app.Remote.Session;
            log.Clear();
            started = DateTimeOffset.UtcNow;
        }
    }

    public async Task Ask()
    {
        if (!Connected || Busy) return;
        Track();
        Busy = true;
        Answer = L("Looking at the screen…");
        var typed = Question.Trim();
        try
        {
            var prompt = typed.Length > 0 ? typed : "Explain briefly what is shown on this screen, name any error or warning you can read, and suggest the next two or three steps for the technician.";
            var system = $"You are an IT support assistant helping a technician during a remote support session. The image is the remote computer's screen (a Mixel Remote window on it only means the session is running; ignore it). Be concrete and brief. No Markdown. Quote on-screen text exactly when you rely on it; say so if you cannot read something. Answer in {LanguageName}.";
            var reply = Plain(await app.Engine.RunAsync<string>("ai_ask", ("address", Address), ("model", Model), ("system", system), ("prompt", prompt), ("image", Screenshot())));
            log.Add($"Question: {(typed.Length > 0 ? typed : "(explain the screen)")}\nAssistant: {reply}");
            if (log.Count > 12) log.RemoveAt(0);
            Answer = reply;
        }
        catch (EngineFailure failure) { Answer = failure.Translated; }
        finally { Busy = false; }
    }

    /// <summary>The service-desk note of this session (from the exchanges and the last picture).</summary>
    public async Task WriteNote()
    {
        if (!Connected || Busy) return;
        Track();
        Busy = true;
        Answer = L("Writing the note…");
        var typed = Question.Trim();
        try
        {
            var minutes = Math.Max(1, (int)Math.Round((DateTimeOffset.UtcNow - started).TotalMinutes));
            var system = $"You write short, factual service-desk notes for an IT provider. Use only the facts given and what the final screenshot shows; never invent work. Format: one summary line, then three short sections for what was done, what is still open, and the time spent, with the section labels in the answer language. Write \"–\" for an empty section. No Markdown. Answer in {LanguageName}.";
            var prompt = $"Remote support session with computer ID {app.Remote.Peer?.ToString(System.Globalization.CultureInfo.InvariantCulture) ?? "unknown"}, about {minutes} minutes so far.\nTechnician's keywords: {(typed.Length > 0 ? typed : "(none)")}\nAssistant exchanges during the session:\n{(log.Count > 0 ? string.Join("\n---\n", log) : "(none)")}\nWrite the ticket note.";
            Answer = Plain(await app.Engine.RunAsync<string>("ai_ask", ("address", Address), ("model", Model), ("system", system), ("prompt", prompt), ("image", Screenshot())));
        }
        catch (EngineFailure failure) { Answer = failure.Translated; }
        finally { Busy = false; }
    }

    /// <summary>Saves the note as an internal note of an ISM ticket (Mixel team accounts).</summary>
    public async Task SaveInTicket()
    {
        var number = Ticket.Trim();
        var note = Answer.Trim();
        if (number.Length < 3)
        {
            TicketStatus = L("Enter the ticket number.");
            return;
        }
        if (note.Length == 0)
        {
            TicketStatus = L("Write the note first, or let the AI write it.");
            return;
        }
        TicketStatus = L("Saving…");
        try
        {
            var saved = await app.Engine.RunAsync<string>("ticket_note", ("ticket", number), ("note", note.Length > 7800 ? note[..7800] : note), ("remote_id", app.Remote.Peer));
            TicketStatus = L("Saved as internal note in %@", saved);
        }
        catch (EngineFailure failure) { TicketStatus = failure.Translated; }
    }

    [GeneratedRegex(@"\*\*(.+?)\*\*")]
    private static partial Regex Bold();
    [GeneratedRegex(@"^#{1,4}\s*", RegexOptions.Multiline)]
    private static partial Regex Heading();
    [GeneratedRegex(@"^\s*\*\s+", RegexOptions.Multiline)]
    private static partial Regex Bullet();

    /// <summary>Plain text for display: no Markdown emphasis or heading marks.</summary>
    public static string Plain(string text) => Bullet().Replace(Heading().Replace(Bold().Replace(text, "$1"), ""), "• ");
}
