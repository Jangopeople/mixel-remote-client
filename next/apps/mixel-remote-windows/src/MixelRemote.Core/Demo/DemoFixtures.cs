using MixelRemote.Engine;
using MixelRemote.Models;
using MixelRemote.Support;
using MixelRemote.ViewModels;

namespace MixelRemote.Demo;

/// <summary>Scripted engine states for the view-model tests and the screenshots of every screen.</summary>
public static class DemoFixtures
{
    public const string Profile = "11111111-2222-4333-8444-555555555555";
    public const string TenantId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
    public const string SessionId = "0f0e0d0c-0b0a-4908-8706-050403020100";
    public const string ConsentId = "c0c0c0c0-0000-4000-8000-000000000001";
    public const uint ThisComputer = 482_913_557;

    /// <summary>Every screen the screenshot run renders.</summary>
    public static readonly string[] Screens =
    [
        "signed-out", "second-step", "remote", "setup", "devices", "devices-manage", "deploy-link", "account", "invite",
        "settings", "consent", "hosted", "session", "session-chat", "session-files", "phone-help", "tray",
        "connecting", "ready-notice", "session-tab", "popout", "sessions", "reports",
    ];

    public static void SignedOut(MockEngine engine)
    {
        engine.ReplyValue("account_status", new AccountStatus { Phase = "signed_out", Message = "Sign in with your Mixel account" });
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "offline" });
        engine.ReplyValue("startup_settings", new StartupSettings());
        engine.Reply("device_removal_pending", "false");
    }

    public static void Mfa(MockEngine engine)
    {
        SignedOut(engine);
        engine.ReplyValue("account_status", new AccountStatus
        {
            Phase = "mfa_required",
            Message = "Additional verification is required for this Mixel account",
            Factors = [new Factor { Id = "f0000000-0000-4000-8000-000000000001", FriendlyName = "Mixel ISM staff" }],
        });
    }

    public static void Company(MockEngine engine, string phase = "ready", string role = "owner", bool staff = false)
    {
        engine.ReplyValue("account_status", new AccountStatus
        {
            Phase = "authenticated",
            Message = "Signed in",
            Account = new Account { Remembered = true, HasCompany = true, Profile = Profile, Organization = TenantId, DisplayName = "Anna Muster", Staff = staff },
        });
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = phase, Host = ThisComputer, Online = [731_204_118, 268_455_901] });
        engine.ReplyValue("saas_my_tenants", new List<Tenant>
        {
            new() { TenantId = TenantId, OrganizationId = TenantId, Name = "Muster Treuhand AG", Slug = "muster", Role = role, Plan = "business", Status = "trial", TrialEndsAt = "2026-11-27T10:00:00Z" },
        });
        engine.Reply("saas_tenant_overview", $$$"""
            {"role":"{{{role}}}","status":"trial","plan":"business","trial_ends_at":"2026-11-27T10:00:00Z","features":{"groups":true,"reports":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}
            """);
        engine.Reply("saas_members", $$"""
            [{"user_id":"{{Profile}}","name":"Anna Muster","email":"anna@muster.test","role":"owner","created_at":"2026-11-01T08:00:00Z"},
             {"user_id":"22222222-2222-4222-8222-222222222222","name":"Marco Rossi","email":"marco@muster.test","role":"technician","created_at":"2026-11-02T08:00:00Z"}]
            """);
        engine.Reply("saas_invites", """
            [{"id":"33333333-3333-4333-8333-333333333333","email":"lea@muster.test","role":"technician","created_at":"2026-11-12T08:00:00Z","expires_at":"2026-11-19T08:00:00Z","mail_state":"delivered"}]
            """);
        engine.Reply("saas_devices", """
            [{"device_id":"d1000000-0000-4000-8000-000000000001","name":"Reception PC","os":"windows","group_id":"g1000000-0000-4000-8000-000000000001","group_name":"Front office","connection_id":731204118,"generation":3,"revoked":false},
             {"device_id":"d1000000-0000-4000-8000-000000000002","name":"Accounting Mac","os":"macos","group_id":null,"group_name":null,"connection_id":268455901,"generation":1,"revoked":false},
             {"device_id":"d1000000-0000-4000-8000-000000000003","name":"Warehouse laptop","os":"linux","group_id":null,"group_name":null,"connection_id":519003442,"generation":2,"revoked":false},
             {"device_id":"d1000000-0000-4000-8000-000000000004","name":"This PC","os":"windows","group_id":null,"group_name":null,"connection_id":482913557,"generation":1,"revoked":false}]
            """);
        engine.Reply("saas_groups", """[{"group_id":"g1000000-0000-4000-8000-000000000001","name":"Front office","devices":1,"technicians":[]}]""");
        var seen = DateTimeOffset.UtcNow.AddHours(-3).ToString("o");
        engine.Reply("saas_device_presence", $$"""[{"device_id":"d1000000-0000-4000-8000-000000000003","connection_id":519003442,"status":"offline","last_seen":"{{seen}}"}]""");
        engine.ReplyValue("startup_settings", new StartupSettings { Remembered = true, Restored = true });
        engine.ReplyValue("remote_printing_status", new PrintingStatus { Supported = true, Installed = false });
        engine.ReplyValue("signin_screen_status", new SignInScreen { Supported = true });
        engine.Reply("screen_access", "true");
        engine.Reply("device_removal_pending", "false");
        engine.Reply("this_computer_name", "\"ANNA-PC\"");
        engine.ReplyValue("capture_targets", new List<CaptureTarget> { new() { Id = "dxgi:0:0:\\\\.\\DISPLAY1", Name = "\\\\.\\DISPLAY1 · 2560 × 1440", FullDisplay = true } });
        engine.Reply("quick_phone_claim", "false");
        engine.Reply("saas_favorites", """[{"device_id":"d1000000-0000-4000-8000-000000000002","created_at":"2026-11-10T08:00:00Z"}]""");
        engine.Reply("saas_recent_devices", """[{"device_id":"d1000000-0000-4000-8000-000000000001","last_connected_at":"2026-11-13T09:12:00Z"},{"device_id":"d1000000-0000-4000-8000-000000000002","last_connected_at":"2026-11-12T15:40:00Z"}]""");
        engine.Reply("saas_session_report", """
            [{"session_id":"e1000000-0000-4000-8000-000000000001","technician_id":"22222222-2222-4222-8222-222222222222","technician_name":"Marco Rossi","technician_email":"marco@muster.test","device_id":"d1000000-0000-4000-8000-000000000001","device_name":"Reception PC","connection_id":731204118,"started_at":"2026-11-13T09:12:00Z","ended_at":"2026-11-13T09:31:00Z","duration_seconds":1140,"kind":"attended","colleague":false,"note":"Printer driver reinstalled, test page OK."},
             {"session_id":"e1000000-0000-4000-8000-000000000002","technician_id":"11111111-2222-4333-8444-555555555555","technician_name":"Anna Muster","technician_email":"anna@muster.test","device_id":"d1000000-0000-4000-8000-000000000002","device_name":"Accounting Mac","connection_id":268455901,"started_at":"2026-11-12T15:40:00Z","ended_at":"2026-11-12T15:52:00Z","duration_seconds":720,"kind":"unattended","colleague":false,"note":null}]
            """);
        engine.Reply("remote_draw", "true");
        // Device administration (contract of docs/mixel-remote-device-admin-api.md, 2.1): badges, versions, notes.
        engine.Reply("remote_admin_devices", $$"""
            [{"device_id":"d1000000-0000-4000-8000-000000000001","connection_id":731204118,"name":"Reception PC","os":"windows","group_name":"Front office","note":"Desk 3, docked","favorite":false,"unattended_setting":"on","unattended_default":false,"unattended_allowed":true,"status":"online","last_seen":"{{DateTimeOffset.UtcNow:o}}","app_version":"2.0.18","can_admin":true,"updated_at":"2026-11-17T08:00:00Z"},
             {"device_id":"d1000000-0000-4000-8000-000000000002","connection_id":268455901,"name":"Accounting Mac","os":"macos","note":null,"favorite":true,"unattended_setting":"default","unattended_default":false,"unattended_allowed":true,"status":"online","app_version":"2.0.18","can_admin":true,"updated_at":"2026-11-17T08:00:00Z"},
             {"device_id":"d1000000-0000-4000-8000-000000000003","connection_id":519003442,"name":"Warehouse laptop","os":"linux","note":null,"favorite":false,"unattended_setting":"off","unattended_default":false,"unattended_allowed":false,"status":"offline","app_version":"2.0.15","can_admin":true,"updated_at":"2026-11-17T08:00:00Z"},
             {"device_id":"d1000000-0000-4000-8000-000000000004","connection_id":482913557,"name":"This PC","os":"windows","note":null,"favorite":false,"unattended_setting":"default","unattended_default":true,"unattended_allowed":true,"status":"online","app_version":"2.0.18","can_admin":true,"updated_at":"2026-11-17T08:00:00Z"}]
            """);
    }

    public static void Incoming(MockEngine engine)
    {
        Company(engine);
        engine.ReplyValue("remote_status", new RemoteStatus { Phase = "incoming", Host = ThisComputer, Session = SessionId, Consent = ConsentId, Peer = 731_204_118 });
    }

    public static void Viewing(MockEngine engine, List<string>? actions = null)
    {
        Company(engine);
        engine.ReplyValue("remote_status", new RemoteStatus
        {
            Phase = "active", Host = ThisComputer, Session = SessionId, Consent = ConsentId, Peer = 731_204_118, Received = 12,
            Control = true, Clipboard = true, Files = true, Recording = new RecordingView { Phase = "ready" }, Displays = 2,
            HostActions = actions ?? ["privacy", "block", "pointer", "lock", "lockend", "sas", "restart", "sound", "meet"],
            PeerFeatures = ["join", "switch", "draw"], PrintJobs = [@"C:\Users\anna\AppData\Local\ch.mixel.remote.next.qa\print\Remote print.pdf"],
        });
        engine.ReplyValue("remote_chat_messages", new List<ChatMessage>
        {
            new() { Id = 1, Mine = false, Text = "Hello, the printer shows an error." },
            new() { Id = 2, Mine = true, Text = "I see it, one moment." },
        });
        engine.Reply("remote_file_status", """
            {"connected":true,"incoming":{"id":"f2000000-0000-4000-8000-000000000002","name":"scan-0412.pdf","size":2400000,"offset":0,"phase":"offered","message":""},"outgoing":{"id":"f1000000-0000-4000-8000-000000000001","name":"driver-setup.exe","size":48000000,"offset":31000000,"phase":"transferring","message":""},"message":""}
            """);
        engine.Reply("remote_record_may_start", "true");
    }

    /// <summary>Remote IDs of the computers of <see cref="Multi"/>.</summary>
    public static readonly uint[] Peers = [731_204_118, 268_455_901, 519_003_442, 604_118_273, 377_905_620];

    /// <summary>The stable id of session <paramref name="index"/> (0 based) of <see cref="Multi"/>.</summary>
    public static string StableId(int index) => $"5e55{index:0000}-0000-4000-8000-00000000000{index}";

    /// <summary>
    /// The status of an engine that runs several technician sessions (engine 2.1): <paramref name="count"/> sessions, all active,
    /// to <see cref="Peers"/>, each with its own stable id, and the single-session fields of the oldest one.
    /// </summary>
    public static RemoteStatus Multi(int count, string phase = "active", int limit = 4)
    {
        var sessions = Enumerable.Range(0, count).Select(index => new SessionStatus
        {
            Id = StableId(index), Session = $"7a00{index:0000}-0000-4000-8000-000000000000", Peer = Peers[index], Device = Peers[index], Phase = phase,
            Consent = $"c0c0c0c0-0000-4000-8000-00000000000{index}", Control = true, Clipboard = true, Files = true,
            Recording = new RecordingView { Phase = "ready" }, Displays = 1,
            HostActions = ["privacy", "block", "pointer", "lock", "lockend", "sas", "restart", "sound"], PeerFeatures = ["join", "switch", "draw"],
            Quality = new SessionQuality { Pictures = 120, PicturesPerSecond = 24, PictureAgeMs = 30 }, StartedAt = 1_800_000_000UL + (ulong)index,
        }).ToList();
        var oldest = sessions.Count > 0 ? sessions[0] : null;
        return new RemoteStatus
        {
            Phase = oldest?.Phase ?? "ready", Host = ThisComputer, Online = [.. Peers], Sessions = sessions, MaxSessions = limit,
            Session = oldest?.Session, Peer = oldest?.Peer, Consent = oldest?.Consent, Control = oldest?.Control ?? false,
        };
    }

    /// <summary>An engine with <paramref name="count"/> sessions running (<see cref="Multi"/>).</summary>
    public static void Viewing(MockEngine engine, int count)
    {
        Viewing(engine);
        engine.ReplyValue("remote_status", Multi(count));
    }

    public static void Hosted(MockEngine engine)
    {
        Company(engine);
        engine.ReplyValue("remote_status", new RemoteStatus
        {
            Phase = "active", Host = ThisComputer, Session = SessionId, Consent = ConsentId, Peer = 731_204_118, Sharing = true, Control = true,
            RecordingPermitted = true, Message = "Sharing",
            Colleagues = [new Colleague { Session = "c1", Connection = 268_455_901, Control = false, State = "active" }],
            JoinRequest = new JoinRequest { Session = "c2", Connection = 519_003_442, InviterConnection = 731_204_118, Control = true },
            PeerFeatures = ["switch"],
        });
    }

    /// <summary>An app model with this engine state read once.</summary>
    public static async Task<AppModel> App(MockEngine engine, IPreferences? preferences = null)
    {
        var app = new AppModel(engine, preferences);
        await app.RefreshAccount();
        await app.Company.Load();
        await app.RefreshRemote();
        await app.Devices.Load();
        await app.Settings.Load();
        return app;
    }

    /// <summary>A ready app model for one named screen (screenshots and the demo mode).</summary>
    public static async Task<AppModel> Screen(string name)
    {
        var engine = new MockEngine();
        switch (name)
        {
            case "signed-out": SignedOut(engine); break;
            case "second-step": Mfa(engine); break;
            case "setup":
                Company(engine, phase: "offline");
                engine.Fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.");
                break;
            case "consent": Incoming(engine); break;
            case "hosted": Hosted(engine); break;
            case "session" or "session-chat" or "session-files" or "session-tab" or "popout" or "ready-notice" or "sessions":
                Viewing(engine);
                for (ulong sequence = 1; sequence <= 3; sequence++) engine.QueueFrame(VideoFrame.Sample(1280, 800, sequence));
                break;
            case "phone-help": Company(engine, staff: true); break;
            default: Company(engine); break;
        }
        var preferences = new MemoryPreferences();
        if (name == "session-tab") preferences.Set(WorkspaceModel.WhenReadyKey, "open");
        var app = await App(engine, preferences);
        if (name == "connecting")
        {
            _ = app.Workspace.Connect(731_204_118, "SRV-DC-02", "windows", unattended: true);
            engine.ReplyValue("remote_status", new RemoteStatus { Phase = "negotiating", Host = ThisComputer, Peer = 731_204_118, Session = SessionId, Online = [731_204_118, 268_455_901] });
            await app.RefreshRemote();
        }
        if (name == "setup") await app.GoOnlineIfNeeded();
        app.Section = name switch
        {
            "signed-out" or "second-step" or "account" or "invite" => AppSection.Account,
            "devices" or "devices-manage" or "deploy-link" or "connecting" or "ready-notice" or "session-tab" => AppSection.Devices,
            "sessions" => AppSection.Sessions,
            "reports" => AppSection.Reports,
            "settings" => AppSection.Settings,
            _ => AppSection.Remote,
        };
        if (name is "ready-notice" or "sessions" or "popout" || name.StartsWith("session", StringComparison.Ordinal))
        {
            app.Session.ShowSample(VideoFrame.Sample(1280, 800, 1));
            await app.Session.ReadTools();
        }
        if (name == "consent") await app.Host.LoadSources();
        if (name == "account") await app.Company.LoadReport();
        if (name == "reports") await app.Reports.Load();
        if (name == "devices") app.Devices.View = DeviceView.All;
        if (name == "session")
        {
            app.Session.Drawing = true;
            app.Session.DrawColor = 0;
            app.Session.BeginStroke(0.55, 0.30);
            foreach (var step in Enumerable.Range(1, 12)) app.Session.ContinueStroke(0.55 + step * 0.012, 0.30 + Math.Sin(step / 2.0) * 0.03);
            app.Session.EndStroke();
            app.Session.DrawTool = "arrow";
            app.Session.BeginStroke(0.30, 0.75);
            app.Session.ContinueStroke(0.45, 0.62);
            app.Session.EndStroke();
            app.Session.DrawTool = "pen";
        }
        return app;
    }
}
