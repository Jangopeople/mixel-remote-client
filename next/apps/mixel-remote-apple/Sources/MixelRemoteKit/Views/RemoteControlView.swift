import SwiftUI

/// Remote control: connect to another computer or phone, and allow remote access to this computer.
public struct RemoteControlView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var host: HostModel
    @ObservedObject var session: SessionModel
    @State private var partner = ""
    @State private var password = ""
    @State private var unattended = false
    @State private var phoneId = ""

    public init(app: AppModel) {
        self.app = app
        host = app.host
        session = app.session
    }

    var remote: RemoteStatus { app.remote }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(L("Remote control"), subtitle: app.connectionMessage)
                if !app.signedIn { signInNotice }
                if app.enrollment.needsSetup, app.signedIn { EnrollmentCard(app: app, enrollment: app.enrollment) }
                if !remote.invitations.isEmpty { invitations }
                if remote.phase == "active", remote.sharing { HostedSessionCard(app: app, host: host) }
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 18) { controlCard; allowCard }
                    VStack(spacing: 18) { controlCard; allowCard }
                }
                if app.account.account?.staff == true { phoneCard }
                if !remote.controllerOnly, app.signedIn, remote.phase == "ready" || remote.unattendedEnabled { unattendedCard }
                MessageLine(app.error)
            }
            .padding(24)
            .frame(maxWidth: 980, alignment: .leading)
        }
    }

    private var signInNotice: some View {
        Card {
            CardTitle(L("Connect with your Mixel account"), symbol: "person.badge.key")
            Text(verbatim: L("Sign in to get your connection ID.")).foregroundStyle(.secondary)
            Button(L("Sign in")) { app.section = .account }.buttonStyle(PrimaryButtonStyle())
        }
    }

    private var controlCard: some View {
        Card {
            CardTitle(L("Control a remote device"), symbol: "display")
            Text(verbatim: L("Enter the 9-digit ID shown on the other computer.")).foregroundStyle(.secondary).font(.callout)
            FieldRow(L("Partner connection ID")) { RemoteIdField(text: $partner) }
            FieldRow(L("One-time password, if provided")) {
                SecureField(text: $password, prompt: Text(verbatim: "")) { Text(verbatim: L("One-time password, if provided")) }
                    .textFieldStyle(.roundedBorder)
                    .disabled(unattended)
            }
            Toggle(isOn: $unattended) { Text(verbatim: L("Connect without confirmation (unattended access)")) }
                .onChange(of: unattended) { value in if value { password = "" } }
            HStack {
                Button {
                    guard let id = Format.parseRemoteId(partner) else { app.error = L("Enter a nine-digit connection ID"); return }
                    let supplied = password
                    password = ""
                    app.connect(to: id, password: supplied, unattended: unattended)
                } label: { Label(L("Connect"), systemImage: "arrow.right") }
                    .buttonStyle(PrimaryButtonStyle())
                    .disabled(app.busy || !app.signedIn || !(["ready", "offline", "error"].contains(remote.phase)))
                    .keyboardShortcut(.defaultAction)
                if remote.phase == "outgoing" {
                    Button(L("Cancel")) { session.end() }
                }
            }
            if !app.progress.isEmpty, app.busy || remote.phase == "outgoing" {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(verbatim: app.progress).font(.callout).foregroundStyle(.secondary) }
            }
        }
    }

    private var allowCard: some View {
        Card {
            CardTitle(L("Allow remote access"), symbol: "lock.open.display")
            if remote.controllerOnly {
                Text(verbatim: L("This app controls other devices; it cannot be controlled itself.")).foregroundStyle(.secondary)
            } else {
                FieldRow(L("Your connection ID")) { RemoteIdText(app.signedIn ? remote.host : nil) }
                HStack {
                    StatusBadge(remote.connected ? .online : remote.phase == "error" ? .attention : .offline,
                                remote.connected ? L("Online") : remote.phase == "error" ? L("Connection problem") : L("Offline"))
                    Spacer()
                    Button(L("Copy ID")) { host.copyId() }.disabled(!app.signedIn || remote.host == nil)
                }
                if let code = remote.supportCode, app.signedIn {
                    FieldRow(L("One-time password")) { Text(verbatim: code).font(.title2.monospaced()).textSelection(.enabled) }
                    Text(verbatim: L("Share this password with your technician. It lasts five minutes and allows one request.")).font(.callout).foregroundStyle(.secondary)
                }
                HStack {
                    if remote.disconnected {
                        Button(L("Make this computer available")) { app.goOnline() }.disabled(app.busy || !app.signedIn)
                    } else {
                        Button(L("Go offline")) { app.goOffline() }.disabled(app.busy)
                    }
                    if app.signedIn, remote.host != nil {
                        if remote.codeRequired {
                            Button(L("Stop requiring a password")) { host.oneTimePassword(required: false) }
                        } else {
                            Button(L("Generate one-time password")) { host.oneTimePassword(required: true) }.disabled(remote.phase != "ready")
                        }
                    }
                }
                if app.signedIn, remote.phase == "ready" {
                    pairing
                }
            }
        }
    }

    @ViewBuilder private var pairing: some View {
        if let prompt = remote.pairing {
            Text(verbatim: L("Pair this computer with %@ for %@?", prompt.providerName, prompt.customerName)).font(.callout)
            Button(L("Confirm pairing")) { host.confirmPairing() }
        } else {
            HStack {
                Button(L("Check for pairing")) { host.checkPairing() }.linkButton()
                if let message = remote.pairingMessage { Text(verbatim: Translator.shared.translate(message)).font(.callout).foregroundStyle(.secondary) }
            }
        }
    }

    private var phoneCard: some View {
        Card {
            CardTitle(L("Help someone on their phone"), symbol: "iphone")
            Text(verbatim: L("The person opens Mixel Quick Support on their phone and tells you the ID it shows.")).foregroundStyle(.secondary).font(.callout)
            FieldRow(L("ID from the phone")) { RemoteIdField(text: $phoneId) }
            Button {
                guard let id = Format.parseRemoteId(phoneId) else { app.error = L("Enter a nine-digit connection ID"); return }
                app.connect(to: id)
            } label: { Label(L("Connect"), systemImage: "arrow.right") }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(app.busy || !app.signedIn)
        }
    }

    private var unattendedCard: some View {
        Card {
            CardTitle(L("Unattended sharing"), symbol: "clock.badge.checkmark")
            Text(verbatim: remote.unattendedPending == "enabling" ? L("Turning on. Waiting for the connection service.")
                 : remote.unattendedPending != nil ? L("Sharing is off. Confirming with the connection service.")
                 : remote.unattendedEnabled ? L("On. Approved users can connect while this computer stays signed in.")
                 : L("Off. Choose a display and confirm before enabling."))
                .foregroundStyle(.secondary).font(.callout)
            if remote.unattendedEnabled || remote.unattendedPending == "enabling" {
                Button(L("Turn off unattended sharing")) { host.disableUnattended() }
            } else {
                if host.unattendedSources.isEmpty {
                    Button(L("Choose a display")) { host.loadUnattendedSources() }.disabled(app.busy)
                } else {
                    Picker(selection: $host.unattendedSource) {
                        ForEach(host.unattendedSources) { Text(verbatim: $0.name).tag($0.id) }
                    } label: { Text(verbatim: L("Display")) }
                    Toggle(isOn: $host.unattendedControl) { Text(verbatim: L("Allow keyboard and mouse control")) }.disabled(!remote.inputAccess)
                    Toggle(isOn: $host.unattendedConfirm) { Text(verbatim: L("I confirm that approved users may see this display without asking.")) }
                    Button(L("Enable unattended sharing")) { host.enableUnattended() }
                        .disabled(app.busy || !host.unattendedConfirm || remote.codeRequired)
                }
            }
            MessageLine(host.error)
        }
    }

    private var invitations: some View {
        Card {
            CardTitle(L("A colleague invites you to a session"), symbol: "person.2")
            ForEach(remote.invitations) { invitation in
                HStack {
                    VStack(alignment: .leading) {
                        Text(verbatim: L("Computer %@", Format.remoteId(invitation.host))).font(.body.weight(.medium))
                        Text(verbatim: invitation.control ? L("View and control") : L("View only")).font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(L("Not now")) { session.dismiss(invitation) }
                    Button(L("Join")) { session.join(invitation) }.buttonStyle(PrimaryButtonStyle())
                }
            }
        }
    }
}

