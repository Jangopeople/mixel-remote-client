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
    /// The session was opened with a one-time support password (docs/mixel-remote-ui-engine-requests.md,
    /// item 3). Absent until the engine reports it; the app then relies on what it sent itself.
    public var passwordSession: PasswordSessionView?
    /// Every technician session this app holds, oldest first (engine 2.1+). `nil` from an older engine,
    /// which describes one session in the fields above: `viewerSessions` covers both.
    public var sessions: [RemoteSessionItem]?
    /// How many technician sessions the engine allows at once (4); `nil` from an older engine (one).
    public var maxSessions: Int?
    /// The connection to the connection service was lost; the session itself continues and is resumed (engine 2.1.0).
    public var reconnecting: Bool?
    public var isReconnecting: Bool { reconnecting == true }

    public static let offline = RemoteStatus(phase: "offline")

    public init(phase: String, message: String = "", controllerOnly: Bool = false, platform: String = "macos", host: UInt32? = nil, session: String? = nil, consent: String? = nil, peer: UInt32? = nil, received: UInt64 = 0, sharing: Bool = false, control: Bool = false, clipboard: Bool = false, files: Bool = false, recordingPermitted: Bool = false, recording: RecordingView? = nil, inputAccess: Bool = true, online: [UInt32] = [], displays: UInt16 = 0, display: UInt16 = 0, displayAll: Bool = false, hostActions: [String] = [], colleagues: [Colleague] = [], joinRequest: JoinRequest? = nil, invitations: [Invitation] = [], peerFeatures: [String] = [], unattendedSession: Bool = false, switched: Bool = false, printJobs: [String] = [], sessions: [RemoteSessionItem]? = nil, maxSessions: Int? = nil) {
        self.sessions = sessions
        self.maxSessions = maxSessions
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
        self.passwordSession = nil
        self.reconnecting = nil
    }

    /// Connected to Mixel (this computer can be reached and can connect).
    public var connected: Bool { ["ready", "active", "incoming", "outgoing", "deciding", "negotiating"].contains(phase) }
    public var disconnected: Bool { ["offline", "error"].contains(phase) }
    public var inSession: Bool { ["active", "incoming", "outgoing", "deciding", "negotiating", "closing"].contains(phase) }
    /// This app views and (maybe) controls another computer right now.
    public var viewing: Bool { phase == "active" && !sharing && session != nil }
    public func offers(_ action: String) -> Bool { hostActions.contains(action) }

    /// The technician sessions: the engine's list, or (older engine without `sessions`) the one session the
    /// single-session fields describe. The `id` of that one is the connection's session id.
    public var viewerSessions: [RemoteSessionItem] {
        if let sessions { return sessions }
        guard !sharing, let peer, let session, ["outgoing", "negotiating", "active", "closing"].contains(phase) else { return [] }
        return [RemoteSessionItem(id: session, session: session, peer: peer, phase: phase, message: message, consent: consent, received: received,
                                  control: control, clipboard: clipboard, files: files, recordingPermitted: recordingPermitted, recording: recording,
                                  displays: displays, display: display, displayAll: displayAll, printJobs: printJobs, hostActions: hostActions,
                                  signinId: signinId, hostMacs: hostMacs, meetingOffer: meetingOffer, peerFeatures: peerFeatures,
                                  joinedSession: joinedSession, switched: switched, passwordSession: passwordSession, unattendedSession: unattendedSession)]
    }

    /// Sessions the engine lets this app hold (1 for an older engine).
    public var sessionLimit: Int { max(1, maxSessions ?? 1) }

    /// Another technician session may be requested now: below the limit, and this computer is not
    /// being helped. An older engine (no `sessions`) runs one session and needs `ready`.
    public var canRequestSession: Bool {
        if sessions == nil { return phase == "ready" }
        return connected && !sharing && !["incoming", "deciding"].contains(phase) && viewerSessions.count < sessionLimit
    }

    /// A session of another computer is running, so Connect is off: an older engine runs one session;
    /// a 2.1 engine allows several (at the limit it answers a request with `too_many_sessions`), but not
    /// while this computer is being helped.
    public var connectBlockedBySession: Bool {
        sessions == nil ? inSession : sharing || ["incoming", "deciding"].contains(phase)
    }

    /// A technician session is being requested (waiting for Accept, or negotiating).
    public var requesting: Bool { viewerSessions.contains { $0.isNewRequest } }

    /// This status with the single-session fields (`phase`, `session`, `control`, ...) describing `item`:
    /// what a session's model and screens read as "the session".
    public func describing(_ item: RemoteSessionItem) -> RemoteStatus {
        var copy = self
        copy.sharing = false
        copy.phase = item.phase
        copy.message = item.message
        copy.session = item.session
        copy.consent = item.consent
        copy.peer = item.peer
        copy.received = item.received
        copy.control = item.control
        copy.clipboard = item.clipboard
        copy.files = item.files
        copy.recordingPermitted = item.recordingPermitted
        copy.recording = item.recording
        copy.displays = item.displays
        copy.display = item.display
        copy.displayAll = item.displayAll
        copy.printJobs = item.printJobs
        copy.hostActions = item.hostActions
        copy.signinId = item.signinId
        copy.hostMacs = item.hostMacs
        copy.meetingOffer = item.meetingOffer
        copy.peerFeatures = item.peerFeatures
        copy.joinedSession = item.joinedSession
        copy.switched = item.switched
        // The one-time-password and unattended flags belong to the session itself (engine 2.1.0 reports them per
        // `sessions[]` item; an older engine's single session carries the status's own, see `viewerSessions`).
        copy.passwordSession = item.passwordSession
        copy.unattendedSession = item.unattendedSession
        return copy
    }
}

