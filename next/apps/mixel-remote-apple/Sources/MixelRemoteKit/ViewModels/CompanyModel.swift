import Foundation

/// The signed-in person's company (Mixel Remote SaaS tenant): plan, trial and usage, members and
/// their roles, invitations, renaming, creating a company, and deleting the company or one's account.
@MainActor
public final class CompanyModel: ObservableObject {
    // The app model lives as long as the app; its parts keep it (no reference cycle matters there).
    let app: AppModel
    @Published public private(set) var tenants: [Tenant] = []
    @Published public private(set) var overview: TenantOverview?
    @Published public private(set) var members: [Member] = []
    @Published public private(set) var invites: [Invite] = []
    @Published public private(set) var loaded = false
    @Published public var error = ""
    @Published public var notice = ""
    @Published public private(set) var working = false

    init(app: AppModel) { self.app = app }

    public var tenant: Tenant? { tenants.first }
    public var role: String { overview?.role ?? tenant?.role ?? "" }
    public var administers: Bool { ["owner", "admin"].contains(role) }
    public var isOwner: Bool { role == "owner" }
    public var readOnly: Bool { !["active", "trial", "past_due"].contains(overview?.status ?? tenant?.status ?? "active") }

    func reset() {
        tenants = []
        overview = nil
        members = []
        invites = []
        loaded = false
        error = ""
        notice = ""
    }

    public func load() async {
        guard app.signedIn else { reset(); return }
        do {
            tenants = try await app.engine.run("saas_my_tenants")
            await loadOverview()
            await loadPeople()
        } catch {
            // Mixel team accounts, ISM companies and personal spaces have no Remote company.
            tenants = []
        }
        loaded = true
    }

    func loadOverview() async {
        guard let tenant else { overview = nil; return }
        overview = try? await app.engine.run("saas_tenant_overview", ["tenant": tenant.tenantId])
    }

    func loadPeople() async {
        guard let tenant else { return }
        members = (try? await app.engine.run("saas_members", ["tenant": tenant.tenantId])) ?? []
        invites = administers ? ((try? await app.engine.run("saas_invites", ["tenant": tenant.tenantId])) ?? []) : []
    }

    /// "Trial until 27 Nov" and similar.
    public var planLine: String {
        guard let tenant else { return "" }
        let plan = Format.plan(overview?.plan ?? tenant.plan)
        switch overview?.status ?? tenant.status {
        case "trial": return L("%@ trial until %@", plan, Format.day(overview?.trialEndsAt ?? tenant.trialEndsAt))
        case "read_only", "expired", "canceled": return L("%@ · read-only", plan)
        default: return plan
        }
    }

    public var usageLine: String {
        guard let usage = overview?.usage else { return "" }
        let devices = overview?.limits?.devices.map { L("%d of %d devices", usage.devices, $0) } ?? L("%d devices", usage.devices)
        let technicians = overview?.limits?.technicians.map { L("%d of %d technicians", usage.technicians, $0) } ?? L("%d technicians", usage.technicians)
        return "\(devices) · \(technicians)"
    }

    public func invite(email: String, role: String) async -> Bool {
        guard let tenant else { return false }
        let email = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard email.contains("@"), email.count <= 254 else { error = L("Enter an e-mail address"); return false }
        return await change {
            _ = try await self.app.engine.run("saas_invite_member", ["tenant": tenant.tenantId, "email": email, "role": role, "locale": Translator.shared.language], as: String.self)
            self.notice = L("Invitation sent to %@.", email)
        }
    }

    public func resend(_ invite: Invite) async {
        guard let tenant else { return }
        _ = await change {
            try await self.app.engine.perform("saas_resend_invite", ["tenant": tenant.tenantId, "invite": invite.id, "locale": Translator.shared.language])
            self.notice = L("Invitation sent again to %@.", invite.email)
        }
    }

    public func revoke(_ invite: Invite) async {
        guard let tenant else { return }
        _ = await change { try await self.app.engine.perform("saas_revoke_invite", ["tenant": tenant.tenantId, "invite": invite.id]) }
    }

    public func setRole(_ member: Member, to role: String) async {
        guard let tenant else { return }
        _ = await change { try await self.app.engine.perform("saas_set_member_role", ["tenant": tenant.tenantId, "member": member.userId, "role": role]) }
    }

    /// Removes a member; for oneself: leaves the company.
    public func remove(_ member: Member) async {
        guard let tenant else { return }
        _ = await change { try await self.app.engine.perform("saas_remove_member", ["tenant": tenant.tenantId, "member": member.userId]) }
        if member.userId == app.account.account?.profile { await app.refreshAccount(); await load() }
    }

    public func rename(to name: String) async -> Bool {
        guard let tenant else { return false }
        return await change { _ = try await self.app.engine.run("saas_rename_tenant", ["tenant": tenant.tenantId, "name": name], as: String.self) }
    }

    /// Creates a company with a 14-day Business trial; this account becomes its owner.
    public func create(name: String, acceptedTerms: Bool) async -> Bool {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard acceptedTerms else { error = L("Accept the Terms of Service and the Data Processing Agreement to create a company."); return false }
        guard !name.isEmpty else { error = L("Enter a company name"); return false }
        let done = await change {
            if !self.app.remote.disconnected { try await self.app.engine.perform("remote_disconnect") }
            let _: SignupResult = try await self.app.engine.run("saas_signup", ["company": name, "terms": true])
        }
        if done { await app.refreshAccount(); await load() }
        return done
    }

    public func deleteCompany(confirmName: String) async -> Bool {
        guard let tenant else { return false }
        let done = await change { try await self.app.engine.perform("saas_delete_company", ["tenant": tenant.tenantId, "name": confirmName]) }
        if done { await app.refreshAccount(); await load() }
        return done
    }

    public func deleteAccount(confirmEmail: String) async -> Bool {
        let done = await change { try await self.app.engine.perform("saas_delete_my_account", ["email": confirmEmail]) }
        if done { app.signOut() }
        return done
    }

    public func createPersonalSpace(name: String) async -> Bool {
        let done = await change {
            if !self.app.remote.disconnected { try await self.app.engine.perform("remote_disconnect") }
            try await self.app.engine.perform("account_create_personal", ["name": name])
        }
        if done { await app.refreshAccount() }
        return done
    }

    private func change(_ work: @escaping () async throws -> Void) async -> Bool {
        error = ""
        notice = ""
        working = true
        defer { working = false }
        do {
            try await work()
            await loadOverview()
            await loadPeople()
            return true
        } catch {
            self.error = AppModel.message(error)
            return false
        }
    }
}
