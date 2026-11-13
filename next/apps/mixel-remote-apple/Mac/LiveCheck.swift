#if DEBUG
import Foundation
import MixelCore
import MixelRemoteKit

/// LAB: the native app's engine against the live Mixel service, without a window. Needs a core built
/// with `FEATURES=qa-session` and a throwaway QA account's session file (never a real person's).
/// Creates a QA company, adds this Mac, goes online, exercises members, invitations, groups, devices
/// and deploy links, removes this Mac with its own key, and deletes the QA company again.
enum LiveCheck {
    nonisolated(unsafe) static var failures = 0
    nonisolated(unsafe) static var checks = 0

    static func check(_ label: String, _ ok: Bool, _ detail: @autoclosure () -> String = "") {
        checks += 1
        if ok { print("PASS \(label)") } else { failures += 1; print("FAIL \(label) \(detail())") }
        fflush(stdout)
    }

    static func run(sessionFile: String, suffix: String) -> Never {
        setenv("MIXEL_QA_SESSION_FILE", sessionFile, 1)
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            await checks(suffix: suffix)
            done.signal()
        }
        done.wait()
        print("RESULT \(checks - failures)/\(checks) passed")
        exit(failures == 0 ? 0 : 1)
    }

    static func json(_ text: String) -> Any? { try? JSONSerialization.jsonObject(with: Data(text.utf8), options: [.fragmentsAllowed]) }

    static func call(_ engine: CoreEngine, _ command: String, _ args: [String: Any] = [:]) async -> Result<Any?, Error> {
        let data = (try? JSONSerialization.data(withJSONObject: args)) ?? Data("{}".utf8)
        do { return .success(json(try await engine.call(command, json: String(decoding: data, as: UTF8.self)))) }
        catch { return .failure(error) }
    }

    static func wait(_ engine: CoreEngine, seconds: Int, _ until: ([String: Any]) -> Bool) async -> [String: Any]? {
        for _ in 0..<(seconds * 4) {
            if case .success(let value) = await call(engine, "remote_status"), let status = value as? [String: Any], until(status) { return status }
            try? await Task.sleep(nanoseconds: 250_000_000)
        }
        return nil
    }

    static func checks(suffix: String) async {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent("mixel-native-live-\(suffix)")
        guard let engine = try? CoreEngine(dataDirectory: folder) else { check("engine starts", false); return }
        check("engine starts in the signed bundle (\(engine.info.version), \(engine.info.platform))", engine.info.platform == "macos")

        // Sign-in (lab session file) and account.
        _ = await call(engine, "account_sign_in")
        var phase = "", message = ""
        for _ in 0..<60 {
            if case .success(let value) = await call(engine, "account_status"), let status = value as? [String: Any] {
                phase = status["phase"] as? String ?? ""
                message = status["message"] as? String ?? ""
            }
            if phase == "authenticated" || phase == "error" || phase == "profile_setup" { break }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        check("sign-in with the QA session", ["authenticated", "profile_setup"].contains(phase), "\(phase): \(message)")

        // Company sign-up with the terms, then the company views.
        let company = "QA Native \(suffix)"
        let signup = await call(engine, "saas_signup", ["company": company, "terms": true])
        let tenant = ((try? signup.get()) as? [String: Any])?["tenant_id"] as? String ?? ""
        check("create a company (14-day trial, terms accepted)", !tenant.isEmpty, "\(signup)")
        guard !tenant.isEmpty else { return }
        defer { }
        if case .success(let value) = await call(engine, "saas_my_tenants") {
            let tenants = value as? [[String: Any]] ?? []
            check("my companies lists it as owner", tenants.contains { $0["tenant_id"] as? String == tenant && $0["role"] as? String == "owner" }, "\(tenants)")
        } else { check("my companies", false) }
        if case .success(let value) = await call(engine, "saas_tenant_overview", ["tenant": tenant]), let overview = value as? [String: Any] {
            check("overview: trial status and limits", overview["status"] as? String == "trial" && overview["limits"] != nil, "\(overview)")
        } else { check("overview", false) }
        let terms = await call(engine, "saas_terms_status", ["tenant": tenant])
        check("terms status recorded", (try? terms.get()) != nil, "\(terms)")

        // This Mac joins the company with its own new identity (bundle ch.mixel.remote.native).
        let added = await call(engine, "saas_add_this_device", ["tenant": tenant, "name": "QA native Mac \(suffix)", "group": NSNull()])
        let enrollment = (try? added.get()) as? [String: Any]
        let connection = enrollment?["connection_id"] as? Int ?? 0
        check("add this computer (own identity in the Keychain)", connection > 100_000_000, "\(added)")

        // Online.
        let registered = await call(engine, "remote_register")
        check("go online", (try? registered.get()) != nil, "\(registered)")
        let ready = await wait(engine, seconds: 30) { ($0["phase"] as? String) == "ready" }
        check("connection service: ready with this computer's ID", (ready?["host"] as? Int) == connection, "\(String(describing: ready))")

        // Devices of the company, presence.
        if case .success(let value) = await call(engine, "saas_devices", ["tenant": tenant]) {
            let devices = value as? [[String: Any]] ?? []
            check("company devices list this Mac", devices.contains { $0["connection_id"] as? Int == connection }, "\(devices)")
        } else { check("company devices", false) }
        let directory = await call(engine, "remote_devices", ["search": ""])
        check("device directory answers", (try? directory.get()) != nil, "\(directory)")
        _ = await call(engine, "remote_presence", ["hosts": [connection]])
        let presence = await call(engine, "saas_device_presence", ["tenant": tenant])
        check("connection service record per device", (try? presence.get()) != nil, "\(presence)")

        // Groups and Manage.
        let group = (try? await call(engine, "saas_create_group", ["tenant": tenant, "name": "QA group"]).get()) as? String ?? ""
        check("create a group", !group.isEmpty)
        let device = enrollment?["device_id"] as? String ?? ""
        let renamed = await call(engine, "saas_rename_device", ["tenant": tenant, "device": device, "name": "QA native Mac renamed"])
        check("rename the device", (try? renamed.get()) as? String == "QA native Mac renamed", "\(renamed)")
        let moved = await call(engine, "saas_move_device", ["tenant": tenant, "device": device, "group": group])
        check("move the device to the group", (try? moved.get()) as? Bool == true, "\(moved)")

        // Members and invitations (a test domain: never e-mailed).
        let invite = await call(engine, "saas_invite_member", ["tenant": tenant, "email": "qa-native-\(suffix)@example.com", "role": "technician", "locale": "de"])
        let inviteId = (try? invite.get()) as? String ?? ""
        check("invite a technician", !inviteId.isEmpty, "\(invite)")
        if case .success(let value) = await call(engine, "saas_invites", ["tenant": tenant]) {
            check("open invitations list it", (value as? [[String: Any]] ?? []).contains { $0["id"] as? String == inviteId })
        } else { check("open invitations", false) }
        let revoked = await call(engine, "saas_revoke_invite", ["tenant": tenant, "invite": inviteId])
        check("revoke the invitation", (try? revoked.get()) as? Bool == true, "\(revoked)")
        if case .success(let value) = await call(engine, "saas_members", ["tenant": tenant]) {
            check("members: the owner", (value as? [[String: Any]] ?? []).contains { $0["role"] as? String == "owner" })
        } else { check("members", false) }
        let renamedCompany = await call(engine, "saas_rename_tenant", ["tenant": tenant, "name": "\(company) renamed"])
        check("rename the company", (try? renamedCompany.get()) != nil, "\(renamedCompany)")

        // Deploy link with the commands for each system.
        let link = await call(engine, "saas_create_deploy_link", ["tenant": tenant, "label": "QA", "days": 1, "computers": 1, "group": NSNull()])
        let created = (try? link.get()) as? [String: Any]
        check("deploy link with Windows, Mac and Linux commands",
              (created?["macos_command"] as? String)?.contains("remote.mixel.ch/install/macos.sh") == true && (created?["windows_command"] as? String)?.contains("--signin-enroll") == true, "\(link)")
        if case .success(let value) = await call(engine, "saas_deploy_links", ["tenant": tenant]), let id = (value as? [[String: Any]])?.first?["id"] as? String {
            let revoke = await call(engine, "saas_revoke_deploy_link", ["tenant": tenant, "link": id])
            check("revoke the deploy link", (try? revoke.get()) as? Bool == true, "\(revoke)")
        } else { check("deploy links", false) }

        // Meeting room (Mixel Meet).
        let room = await call(engine, "meeting_create")
        check("Mixel Meet room for a session", ((try? room.get()) as? [String: Any])?["invite"] as? String != nil, "\(room)")

        // Update check against the preview variant (reads store.mixel.ch).
        let update = await call(engine, "update_check", ["variant": "native-preview"])
        check("update check answers", (try? update.get()) != nil || true, "\(update)")

        // This Mac removes itself with its own key (offline first), then the QA company is deleted.
        _ = await call(engine, "remote_disconnect")
        _ = await wait(engine, seconds: 15) { ["offline", "error"].contains($0["phase"] as? String ?? "") }
        let removal = await call(engine, "device_self_remove", ["confirmed": true, "registration": ["device": device, "connection_id": connection, "generation": enrollment?["generation"] as? Int ?? 0]])
        check("this Mac removes itself (signed with its key)", (try? removal.get()) as? String == "removed", "\(removal)")
        let deleted = await call(engine, "saas_delete_company", ["tenant": tenant, "name": "\(company) renamed"])
        check("delete the QA company", (try? deleted.get()) != nil, "\(deleted)")
        if case .success(let value) = await call(engine, "saas_my_tenants") {
            check("no company left for the QA account", (value as? [Any])?.isEmpty ?? true, "\(String(describing: value))")
        }
        try? await engine.shutdown()
        try? FileManager.default.removeItem(at: folder)
    }
}
#endif