/// One technician session of `remote_status.sessions` (docs/plans/multi-session-engine-api.md). Decoding is
/// tolerant: a missing field takes its neutral value.
public struct RemoteSessionItem: Codable, Equatable, Sendable, Identifiable {
    /// The stable id of the session, chosen when it is requested; every command carries it.
    public var id: String
    /// The connection service's id of the current transport (`nil` until it answered); changes at a renewal.
    public var session: String?
    public var peer: UInt32?
    /// `outgoing`, `negotiating`, `active`, `closing`.
    public var phase: String
    public var message: String
    public var consent: String?
    public var received: UInt64
    public var control: Bool
    public var clipboard: Bool
    public var files: Bool
    public var recordingPermitted: Bool
    public var recording: RecordingView?
    public var displays: UInt16
    public var display: UInt16
    public var displayAll: Bool
    public var printJobs: [String]
    public var hostActions: [String]
    public var signinId: UInt32?
    public var hostMacs: [String]
    public var meetingOffer: [String]?
    public var peerFeatures: [String]
    public var joinedSession: Bool
    public var switched: Bool
    public var quality: SessionQuality?
    public var startedAt: Double?
    /// The computer went away and this app asks for it again (every 2 s for 60 s); `phase` is `outgoing`.
    public var reconnecting: Bool
    /// The minute is over: "Could not reconnect"; the session stays listed until `remote_reconnect` or `remote_close`.
    public var reconnectFailed: Bool
    /// Opened with a one-time password: this session's own end time (its 5 and 1 minute warnings are this session's only).
    public var passwordSession: PasswordSessionView?
    public var unattendedSession: Bool

    /// A request still waiting for Accept or negotiating (not a session asking its computer again).
    public var isNewRequest: Bool { ["outgoing", "negotiating"].contains(phase) && !reconnecting && !reconnectFailed }

    public init(id: String, session: String? = nil, peer: UInt32? = nil, phase: String = "active", message: String = "", consent: String? = nil,
                received: UInt64 = 0, control: Bool = false, clipboard: Bool = false, files: Bool = false, recordingPermitted: Bool = false,
                recording: RecordingView? = nil, displays: UInt16 = 0, display: UInt16 = 0, displayAll: Bool = false, printJobs: [String] = [],
                hostActions: [String] = [], signinId: UInt32? = nil, hostMacs: [String] = [], meetingOffer: [String]? = nil,
                peerFeatures: [String] = [], joinedSession: Bool = false, switched: Bool = false, quality: SessionQuality? = nil, startedAt: Double? = nil,
                reconnecting: Bool = false, reconnectFailed: Bool = false, passwordSession: PasswordSessionView? = nil, unattendedSession: Bool = false) {
        self.reconnecting = reconnecting; self.reconnectFailed = reconnectFailed
        self.passwordSession = passwordSession; self.unattendedSession = unattendedSession
        self.id = id; self.session = session; self.peer = peer; self.phase = phase; self.message = message; self.consent = consent
        self.received = received; self.control = control; self.clipboard = clipboard; self.files = files
        self.recordingPermitted = recordingPermitted; self.recording = recording; self.displays = displays; self.display = display
        self.displayAll = displayAll; self.printJobs = printJobs; self.hostActions = hostActions; self.signinId = signinId
        self.hostMacs = hostMacs; self.meetingOffer = meetingOffer; self.peerFeatures = peerFeatures
        self.joinedSession = joinedSession; self.switched = switched; self.quality = quality; self.startedAt = startedAt
    }

