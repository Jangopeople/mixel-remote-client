import Foundation

#if DEBUG

/// Scripted engine states for the view-model tests and the screenshots of every screen (Debug builds only).
@MainActor
public enum DemoFixtures {
    public static let profile = "11111111-2222-4333-8444-555555555555"
    public static let tenant = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    public static let session = "0f0e0d0c-0b0a-4908-8706-050403020100"
    public static let consent = "c0c0c0c0-0000-4000-8000-000000000001"

    public static func signedOut(_ engine: MockEngine) {
        engine.reply("account_status", AccountStatus(phase: "signed_out", message: "Sign in with your Mixel account"))
        engine.reply("remote_status", RemoteStatus(phase: "offline"))
    }

    public static func mfa(_ engine: MockEngine) {
        engine.reply("account_status", AccountStatus(phase: "mfa_required", message: "Additional verification is required for this Mixel account",
                                                     factors: [Factor(id: "f0000000-0000-4000-8000-000000000001", friendlyName: "Mixel ISM staff")]))
        engine.reply("remote_status", RemoteStatus(phase: "offline"))
    }

    public static func company(_ engine: MockEngine, phase: String = "ready", role: String = "owner") {
        engine.reply("account_status", AccountStatus(phase: "authenticated", message: "Signed in",
                                                     account: Account(remembered: true, hasCompany: true, profile: profile, organization: tenant, displayName: "Anna Muster")))
        engine.reply("remote_status", RemoteStatus(phase: phase, host: 482_913_557, online: [731_204_118, 268_455_901]))
        engine.reply("saas_my_tenants", [Tenant(tenantId: tenant, organizationId: tenant, name: "Muster Treuhand AG", slug: "muster", role: role, plan: "business", status: "trial", trialEndsAt: "2026-11-27T10:00:00Z")])
        engine.reply("saas_tenant_overview", json: """
        {"role":"\(role)","status":"trial","plan":"business","trial_ends_at":"2026-11-27T10:00:00Z","features":{"groups":true},"limits":{"devices":2000,"technicians":15},"usage":{"devices":4,"technicians":2}}
        """)
        engine.reply("saas_members", json: """
        [{"user_id":"\(profile)","name":"Anna Muster","email":"anna@muster.test","role":"owner","created_at":"2026-11-01T08:00:00Z"},
         {"user_id":"22222222-2222-4222-8222-222222222222","name":"Marco Rossi","email":"marco@muster.test","role":"technician","created_at":"2026-11-02T08:00:00Z"}]
        """)
        engine.reply("saas_invites", json: """
        [{"id":"33333333-3333-4333-8333-333333333333","email":"lea@muster.test","role":"technician","created_at":"2026-11-12T08:00:00Z","expires_at":"2026-11-19T08:00:00Z","mail_state":"delivered"}]
        """)
        engine.reply("saas_devices", json: """
        [{"device_id":"d1000000-0000-4000-8000-000000000001","name":"Reception PC","os":"windows","group_id":"g1000000-0000-4000-8000-000000000001","group_name":"Front office","connection_id":731204118,"generation":3,"revoked":false},
         {"device_id":"d1000000-0000-4000-8000-000000000002","name":"Accounting Mac","os":"macos","group_id":null,"group_name":null,"connection_id":268455901,"generation":1,"revoked":false},
         {"device_id":"d1000000-0000-4000-8000-000000000003","name":"Warehouse laptop","os":"linux","group_id":null,"group_name":null,"connection_id":519003442,"generation":2,"revoked":false},
         {"device_id":"d1000000-0000-4000-8000-000000000004","name":"This Mac","os":"macos","group_id":null,"group_name":null,"connection_id":482913557,"generation":1,"revoked":false}]
        """)
        engine.reply("saas_groups", json: """
        [{"group_id":"g1000000-0000-4000-8000-000000000001","name":"Front office","devices":1,"technicians":[]}]
        """)
        engine.reply("saas_device_presence", json: """
        [{"device_id":"d1000000-0000-4000-8000-000000000003","connection_id":519003442,"status":"offline","last_seen":"\(ISO8601DateFormatter().string(from: Date().addingTimeInterval(-3 * 3600)))"}]
        """)
        engine.reply("startup_settings", StartupSettings(remembered: true, autostart: false, launchedAtStartup: false, restored: true))
        engine.reply("remote_printing_status", PrintingStatus(supported: true, installed: false))
        engine.reply("screen_access", json: "true")
        engine.reply("device_removal_pending", json: "false")
        engine.reply("this_computer_name", json: "\"Anna’s MacBook Pro\"")
        engine.reply("capture_targets", [CaptureTarget(id: "display:1", name: "Display 1 · 2560 × 1440", fullDisplay: true)])
        engine.reply("quick_phone_claim", json: "false")
    }

