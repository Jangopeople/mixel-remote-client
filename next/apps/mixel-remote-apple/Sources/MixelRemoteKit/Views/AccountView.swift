import SwiftUI

/// Account: sign in with Mixel ID (and its second step), the company with its members and
/// invitations, creating a company or a personal space, deleting the account.
public struct AccountView: View {
    @ObservedObject var app: AppModel
    @ObservedObject var company: CompanyModel

    public init(app: AppModel) {
        self.app = app
        company = app.company
    }

    var account: AccountStatus { app.account }

    public var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                PageHeader(L("Account"), subtitle: account.signedIn ? "" : L("A secure home for your devices and connections."))
                if account.signedIn, let profile = account.account {
                    profileCard(profile)
                    if company.tenant != nil { CompanyCard(app: app, company: company) }
                    else if company.loaded, !profile.staff { CreateCompanyCard(app: app, company: company) }
                    dangerCard
                } else {
                    signInCard
                }
                MessageLine(app.error)
            }
            .padding(24)
            .frame(maxWidth: 820, alignment: .leading)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.background)
    }

    @ViewBuilder private var signInCard: some View {
        Card {
            CardTitle(L("Connect with your Mixel account"), symbol: "person.badge.key")
            switch account.phase {
            case "starting", "waiting_browser", "verifying":
                HStack(spacing: 10) {
                    ProgressView().controlSize(.small)
                    Text(verbatim: Translator.shared.translate(account.message))
                }
                if let link = app.signInLink {
                    FieldRow(L("Open this link in your other browser")) {
                        HStack {
                            Text(verbatim: link).font(.caption.monospaced()).lineLimit(2).truncationMode(.middle).textSelection(.enabled)
                            Button { Pasteboard.copy(link) } label: { Label(L("Copy"), systemImage: "doc.on.doc") }.labelStyle(.iconOnly)
                        }
                    }
                }
                Button(L("Cancel sign-in")) { app.cancelSignIn() }
            case "mfa_required":
                Text(verbatim: L("Additional verification is required for this Mixel account")).foregroundStyle(.secondary)
                let factor = account.factors.first
                if let name = factor?.friendlyName {
                    Text(verbatim: L("Enter the code from the authenticator entry “%@”.", name)).font(.callout)
                }
                FieldRow(L("Verification code")) {
                    TextField(text: $app.verificationCode, prompt: Text(verbatim: "123456")) { Text(verbatim: L("Verification code")) }
                        .textFieldStyle(.roundedBorder)
                        .font(.title3.monospacedDigit())
                        .frame(maxWidth: 200)
                        #if os(iOS)
                        .keyboardType(.numberPad)
                        .textContentType(.oneTimeCode)
                        #endif
                        .onSubmit { if let factor { app.verify(factor: factor.id) } }
                }
                HStack {
                    Button(L("Verify")) { if let factor { app.verify(factor: factor.id) } }
                        .buttonStyle(PrimaryButtonStyle()).disabled(factor == nil || app.busy)
                    Button(L("Cancel sign-in")) { app.cancelSignIn() }
                }
            default:
                Text(verbatim: L("Sign in with your Mixel ID. Your browser opens; come back here when it says you are signed in.")).foregroundStyle(.secondary)
                if account.phase == "error" { MessageLine(Translator.shared.translate(account.message)) }
                HStack {
                    Button(L("Sign in with Mixel ID")) { app.signIn() }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(app.busy)
                    if !app.isPhone {
                        Button(L("Use another browser")) { app.signIn(showLink: true) }.disabled(app.busy)
                    }
                }
                Button(L("Create Mixel ID")) {
                    if let url = URL(string: "https://auth.mixel.ch/if/flow/mixel-remote-enrollment/") { app.openURL(url) }
                }.linkButton()
            }
        }
    }

    private func profileCard(_ profile: Account) -> some View {
        Card {
            HStack(alignment: .top) {
                Image(systemName: "person.crop.circle.fill").font(.system(size: 40)).foregroundStyle(Theme.brand)
                VStack(alignment: .leading, spacing: 3) {
                    Text(verbatim: profile.displayName ?? L("Signed in")).font(.title3.weight(.semibold))
                    Text(verbatim: kind(profile)).foregroundStyle(.secondary)
                    if profile.remembered { Label(L("Stays signed in on this computer"), systemImage: "key.fill").font(.callout).foregroundStyle(.secondary) }
                }
                Spacer()
                Button(L("Sign out")) { app.signOut() }
            }
            if profile.hasPersonal, profile.hasCompany {
                Picker(selection: Binding(get: { profile.personal }, set: { personal in
                    app.act { try await app.engine.perform("account_workspace", ["personal": personal]); await app.refreshAccount() }
                })) {
                    Text(verbatim: L("Company devices")).tag(false)
                    Text(verbatim: L("Personal devices")).tag(true)
                } label: { Text(verbatim: L("Devices of")) }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 360)
            }
            if !profile.companies.isEmpty {
                Divider()
                Text(verbatim: L("Company administration")).font(.headline)
                ForEach(profile.companies) { item in
                    HStack {
                        Text(verbatim: item.companyName)
                        Spacer()
                        Button(L("Manage devices and company")) {
                            app.act {
                                let url: String = try await app.engine.run("company_settings_url", ["company_id": item.companyId])
                                if let link = URL(string: url) { app.openURL(link) }
                            }
                        }
                    }
                }
            }
        }
    }

    private func kind(_ profile: Account) -> String {
        if profile.staff { return L("Mixel team account") }
        if profile.personal { return L("Personal account") }
        if let tenant = company.tenant { return "\(tenant.name) · \(Format.role(tenant.role))" }
        return L("Mixel account")
    }

    @State private var deletingAccount = false
    private var dangerCard: some View {
        Card {
            CardTitle(L("Delete my account"), symbol: "person.crop.circle.badge.xmark")
            Text(verbatim: L("Deletes your Mixel Remote account and signs you out on every device. Company data stays with the company.")).foregroundStyle(.secondary).font(.callout)
            Button(L("Delete my account…"), role: .destructive) { deletingAccount = true }
        }
        .sheet(isPresented: $deletingAccount) {
            FormSheet(title: L("Delete my account"), text: L("Type your e-mail address to confirm. This cannot be undone."), fields: [.init(id: "email", label: L("E-mail"))], confirm: L("Delete my account"), danger: true) { values in
                await company.deleteAccount(confirmEmail: values["email"] ?? "") ? nil : company.error
            }
        }
    }
}

