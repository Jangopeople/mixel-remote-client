using System.Text.Json.Nodes;

namespace MixelRemote.Models;

// What the engine reports, decoded from its JSON (snake_case keys become PascalCase properties).
// Field names and meanings match the Swift models of the Apple apps and the desktop app's window.

/// <summary>The sign-in state (account_status).</summary>
public sealed record AccountStatus
{
    /// <summary>signed_out, starting, waiting_browser, verifying, mfa_required, profile_setup, authenticated, error.</summary>
    public string Phase { get; init; } = "signed_out";
    public string Message { get; init; } = "";
    public Account? Account { get; init; }
    public List<Factor> Factors { get; init; } = [];

    public bool SignedIn => Phase == "authenticated" && Account is not null;
    public bool SigningIn => Phase is "starting" or "waiting_browser" or "verifying";
}

public sealed record Account
{
    public bool Remembered { get; init; }
    public bool HasCompany { get; init; }
    public bool HasPersonal { get; init; }
    public string Profile { get; init; } = "";
    public string? Organization { get; init; }
    public bool Staff { get; init; }
    public bool Personal { get; init; }
    public string? DisplayName { get; init; }
    public List<CompanyAdministration> Companies { get; init; } = [];
}

public sealed record CompanyAdministration
{
    public string CompanyId { get; init; } = "";
    public string CompanyName { get; init; } = "";
}

public sealed record Factor
{
    public string Id { get; init; } = "";
    public string FactorType { get; init; } = "totp";
    public string Status { get; init; } = "verified";
    public string? FriendlyName { get; init; }
}

/// <summary>The connection to Mixel and the session (remote_status).</summary>
public sealed record RemoteStatus
{
    public bool ControllerOnly { get; init; }
    public string Platform { get; init; } = "windows";
    /// <summary>offline, connecting, ready, outgoing, incoming, deciding, negotiating, active, closing, error.</summary>
    public string Phase { get; init; } = "offline";
    public string Message { get; init; } = "";
    public uint? Host { get; init; }
    public string? Session { get; init; }
    public string? Consent { get; init; }
    public uint? Peer { get; init; }
    public string? Requester { get; init; }
    public ulong Received { get; init; }
    public bool Sharing { get; init; }
    public bool Control { get; init; }
    public bool Clipboard { get; init; }
    public bool Files { get; init; }
    public bool RecordingPermitted { get; init; }
    public RecordingView? Recording { get; init; }
    public RecordingView? PreviousRecording { get; init; }
    public bool InputAccess { get; init; } = true;
    public bool UnattendedEnabled { get; init; }
    /// <summary>enabling, disabling, … while the connection service confirms a change.</summary>
    public string? UnattendedPending { get; init; }
    public bool UnattendedSession { get; init; }
    public PairingPrompt? Pairing { get; init; }
    public string? PairingMessage { get; init; }
    public bool CodeRequired { get; init; }
    public string? SupportCode { get; init; }
    public List<uint> Online { get; init; } = [];
    public ushort Displays { get; init; }
    public ushort Display { get; init; }
    public bool DisplayAll { get; init; }
    public List<string> PrintJobs { get; init; } = [];
    public List<string> HostActions { get; init; } = [];
    public uint? SigninId { get; init; }
    public List<string> HostMacs { get; init; } = [];
    /// <summary>The technician offers to show their screen in this meeting room: [session, room].</summary>
    public List<string>? MeetingOffer { get; init; }
    public List<Colleague> Colleagues { get; init; } = [];
    public JoinRequest? JoinRequest { get; init; }
    public List<Invitation> Invitations { get; init; } = [];
    public string? InviteMessage { get; init; }
    public bool JoinedSession { get; init; }
    public bool Switched { get; init; }
    public List<string> PeerFeatures { get; init; } = [];

    public static readonly RemoteStatus Offline = new();

    /// <summary>Connected to Mixel (this computer can be reached and can connect).</summary>
    public bool Connected => Phase is "ready" or "active" or "incoming" or "outgoing" or "deciding" or "negotiating";
    public bool Disconnected => Phase is "offline" or "error";
    public bool InSession => Phase is "active" or "incoming" or "outgoing" or "deciding" or "negotiating" or "closing";
    /// <summary>This app views and (maybe) controls another computer right now.</summary>
    public bool Viewing => Phase == "active" && !Sharing && Session is not null;
    public bool Offers(string action) => HostActions.Contains(action);
}

public sealed record PairingPrompt
{
    public string RequestId { get; init; } = "";
    public string ProviderName { get; init; } = "";
    public string CustomerName { get; init; } = "";
}

public sealed record Colleague
{
    public string Session { get; init; } = "";
    public uint Connection { get; init; }
    public bool Control { get; init; }
    public string State { get; init; } = "";
}

public sealed record JoinRequest
{
    public string Session { get; init; } = "";
    public uint Connection { get; init; }
    public uint InviterConnection { get; init; }
    public bool Control { get; init; }
    public bool Unattended { get; init; }
}