    public static func incoming(_ engine: MockEngine) {
        company(engine)
        engine.reply("remote_status", RemoteStatus(phase: "incoming", host: 482_913_557, session: session, consent: consent, peer: 731_204_118, inputAccess: true))
    }

    public static func viewing(_ engine: MockEngine, actions: [String] = ["privacy", "block", "pointer", "lock", "lockend", "sas", "restart", "sound", "meet"]) {
        company(engine)
        engine.reply("remote_status", RemoteStatus(phase: "active", host: 482_913_557, session: session, consent: consent, peer: 731_204_118, received: 12,
                                                   control: true, clipboard: true, files: true, recording: RecordingView(phase: "ready"), displays: 2,
                                                   hostActions: actions, peerFeatures: ["join", "switch"]))
        engine.reply("remote_chat_messages", [ChatMessage(id: 1, mine: false, text: "Hello, the printer shows an error."), ChatMessage(id: 2, mine: true, text: "I see it, one moment.")])
        engine.reply("remote_file_status", json: """
        {"connected":true,"incoming":null,"outgoing":{"id":"f1000000-0000-4000-8000-000000000001","name":"driver-setup.exe","size":48000000,"offset":31000000,"phase":"transferring","message":""},"message":""}
        """)
    }

    public static func hostedSession(_ engine: MockEngine) {
        company(engine)
        var status = RemoteStatus(phase: "active", host: 482_913_557, session: session, consent: consent, peer: 731_204_118, sharing: true, control: true,
                                  recordingPermitted: true, colleagues: [Colleague(session: "c1", connection: 268_455_901, control: false, state: "active")],
                                  joinRequest: JoinRequest(session: "c2", connection: 519_003_442, inviterConnection: 731_204_118, control: true, unattended: false),
                                  peerFeatures: ["switch"])
        status.message = "Sharing"
        engine.reply("remote_status", status)
    }

    /// An app model with this engine state read once.
    public static func app(_ engine: MockEngine) async -> AppModel {
        let app = AppModel(engine: engine)
        await app.refreshAccount()
        await app.company.load()
        await app.refreshRemote()
        await app.devices.load()
        await app.settings.load()
        return app
    }

    /// A ready app model for one named screen (screenshots of the real app in the Simulator).
    public static func screen(_ name: String) async -> AppModel {
        let engine = MockEngine(platform: "ios", controllerOnly: true)
        switch name {
        case "signed-out": signedOut(engine)
        case "second-step": mfa(engine)
        case "setup":
            company(engine, phase: "offline")
            engine.fail("remote_register", "No saved device identity. Choose Create device identity to generate one locally.")
        case "session", "chat":
            viewing(engine)
            for sequence in 1...3 { engine.queue(frame: VideoFrame.sample(width: 1280, height: 800, sequence: UInt64(sequence))) }
        default: company(engine)
        }
        let app = await self.app(engine)
        switch name {
        case "signed-out", "second-step", "account": app.section = .account
        case "connect", "setup": app.section = .remote
        default: app.section = .devices
        }
        if name == "session" || name == "chat" {
            await app.session.readTools()
        }
        return app
    }
}
#endif
