import Foundation

// What the engine reports, decoded from its JSON (snake_case keys become camelCase).

/// The sign-in state (`account_status`).
public struct AccountStatus: Codable, Equatable, Sendable {
    /// `signed_out`, `starting`, `waiting_browser`, `verifying`, `mfa_required`, `profile_setup`,
    /// `authenticated`, `error`.
    public var phase: String
    public var message: String
    public var account: Account?
    public var factors: [Factor]
    public init(phase: String, message: String, account: Account? = nil, factors: [Factor] = []) {
        self.phase = phase
        self.message = message
        self.account = account
        self.factors = factors
    }
    public var signedIn: Bool { phase == "authenticated" && account != nil }
    public var signingIn: Bool { ["starting", "waiting_browser", "verifying"].contains(phase) }
}

public struct Account: Codable, Equatable, Sendable {
    public var remembered: Bool
    public var hasCompany: Bool
    public var hasPersonal: Bool
    public var profile: String
    public var organization: String?
    public var staff: Bool
    public var personal: Bool
    public var displayName: String?
    public var companies: [CompanyAdministration]
    public init(remembered: Bool = false, hasCompany: Bool = false, hasPersonal: Bool = false, profile: String, organization: String? = nil, staff: Bool = false, personal: Bool = false, displayName: String? = nil, companies: [CompanyAdministration] = []) {
        self.remembered = remembered
        self.hasCompany = hasCompany
        self.hasPersonal = hasPersonal
        self.profile = profile
        self.organization = organization
        self.staff = staff
        self.personal = personal
        self.displayName = displayName
        self.companies = companies
    }
}

public struct CompanyAdministration: Codable, Equatable, Sendable, Identifiable {
    public var companyId: String
    public var companyName: String
    public var id: String { companyId }
}

public struct Factor: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var factorType: String
    public var status: String
    public var friendlyName: String?
    public init(id: String, factorType: String = "totp", status: String = "verified", friendlyName: String? = nil) {
        self.id = id
        self.factorType = factorType
        self.status = status
        self.friendlyName = friendlyName
    }
}

/// The connection to Mixel and the session (`remote_status`).
public struct RemoteStatus: Codable, Equatable, Sendable {
    public var controllerOnly: Bool
    public var platform: String
    /// `offline`, `connecting`, `ready`, `outgoing`, `incoming`, `deciding`, `negotiating`,
    /// `active`, `closing`, `error`.
    public var phase: String
    public var message: String
    public var host: UInt32?
    public var session: String?
    public var consent: String?
    public var peer: UInt32?
    public var requester: String?
    public var received: UInt64
    public var sharing: Bool
    public var control: Bool
    public var clipboard: Bool
    public var files: Bool
    public var recordingPermitted: Bool
    public var recording: RecordingView?
    public var previousRecording: RecordingView?
    public var inputAccess: Bool
    public var unattendedEnabled: Bool
    /// `enabling`, `disabling`, … while the connection service confirms a change.
    public var unattendedPending: String?
    public var unattendedSession: Bool
    public var pairing: PairingPrompt?
    public var pairingMessage: String?
    public var codeRequired: Bool
    public var supportCode: String?
    public var online: [UInt32]
    public var displays: UInt16
    public var display: UInt16
    public var displayAll: Bool
    public var printJobs: [String]
    public var hostActions: [String]
    public var signinId: UInt32?
    public var hostMacs: [String]
    /// The technician offers to show their screen in this meeting room: (session, room).
    public var meetingOffer: [String]?
    public var colleagues: [Colleague]
    public var joinRequest: JoinRequest?
    public var invitations: [Invitation]
    public var inviteMessage: String?
    public var joinedSession: Bool
    public var switched: Bool
    public var peerFeatures: [String]

    public static let offline = RemoteStatus(phase: "offline")