public sealed record Invitation
{
    [System.Text.Json.Serialization.JsonPropertyName("invitation")]
    public string Id { get; init; } = "";
    public uint Host { get; init; }
    public string Inviter { get; init; } = "";
    public uint InviterConnection { get; init; }
    public bool Control { get; init; }
    public bool Unattended { get; init; }
    public ulong ExpiresAt { get; init; }
}

/// <summary>Recording state: ready, waiting_for_video, recording, saving, saved, saved_partial, error.</summary>
public sealed record RecordingView
{
    public string Phase { get; init; } = "ready";
    public string? Message { get; init; }
    public bool Running => Phase is "recording" or "waiting_for_video";
}

/// <summary>A screen this computer can share (capture_targets).</summary>
public sealed record CaptureTarget
{
    public string Id { get; init; } = "";
    public string Name { get; init; } = "";
    public bool FullDisplay { get; init; }
}

/// <summary>The host's answer to a connection request (remote_decide).</summary>
public sealed record Decision
{
    public string Session { get; init; } = "";
    public bool Accept { get; init; }
    public string Source { get; init; } = "";
    public bool Confirm { get; init; }
    public bool Control { get; init; }
    public bool Clipboard { get; init; }
    public bool Files { get; init; }
    public bool Chat { get; init; }
    public bool Recording { get; init; }
}

// ---- Devices ----

/// <summary>A device of the account's directory (remote_devices) or of a company (saas_devices).</summary>
public sealed record Device
{
    [System.Text.Json.Serialization.JsonPropertyName("device")]
    public string DeviceId { get; init; } = "";
    public uint ConnectionId { get; init; }
    public string Name { get; init; } = "";
    public string Os { get; init; } = "";
    public string? GroupId { get; init; }
    public string? GroupName { get; init; }
    public ulong? Generation { get; init; }
}

public sealed record DevicePage
{
    public List<Device> Devices { get; init; } = [];
    public uint? Next { get; init; }
}

public sealed record SaasDevice
{
    public string DeviceId { get; init; } = "";
    public string Name { get; init; } = "";
    public string Os { get; init; } = "";
    public string? GroupId { get; init; }
    public string? GroupName { get; init; }
    public uint? ConnectionId { get; init; }
    public ulong? Generation { get; init; }
    public bool? Revoked { get; init; }

    public Device? AsDevice() => ConnectionId is { } id && Revoked != true
        ? new Device { DeviceId = DeviceId, ConnectionId = id, Name = Name, Os = Os, GroupId = GroupId, GroupName = GroupName, Generation = Generation }
        : null;
}

public sealed record SaasPresence
{
    public string DeviceId { get; init; } = "";
    public uint? ConnectionId { get; init; }
    public string Status { get; init; } = "";
    public string? LastSeen { get; init; }
}

public sealed record SaasGroup
{
    public string GroupId { get; init; } = "";
    public string Name { get; init; } = "";
    public int Devices { get; init; }
    public List<string> Technicians { get; init; } = [];
}

// ---- Company ----

public sealed record Tenant
{
    public string TenantId { get; init; } = "";
    public string OrganizationId { get; init; } = "";
    public string Name { get; init; } = "";
    public string Slug { get; init; } = "";
    public string Role { get; init; } = "";
    public string Plan { get; init; } = "";
    public string Status { get; init; } = "";
    public string? TrialEndsAt { get; init; }
}

/// <summary>saas_tenant_overview: the tenant plus features, limits and usage.</summary>
public sealed record TenantOverview
{
    public string? Role { get; init; }
    public string? Status { get; init; }
    public string? Plan { get; init; }
    public string? TrialEndsAt { get; init; }
    public Dictionary<string, bool>? Features { get; init; }
    public OverviewLimits? Limits { get; init; }
    public OverviewUsage? Usage { get; init; }
}

public sealed record OverviewLimits
{
    public int? Devices { get; init; }
    public int? Technicians { get; init; }
}

public sealed record OverviewUsage
{
    public int Devices { get; init; }
    public int Technicians { get; init; }
}

public sealed record Member
{
    public string UserId { get; init; } = "";
    public string Name { get; init; } = "";
    public string Email { get; init; } = "";
    public string Role { get; init; } = "";
    public string CreatedAt { get; init; } = "";
}

public sealed record Invite
{
    public string Id { get; init; } = "";
    public string Email { get; init; } = "";
    public string Role { get; init; } = "";
    public string CreatedAt { get; init; } = "";
    public string ExpiresAt { get; init; } = "";
    public string? MailState { get; init; }
    public string? MailSentAt { get; init; }
}

public sealed record DeployLinkCreated
{
    public string Token { get; init; } = "";
    public string WindowsCommand { get; init; } = "";
    public string MacosCommand { get; init; } = "";
    public string LinuxCommand { get; init; } = "";
}

/// <summary>A Mixel team rollout token for an ISM company (rollout_list).</summary>
public sealed record RolloutToken
{
    public string Id { get; init; } = "";
    public string Label { get; init; } = "";
    public string CreatedAt { get; init; } = "";
    public string ExpiresAt { get; init; } = "";
    public int MaxUses { get; init; }
    public int Uses { get; init; }
    public string? RevokedAt { get; init; }
}