/// The company: plan, usage, members with roles, invitations.
struct CompanyCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var company: CompanyModel
    @State private var inviting = false
    @State private var renaming = false
    @State private var deleting = false

    var body: some View {
        Card {
            HStack(alignment: .firstTextBaseline) {
                CardTitle(company.tenant?.name ?? "", symbol: "building.2")
                Spacer()
                if company.administers {
                    Button(L("Rename company")) { renaming = true }.disabled(company.readOnly)
                }
            }
            HStack(spacing: 16) {
                Label(company.planLine, systemImage: "creditcard")
                if !company.usageLine.isEmpty { Label(company.usageLine, systemImage: "chart.bar") }
            }
            .font(.callout).foregroundStyle(.secondary)
            if company.readOnly {
                MessageLine(L("This company is read-only. You can still remove devices; adding devices and other changes need an active plan."), error: false)
            }
            Divider()
            HStack {
                Text(verbatim: L("Members")).font(.headline)
                Spacer()
                if company.administers { Button(L("Invite a person")) { inviting = true }.disabled(company.readOnly) }
            }
            ForEach(company.members) { member in
                HStack {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(verbatim: member.name.isEmpty ? member.email : member.name)
                        Text(verbatim: member.email).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    if company.administers, member.userId != app.account.account?.profile {
                        Menu {
                            ForEach(["owner", "admin", "technician", "member"], id: \.self) { role in
                                Button(Format.role(role)) { Task { await company.setRole(member, to: role) } }.disabled(role == member.role)
                            }
                            Divider()
                            Button(L("Remove from company"), role: .destructive) { Task { await company.remove(member) } }
                        } label: { Text(verbatim: Format.role(member.role)) }
                            .fixedSize()
                    } else {
                        Text(verbatim: Format.role(member.role)).foregroundStyle(.secondary)
                        if member.userId == app.account.account?.profile, !company.isOwner {
                            Button(L("Leave")) { Task { await company.remove(member) } }
                        }
                    }
                }
            }
            if company.administers, !company.invites.isEmpty {
                Divider()
                Text(verbatim: L("Open invitations")).font(.headline)
                ForEach(company.invites) { invite in
                    HStack {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(verbatim: invite.email)
                            Text(verbatim: "\(Format.role(invite.role)) · \(mailState(invite))").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(L("Send again")) { Task { await company.resend(invite) } }
                        Button(L("Revoke"), role: .destructive) { Task { await company.revoke(invite) } }
                    }
                }
            }
            MessageLine(company.notice, error: false)
            MessageLine(company.error)
            if company.isOwner {
                Divider()
                Text(verbatim: L("Data and deletion")).font(.headline)
                HStack {
                    Button(L("Export data in the portal")) {
                        app.act {
                            let url: String = try await app.engine.run("legal_url", ["page": "company-data", "lang": Translator.shared.language])
                            if let link = URL(string: url) { app.openURL(link) }
                        }
                    }
                    Button(L("Delete company…"), role: .destructive) { deleting = true }
                }
            }
        }
        .sheet(isPresented: $inviting) { InviteSheet(company: company) }
        .sheet(isPresented: $renaming) {
            FormSheet(title: L("Rename company"), fields: [.init(id: "name", label: L("Company name"), value: company.tenant?.name ?? "")], confirm: L("Rename")) { values in
                await company.rename(to: values["name"] ?? "") ? nil : company.error
            }
        }
        .sheet(isPresented: $deleting) {
            FormSheet(title: L("Delete company"), text: L("Deletes the company at once: devices, invitations, members and their accounts. The subscription ends. Type the company name to confirm."), fields: [.init(id: "name", label: L("Company name"))], confirm: L("Delete company"), danger: true) { values in
                await company.deleteCompany(confirmName: values["name"] ?? "") ? nil : company.error
            }
        }
    }

    func mailState(_ invite: Invite) -> String {
        switch invite.mailState {
        case "sent", "delivered": L("E-mail sent")
        case "queued", "sending": L("E-mail on its way")
        case "failed", "bounced": L("E-mail failed")
        default: L("Expires %@", Format.day(invite.expiresAt))
        }
    }
}