    public init(phase: String, message: String = "", controllerOnly: Bool = false, platform: String = "macos", host: UInt32? = nil, session: String? = nil, consent: String? = nil, peer: UInt32? = nil, received: UInt64 = 0, sharing: Bool = false, control: Bool = false, clipboard: Bool = false, files: Bool = false, recordingPermitted: Bool = false, recording: RecordingView? = nil, inputAccess: Bool = true, online: [UInt32] = [], displays: UInt16 = 0, display: UInt16 = 0, displayAll: Bool = false, hostActions: [String] = [], colleagues: [Colleague] = [], joinRequest: JoinRequest? = nil, invitations: [Invitation] = [], peerFeatures: [String] = [], unattendedSession: Bool = false, switched: Bool = false, printJobs: [String] = []) {
        self.controllerOnly = controllerOnly
        self.platform = platform
        self.phase = phase
        self.message = message
        self.host = host
        self.session = session
        self.consent = consent
        self.peer = peer
        self.requester = nil
        self.received = received
        self.sharing = sharing
        self.control = control
        self.clipboard = clipboard
        self.files = files
        self.recordingPermitted = recordingPermitted
        self.recording = recording
        self.previousRecording = nil
        self.inputAccess = inputAccess
        self.unattendedEnabled = false
        self.unattendedPending = nil
        self.unattendedSession = unattendedSession
        self.pairing = nil
        self.pairingMessage = nil
        self.codeRequired = false
        self.supportCode = nil
        self.online = online
        self.displays = displays
        self.display = display
        self.displayAll = displayAll
        self.printJobs = printJobs
        self.hostActions = hostActions
        self.signinId = nil
        self.hostMacs = []
        self.meetingOffer = nil
        self.colleagues = colleagues
        self.joinRequest = joinRequest
        self.invitations = invitations
        self.inviteMessage = nil
        self.joinedSession = false
        self.switched = switched
        self.peerFeatures = peerFeatures
    }

    /// Connected to Mixel (this computer can be reached and can connect).
    public var connected: Bool { ["ready", "active", "incoming", "outgoing", "deciding", "negotiating"].contains(phase) }
    public var disconnected: Bool { ["offline", "error"].contains(phase) }
    public var inSession: Bool { ["active", "incoming", "outgoing", "deciding", "negotiating", "closing"].contains(phase) }
    /// This app views and (maybe) controls another computer right now.
    public var viewing: Bool { phase == "active" && !sharing && session != nil }
    public func offers(_ action: String) -> Bool { hostActions.contains(action) }
}

public struct PairingPrompt: Codable, Equatable, Sendable {
    public var requestId: String
    public var providerName: String
    public var customerName: String
}

public struct Colleague: Codable, Equatable, Sendable, Identifiable {
    public var session: String
    public var connection: UInt32
    public var control: Bool
    public var state: String
    public var id: String { session }
}

public struct JoinRequest: Codable, Equatable, Sendable {
    public var session: String
    public var connection: UInt32
    public var inviterConnection: UInt32
    public var control: Bool
    public var unattended: Bool
}

public struct Invitation: Codable, Equatable, Sendable, Identifiable {
    public var invitation: String
    public var host: UInt32
    public var inviter: String
    public var inviterConnection: UInt32
    public var control: Bool
    public var unattended: Bool
    public var expiresAt: UInt64
    public var id: String { invitation }
}

/// Recording state of the session: `ready`, `waiting_for_video`, `recording`, `saving`, `saved`,
/// `saved_partial`, `error` (with a message).
public struct RecordingView: Codable, Equatable, Sendable {
    public var phase: String
    public var message: String?
    public init(phase: String, message: String? = nil) {
        self.phase = phase
        self.message = message
    }
    public var running: Bool { phase == "recording" || phase == "waiting_for_video" }
}

/// A screen this computer can share (`capture_targets`).
public struct CaptureTarget: Codable, Equatable, Sendable, Identifiable, Hashable {
    public var id: String
    public var name: String
    public var fullDisplay: Bool
    public init(id: String, name: String, fullDisplay: Bool) {
        self.id = id
        self.name = name
        self.fullDisplay = fullDisplay
    }
}