public sealed record SaasEnrollment
{
    public string DeviceId { get; init; } = "";
    public uint ConnectionId { get; init; }
    public ulong Generation { get; init; }
}

public sealed record SignupResult
{
    public string TenantId { get; init; } = "";
    public string Plan { get; init; } = "";
    public string Status { get; init; } = "";
    public string? TrialEndsAt { get; init; }
}

// ---- Session tools ----

public sealed record ChatMessage
{
    public ulong Id { get; init; }
    public bool Mine { get; init; }
    public string Text { get; init; } = "";
}

public sealed record FileView
{
    public bool Connected { get; init; }
    public Transfer? Incoming { get; init; }
    public Transfer? Outgoing { get; init; }
    public string Message { get; init; } = "";
}

public sealed record Transfer
{
    public string Id { get; init; } = "";
    public string Name { get; init; } = "";
    public ulong Size { get; init; }
    public ulong Offset { get; init; }
    public string Phase { get; init; } = "";
    public string Message { get; init; } = "";
    public double Progress => Size == 0 ? 0 : Math.Min(1, (double)Offset / Size);
}

public sealed record MeetingRoom
{
    public string Room { get; init; } = "";
    public string Invite { get; init; } = "";
    public string Host { get; init; } = "";
}

public sealed record PrintingStatus
{
    public bool Supported { get; init; }
    public bool Installed { get; init; }
}

public sealed record StartupSettings
{
    public bool Remembered { get; init; }
    public bool Autostart { get; init; }
    public bool LaunchedAtStartup { get; init; }
    public bool Restored { get; init; }
}

/// <summary>Sign-in screen and always-on access (signin_screen_status).</summary>
public sealed record SignInScreen
{
    public bool Supported { get; init; }
    public bool Outdated { get; init; }
    public bool Technician { get; init; }
    public uint? ConnectionId { get; init; }
    public string? Name { get; init; }
}

public sealed record UpdateInfo
{
    public string Version { get; init; } = "";
    /// <summary>The app can install it itself (signed update channel).</summary>
    public bool Installable { get; init; }
    /// <summary>Nothing would be interrupted and the sign-in survives the restart.</summary>
    public bool Silent { get; init; }
    public bool NeedsPassword { get; init; }
}

public sealed record EnrollmentCompany
{
    public string ClientId { get; init; } = "";
    public string ClientName { get; init; } = "";
}

public sealed record InventoryDevice
{
    public string Id { get; init; } = "";
    public string Name { get; init; } = "";
    public string Os { get; init; } = "";
}

public sealed record InventoryPage
{
    public List<InventoryDevice> Devices { get; init; } = [];
    public string? Next { get; init; }
}

public sealed record CurrentEnrollment
{
    public string Device { get; init; } = "";
    public uint ConnectionId { get; init; }
    public string Name { get; init; } = "";
}

/// <summary>This computer's registration as the app remembers it.</summary>
public sealed record Registration
{
    public string Device { get; init; } = "";
    public uint ConnectionId { get; init; }
    public ulong Generation { get; init; }
    public string? Tenant { get; init; }
}

/// <summary>An input event, encoded as the desktop app sends it ({"kind":"key","usage":4,"down":true}).</summary>
public abstract record InputEvent
{
    public abstract string ToJson();

    public sealed record Text(string Value) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "text", ["text"] = Value }.ToJsonString();
    }

    public sealed record Key(ushort Usage, bool Down) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "key", ["usage"] = Usage, ["down"] = Down }.ToJsonString();
    }

    public sealed record Pointer(ushort X, ushort Y) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "pointer", ["x"] = X, ["y"] = Y }.ToJsonString();
    }

    /// <summary>Buttons as the engine numbers them: 1 left, 2 middle, 3 right, 4 back, 5 forward.</summary>
    public sealed record ButtonClick(byte Button, bool Down, byte Clicks) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "button_click", ["button"] = Button, ["down"] = Down, ["clicks"] = Clicks }.ToJsonString();
    }

    /// <summary>Wheel steps (one per notch); positive vertical scrolls up, positive horizontal right.</summary>
    public sealed record Wheel(short Horizontal, short Vertical) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "wheel", ["horizontal"] = Horizontal, ["vertical"] = Vertical }.ToJsonString();
    }

    public sealed record ReleaseAll : InputEvent
    {
        public override string ToJson() => """{"kind":"release_all"}""";
    }

    public sealed record SelectDisplay(ushort Index) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "select_display", ["index"] = Index }.ToJsonString();
    }

    public sealed record Action(string Name) : InputEvent
    {
        public override string ToJson() => new JsonObject { ["kind"] = "action", ["name"] = Name }.ToJsonString();
    }
}

/// <summary>The engine's answer to removing this computer: "removed", "queued" or "stale".</summary>
public enum SelfRemovalStatus { Removed, Queued, Stale }

public static class SelfRemoval
{
    public static SelfRemovalStatus? Parse(string? value) => value switch
    {
        "removed" => SelfRemovalStatus.Removed,
        "queued" => SelfRemovalStatus.Queued,
        "stale" => SelfRemovalStatus.Stale,
        _ => null,
    };
}