    private enum Key: String, CodingKey {
        case id, session, peer, device, phase, message, consent, received, control, clipboard, files, recordingPermitted, recording, displays, display
        case displayAll, printJobs, hostActions, signinId, hostMacs, meetingOffer, peerFeatures, joinedSession, switched, quality, startedAt
        case reconnecting, reconnectFailed, passwordSession, unattendedSession
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Key.self)
        id = try c.decode(String.self, forKey: .id)
        session = try? c.decodeIfPresent(String.self, forKey: .session)
        peer = (try? c.decodeIfPresent(UInt32.self, forKey: .peer)) ?? (try? c.decodeIfPresent(UInt32.self, forKey: .device))
        phase = (try? c.decodeIfPresent(String.self, forKey: .phase)) ?? "active"
        message = (try? c.decodeIfPresent(String.self, forKey: .message)) ?? ""
        consent = try? c.decodeIfPresent(String.self, forKey: .consent)
        received = (try? c.decodeIfPresent(UInt64.self, forKey: .received)) ?? 0
        control = (try? c.decodeIfPresent(Bool.self, forKey: .control)) ?? false
        clipboard = (try? c.decodeIfPresent(Bool.self, forKey: .clipboard)) ?? false
        files = (try? c.decodeIfPresent(Bool.self, forKey: .files)) ?? false
        recordingPermitted = (try? c.decodeIfPresent(Bool.self, forKey: .recordingPermitted)) ?? false
        recording = try? c.decodeIfPresent(RecordingView.self, forKey: .recording)
        displays = (try? c.decodeIfPresent(UInt16.self, forKey: .displays)) ?? 0
        display = (try? c.decodeIfPresent(UInt16.self, forKey: .display)) ?? 0
        displayAll = (try? c.decodeIfPresent(Bool.self, forKey: .displayAll)) ?? false
        printJobs = (try? c.decodeIfPresent([String].self, forKey: .printJobs)) ?? []
        hostActions = (try? c.decodeIfPresent([String].self, forKey: .hostActions)) ?? []
        signinId = try? c.decodeIfPresent(UInt32.self, forKey: .signinId)
        hostMacs = (try? c.decodeIfPresent([String].self, forKey: .hostMacs)) ?? []
        meetingOffer = try? c.decodeIfPresent([String].self, forKey: .meetingOffer)
        peerFeatures = (try? c.decodeIfPresent([String].self, forKey: .peerFeatures)) ?? []
        joinedSession = (try? c.decodeIfPresent(Bool.self, forKey: .joinedSession)) ?? false
        switched = (try? c.decodeIfPresent(Bool.self, forKey: .switched)) ?? false
        quality = try? c.decodeIfPresent(SessionQuality.self, forKey: .quality)
        startedAt = try? c.decodeIfPresent(Double.self, forKey: .startedAt)
        reconnecting = (try? c.decodeIfPresent(Bool.self, forKey: .reconnecting)) ?? false
        reconnectFailed = (try? c.decodeIfPresent(Bool.self, forKey: .reconnectFailed)) ?? false
        passwordSession = try? c.decodeIfPresent(PasswordSessionView.self, forKey: .passwordSession)
        unattendedSession = (try? c.decodeIfPresent(Bool.self, forKey: .unattendedSession)) ?? false
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Key.self)
        try c.encode(id, forKey: .id)
        try c.encode(session, forKey: .session)
        try c.encode(peer, forKey: .peer)
        try c.encode(peer, forKey: .device)
        try c.encode(phase, forKey: .phase)
        try c.encode(message, forKey: .message)
        try c.encode(consent, forKey: .consent)
        try c.encode(received, forKey: .received)
        try c.encode(control, forKey: .control)
        try c.encode(clipboard, forKey: .clipboard)
        try c.encode(files, forKey: .files)
        try c.encode(recordingPermitted, forKey: .recordingPermitted)
        try c.encode(recording, forKey: .recording)
        try c.encode(displays, forKey: .displays)
        try c.encode(display, forKey: .display)
        try c.encode(displayAll, forKey: .displayAll)
        try c.encode(printJobs, forKey: .printJobs)
        try c.encode(hostActions, forKey: .hostActions)
        try c.encode(signinId, forKey: .signinId)
        try c.encode(hostMacs, forKey: .hostMacs)
        try c.encode(meetingOffer, forKey: .meetingOffer)
        try c.encode(peerFeatures, forKey: .peerFeatures)
        try c.encode(joinedSession, forKey: .joinedSession)
        try c.encode(switched, forKey: .switched)
        try c.encode(reconnecting, forKey: .reconnecting)
        try c.encode(reconnectFailed, forKey: .reconnectFailed)
        try c.encode(passwordSession, forKey: .passwordSession)
        try c.encode(unattendedSession, forKey: .unattendedSession)
        try c.encode(quality, forKey: .quality)
        try c.encode(startedAt, forKey: .startedAt)
    }
}