/// The host's answer to a connection request (`remote_decide`).
public struct Decision: Codable, Equatable, Sendable {
    public var session: String
    public var accept: Bool
    public var source: String
    public var confirm: Bool
    public var control: Bool
    public var clipboard: Bool
    public var files: Bool
    public var chat: Bool
    public var recording: Bool
    public init(session: String, accept: Bool, source: String, confirm: Bool, control: Bool, clipboard: Bool, files: Bool, chat: Bool, recording: Bool) {
        self.session = session
        self.accept = accept
        self.source = source
        self.confirm = confirm
        self.control = control
        self.clipboard = clipboard
        self.files = files
        self.chat = chat
        self.recording = recording
    }
}

// MARK: Devices

/// A device of the account's directory (`remote_devices`), or of a company (`saas_devices`).
public struct Device: Codable, Equatable, Sendable, Identifiable, Hashable {
    public var device: String
    public var connectionId: UInt32
    public var name: String
    public var os: String
    public var groupId: String?
    public var groupName: String?
    public var generation: UInt64?
    public var id: String { device }
    public init(device: String, connectionId: UInt32, name: String, os: String, groupId: String? = nil, groupName: String? = nil, generation: UInt64? = nil) {
        self.device = device
        self.connectionId = connectionId
        self.name = name
        self.os = os
        self.groupId = groupId
        self.groupName = groupName
        self.generation = generation
    }
}

public struct DevicePage: Codable, Equatable, Sendable {
    public var devices: [Device]
    public var next: UInt32?
}

public struct SaasDevice: Codable, Equatable, Sendable {
    public var deviceId: String
    public var name: String
    public var os: String
    public var groupId: String?
    public var groupName: String?
    public var connectionId: UInt32?
    public var generation: UInt64?
    public var revoked: Bool?
    public var device: Device? {
        guard let connectionId, revoked != true else { return nil }
        return Device(device: deviceId, connectionId: connectionId, name: name, os: os, groupId: groupId, groupName: groupName, generation: generation)
    }
}

public struct SaasPresence: Codable, Equatable, Sendable {
    public var deviceId: String
    public var connectionId: UInt32?
    public var status: String
    public var lastSeen: String?
}

public struct SaasGroup: Codable, Equatable, Sendable, Identifiable, Hashable {
    public var groupId: String
    public var name: String
    public var devices: Int
    public var technicians: [String]
    public var id: String { groupId }
}

// MARK: Company

public struct Tenant: Codable, Equatable, Sendable, Identifiable {
    public var tenantId: String
    public var organizationId: String
    public var name: String
    public var slug: String
    public var role: String
    public var plan: String
    public var status: String
    public var trialEndsAt: String?
    public var id: String { tenantId }
    public init(tenantId: String, organizationId: String, name: String, slug: String, role: String, plan: String, status: String, trialEndsAt: String? = nil) {
        self.tenantId = tenantId
        self.organizationId = organizationId
        self.name = name
        self.slug = slug
        self.role = role
        self.plan = plan
        self.status = status
        self.trialEndsAt = trialEndsAt
    }
}

/// `saas_tenant_overview`: the tenant plus features, limits and usage.
public struct TenantOverview: Codable, Equatable, Sendable {
    public var role: String?
    public var status: String?
    public var plan: String?
    public var trialEndsAt: String?
    public var features: [String: Bool]?
    public var limits: Limits?
    public var usage: Usage?
    public struct Limits: Codable, Equatable, Sendable {
        public var devices: Int?
        public var technicians: Int?
    }
    public struct Usage: Codable, Equatable, Sendable {
        public var devices: Int
        public var technicians: Int
    }
}

public struct Member: Codable, Equatable, Sendable, Identifiable {
    public var userId: String
    public var name: String
    public var email: String
    public var role: String
    public var createdAt: String
    public var id: String { userId }
}