/// While this computer is shared: who is connected, a colleague asking to join, a meeting offer,
/// switching sides and ending.
struct HostedSessionCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var host: HostModel

    var body: some View {
        Card {
            CardTitle(L("A technician is connected to this computer."), symbol: "dot.radiowaves.left.and.right")
            Text(verbatim: app.remote.control ? L("They can see your screen and use keyboard and mouse.") : L("They can see your screen."))
                .foregroundStyle(.secondary)
            if app.remote.recordingPermitted {
                Label(L("You allowed the other person to save a video of this session."), systemImage: "record.circle").font(.callout)
            }
            ForEach(app.remote.colleagues) { colleague in
                HStack {
                    Label(L("Colleague %@", Format.remoteId(colleague.connection)), systemImage: "person.fill")
                    Text(verbatim: colleague.control ? L("View and control") : L("View only")).foregroundStyle(.secondary)
                    Spacer()
                    Button(L("Remove")) { host.removeColleague(colleague) }
                }
            }
            if let request = app.remote.joinRequest {
                Divider()
                Text(verbatim: L("A second technician wants to join")).font(.headline)
                Text(verbatim: L("Technician %@ was invited by the technician who is connected.", Format.remoteId(request.connection))).font(.callout).foregroundStyle(.secondary)
                HStack {
                    Button(L("Decline")) { host.decideJoin(accept: false, control: false) }
                    Button(L("Allow viewing")) { host.decideJoin(accept: true, control: false) }
                    if request.control { Button(L("Allow viewing and control")) { host.decideJoin(accept: true, control: true) }.buttonStyle(PrimaryButtonStyle()) }
                }
            }
            if host.meetingOffer != nil {
                Divider()
                Text(verbatim: L("The technician wants to show you their screen.")).font(.headline)
                HStack {
                    Button(L("Not now")) { app.session.dismissedMeetingRoom = host.meetingOffer ?? "" }
                    Button(L("Show it")) { host.openMeetingOffer() }.buttonStyle(PrimaryButtonStyle())
                }
            }
            HStack {
                if app.session.canSwitchSides, !app.remote.switched {
                    Button(L("Switch sides")) { app.session.switchSides() }
                }
                Spacer()
                Button(role: .destructive) { host.stopSharing() } label: { Label(L("End session"), systemImage: "xmark.circle") }
                    .buttonStyle(.borderedProminent).tint(Theme.brand)
            }
        }
    }
}