/// Picture figures of one session.
public struct SessionQuality: Codable, Equatable, Sendable {
    public var pictures: UInt64
    public var picturesPerSecond: Double
    public var pictureAgeMs: UInt64?
    public init(pictures: UInt64 = 0, picturesPerSecond: Double = 0, pictureAgeMs: UInt64? = nil) {
        self.pictures = pictures; self.picturesPerSecond = picturesPerSecond; self.pictureAgeMs = pictureAgeMs
    }
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

/// `remote_status.password_session`: the session was opened with a one-time password; it ends by itself
/// at `ends_at` (the broker's 2-hour limit, ISO 8601).
public struct PasswordSessionView: Codable, Equatable, Sendable {
    public var endsAt: String?
    /// Seconds left as the engine counts them (informational; the banner counts down from `endsAt`).
    public var secondsLeft: Int?
    public init(endsAt: String? = nil, secondsLeft: Int? = nil) { self.endsAt = endsAt; self.secondsLeft = secondsLeft }
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
    /// Can be installed in the app (signed update channel); otherwise download only.
    public var installable: Bool
    /// Nothing would be interrupted and the sign-in survives the restart: install without asking.
    public var silent: Bool
    /// A session is running (or ended a moment ago): the update is ready and installs after it (`waits_for_session`).
    public var waitsForSession: Bool
    /// Seconds of idle time still needed after the last session (`idle_seconds_left`), when it is counting down.
    public var idleSecondsLeft: Int?
    public init(version: String, installable: Bool = false, silent: Bool = false, waitsForSession: Bool = false, idleSecondsLeft: Int? = nil) {
        self.version = version
        self.installable = installable
        self.silent = silent
        self.waitsForSession = waitsForSession
        self.idleSecondsLeft = idleSecondsLeft
    }
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decode(String.self, forKey: .version)
        installable = try container.decodeIfPresent(Bool.self, forKey: .installable) ?? false
        silent = try container.decodeIfPresent(Bool.self, forKey: .silent) ?? false
        waitsForSession = try container.decodeIfPresent(Bool.self, forKey: .waitsForSession) ?? false
        idleSecondsLeft = try container.decodeIfPresent(Int.self, forKey: .idleSecondsLeft)
    }
}

/// Access at this Mac's sign-in screen (before anyone signs in).
public struct SignInScreenStatus: Codable, Equatable, Sendable {
    public enum Availability: String, Codable, Sendable {
        /// The login window host is in this app with Apple's virtual keyboard entitlement.
        case ready
        /// Apple has not granted the virtual keyboard entitlement yet: the host is not shipped.
        case waitingForApple = "waiting_for_apple"
        /// Not on this device (iPhone, iPad).
        case notHere = "not_here"
    }
    public var supported: Bool
    public var availability: Availability
    /// Set up: the Remote ID of the sign-in screen.
    public var connectionId: UInt32?
    public var name: String?
    public init(supported: Bool, availability: Availability, connectionId: UInt32? = nil, name: String? = nil) {
        self.supported = supported
        self.availability = availability
        self.connectionId = connectionId
        self.name = name
    }
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