struct InviteSheet: View {
    @ObservedObject var company: CompanyModel
    @State private var email = ""
    @State private var role = "technician"
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(verbatim: L("Invite a person")).font(.title2.weight(.semibold))
            Text(verbatim: L("Mixel Remote e-mails the invitation link. Administrators and technicians count against the technicians of your plan.")).foregroundStyle(.secondary)
            FieldRow(L("E-mail")) {
                TextField(text: $email, prompt: Text(verbatim: "name@company.ch")) { Text(verbatim: L("E-mail")) }
                    .textFieldStyle(.roundedBorder)
                    #if os(iOS)
                    .keyboardType(.emailAddress).textInputAutocapitalization(.never)
                    #endif
            }
            Picker(selection: $role) {
                ForEach(["admin", "technician", "member"], id: \.self) { Text(verbatim: Format.role($0)).tag($0) }
            } label: { Text(verbatim: L("Role")) }
            MessageLine(company.error)
            HStack {
                Spacer()
                Button(L("Cancel")) { dismiss() }.keyboardShortcut(.cancelAction)
                Button(L("Send invitation")) { Task { if await company.invite(email: email, role: role) { dismiss() } } }
                    .buttonStyle(PrimaryButtonStyle()).keyboardShortcut(.defaultAction).disabled(company.working)
            }
        }
        .padding(22)
        .background(Theme.background)
        .frame(minWidth: 420)
    }
}

/// No company yet: create one (14-day Business trial) or a personal space.
struct CreateCompanyCard: View {
    @ObservedObject var app: AppModel
    @ObservedObject var company: CompanyModel
    @State private var name = ""
    @State private var terms = false
    @State private var personalName = ""

    var body: some View {
        Card {
            CardTitle(L("Create a company"), symbol: "building.2.crop.circle")
            Text(verbatim: L("Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team.")).foregroundStyle(.secondary)
            FieldRow(L("Company name")) { TextField(text: $name) { Text(verbatim: L("Company name")) }.textFieldStyle(.roundedBorder) }
            Toggle(isOn: $terms) { Text(verbatim: L("I accept the Terms of Service and the Data Processing Agreement")) }
            Button(L("Read the Terms of Service, the Data Processing Agreement and the Privacy Policy")) { app.settings.openLegal("legal") }.linkButton()
            Button(L("Create company")) { Task { _ = await company.create(name: name, acceptedTerms: terms) } }
                .buttonStyle(PrimaryButtonStyle()).disabled(company.working || name.isEmpty)
            if app.account.account?.hasPersonal == false {
                Divider()
                Text(verbatim: L("Or a personal space for your own devices")).font(.headline)
                HStack {
                    TextField(text: $personalName) { Text(verbatim: L("Name shown to your own devices")) }.textFieldStyle(.roundedBorder)
                    Button(L("Create personal space")) { Task { _ = await company.createPersonalSpace(name: personalName) } }.disabled(personalName.isEmpty)
                }
            }
            MessageLine(company.error)
        }
    }
}