public struct Invite: Codable, Equatable, Sendable, Identifiable {
    public var id: String
    public var email: String
    public var role: String
    public var createdAt: String
    public var expiresAt: String
    public var mailState: String?
    public var mailSentAt: String?
}

public struct DeployLinkCreated: Codable, Equatable, Sendable {
    public var token: String
    public var windowsCommand: String
    public var macosCommand: String
    public var linuxCommand: String
}

public struct SaasEnrollment: Codable, Equatable, Sendable {
    public var deviceId: String
    public var connectionId: UInt32
    public var generation: UInt64
}

public struct SignupResult: Codable, Equatable, Sendable {
    public var tenantId: String
    public var plan: String
    public var status: String
    public var trialEndsAt: String?
}

// MARK: Session tools

public struct ChatMessage: Codable, Equatable, Sendable, Identifiable {
    public var id: UInt64
    public var mine: Bool
    public var text: String
}

public struct FileView: Codable, Equatable, Sendable {
    public var connected: Bool
    public var incoming: Transfer?
    public var outgoing: Transfer?
    public var message: String
    public struct Transfer: Codable, Equatable, Sendable {
        public var id: String
        public var name: String
        public var size: UInt64
        public var offset: UInt64
        public var phase: String
        public var message: String
        public var progress: Double { size == 0 ? 0 : min(1, Double(offset) / Double(size)) }
    }
}

public struct MeetingRoom: Codable, Equatable, Sendable {
    public var room: String
    public var invite: String
    public var host: String
}

public struct PrintingStatus: Codable, Equatable, Sendable {
    public var supported: Bool
    public var installed: Bool
}

public struct StartupSettings: Codable, Equatable, Sendable {
    public var remembered: Bool
    public var autostart: Bool
    public var launchedAtStartup: Bool
    public var restored: Bool
}

public struct UpdateInfo: Codable, Equatable, Sendable {
    public var version: String
}

public enum SelfRemovalStatus: String, Codable, Sendable {
    case removed, queued, stale
}

/// An input event, encoded as the desktop app sends it (`{"kind":"key","usage":4,"down":true}`).
public enum InputEvent: Encodable, Equatable, Sendable {
    case text(String)
    case key(usage: UInt16, down: Bool)
    case pointer(x: UInt16, y: UInt16)
    case buttonClick(button: UInt8, down: Bool, clicks: UInt8)
    case wheel(horizontal: Int16, vertical: Int16)
    case releaseAll
    case selectDisplay(UInt16)
    case action(String)

    private enum Keys: String, CodingKey { case kind, text, usage, down, x, y, button, clicks, horizontal, vertical, index, name }
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: Keys.self)
        switch self {
        case .text(let text):
            try container.encode("text", forKey: .kind); try container.encode(text, forKey: .text)
        case .key(let usage, let down):
            try container.encode("key", forKey: .kind); try container.encode(usage, forKey: .usage); try container.encode(down, forKey: .down)
        case .pointer(let x, let y):
            try container.encode("pointer", forKey: .kind); try container.encode(x, forKey: .x); try container.encode(y, forKey: .y)
        case .buttonClick(let button, let down, let clicks):
            try container.encode("button_click", forKey: .kind); try container.encode(button, forKey: .button)
            try container.encode(down, forKey: .down); try container.encode(clicks, forKey: .clicks)
        case .wheel(let horizontal, let vertical):
            try container.encode("wheel", forKey: .kind); try container.encode(horizontal, forKey: .horizontal); try container.encode(vertical, forKey: .vertical)
        case .releaseAll:
            try container.encode("release_all", forKey: .kind)
        case .selectDisplay(let index):
            try container.encode("select_display", forKey: .kind); try container.encode(index, forKey: .index)
        case .action(let name):
            try container.encode("action", forKey: .kind); try container.encode(name, forKey: .name)
        }
    }
}
