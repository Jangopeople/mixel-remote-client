#if DEBUG
import AppKit
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

    static func run(sessionFile: String, suffix: String, sessionFolder: String? = nil, expectedId: UInt32? = nil) -> Never {
        setenv("MIXEL_QA_SESSION_FILE", sessionFile, 1)
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            if let expectedId { await identity(expected: expectedId) }
            else if let sessionFolder { await session(suffix: suffix, folder: URL(fileURLWithPath: sessionFolder)) }
            else { await checks(suffix: suffix) }
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

    static func signIn(_ engine: CoreEngine) async -> Bool {
        _ = await call(engine, "account_sign_in")
        for _ in 0..<60 {
            if case .success(let value) = await call(engine, "account_status"), let status = value as? [String: Any],
               ["authenticated", "profile_setup", "error"].contains(status["phase"] as? String ?? "") {
                return status["phase"] as? String != "error"
            }
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
        return false
    }

    /// A real session: this Mac (technician) to a lab computer enrolled with the deploy link written to
    /// `folder/deploy-command.txt`. Waits for the computer, connects unattended, receives pictures, sends
    /// harmless input (pointer moves, Shift), then ends and removes everything it created.
    static func session(suffix: String, folder: URL) async {
        guard let engine = try? CoreEngine(dataDirectory: folder.appendingPathComponent("engine")) else { check("engine starts", false); return }
        check("sign-in with the QA session", await signIn(engine))
        let company = "QA Native \(suffix)"
        let tenant = ((try? await call(engine, "saas_signup", ["company": company, "terms": true]).get()) as? [String: Any])?["tenant_id"] as? String ?? ""
        check("QA company", !tenant.isEmpty)
        guard !tenant.isEmpty else { return }
        let added = (try? await call(engine, "saas_add_this_device", ["tenant": tenant, "name": "QA native Mac \(suffix)", "group": NSNull()]).get()) as? [String: Any]
        let mine = added?["connection_id"] as? Int ?? 0
        check("this Mac added", mine > 0)
        _ = await call(engine, "remote_register")
        check("this Mac online", await wait(engine, seconds: 30) { ($0["phase"] as? String) == "ready" } != nil)
        let link = (try? await call(engine, "saas_create_deploy_link", ["tenant": tenant, "label": "QA lab", "days": 1, "computers": 1, "group": NSNull()]).get()) as? [String: Any]
        let command = link?["windows_command"] as? String ?? ""
        check("deploy link for the lab computer", !command.isEmpty)
        try? command.write(to: folder.appendingPathComponent("deploy-command.txt"), atomically: true, encoding: .utf8)
        print("WAITING for the lab computer (deploy-command.txt)"); fflush(stdout)
        var host = 0
        for _ in 0..<180 {
            if let rows = (try? await call(engine, "saas_devices", ["tenant": tenant]).get()) as? [[String: Any]],
               let row = rows.first(where: { ($0["os"] as? String) == "windows" }), let id = row["connection_id"] as? Int {
                _ = await call(engine, "remote_presence", ["hosts": [id]])
                if let status = (try? await call(engine, "remote_status").get()) as? [String: Any], (status["online"] as? [Int] ?? []).contains(id) { host = id; break }
            }
            try? await Task.sleep(nanoseconds: 2_000_000_000)
        }
        check("lab computer enrolled with the deploy link and online", host > 0)
        if host > 0 {
            let request = await call(engine, "remote_request", ["host": host, "password": NSNull(), "unattended": true])
            check("unattended request", (try? request.get()) != nil, "\(request)")
            let active = await wait(engine, seconds: 60) { ($0["phase"] as? String) == "active" }
            let after = try? await call(engine, "remote_status").get()
            check("session active", active != nil, "\(String(describing: after))")
            if let active, let id = active["session"] as? String {
                check("view and control granted", active["control"] as? Bool == true, "\(active)")
                print("host actions: \(active["host_actions"] ?? "")  displays: \(active["displays"] ?? "")  features: \(active["peer_features"] ?? "")")
                var frames = 0, sequence: UInt64 = 0, size = ""
                var start = Date()
                var step = 0
                while Date().timeIntervalSince(start) < 20, frames < 40 {
                    // Pointer moves on the remote screen change the picture (pictures follow changes only).
                    step += 1
                    if step % 10 == 0 { _ = try? await engine.input(session: id, json: "{\"kind\":\"pointer\",\"x\":\((step * 997) % 60000 + 2000),\"y\":\((step * 431) % 50000 + 5000)}") }
                    guard let data = try? await engine.nextVideo(session: id, compressed: false) else { break }
                    if data.isEmpty { try? await Task.sleep(nanoseconds: 20_000_000); continue }
                    if let frame = try? VideoFrame.decode(data, after: sequence) {
                        sequence = frame.sequence; frames += 1; size = "\(frame.width)×\(frame.height)"
                        if frames == 1 || frames == 10 { save(frame.image, folder.appendingPathComponent("remote-screen.png")) }
                    }
                }
                var seconds = Date().timeIntervalSince(start)
                check("pictures from the lab computer, decoded by the engine (\(frames) in \(Int(seconds)) s, \(size); a still screen sends pictures only on change)", frames >= 2)
                // The same session with the pictures decoded on this Mac (VideoToolbox, as the app does).
                let decoder = H264Decoder()
                var decodedHere = 0, packets = 0, failure = ""
                start = Date()
                while Date().timeIntervalSince(start) < 20, decodedHere < 40 {
                    step += 1
                    if step % 10 == 0 { _ = try? await engine.input(session: id, json: "{\"kind\":\"pointer\",\"x\":\((step * 613) % 60000 + 2000),\"y\":\((step * 271) % 50000 + 5000)}") }
                    guard let data = try? await engine.nextVideo(session: id, compressed: true) else { break }
                    if data.isEmpty { try? await Task.sleep(nanoseconds: 20_000_000); continue }
                    guard VideoFrame.isCompressed(data) else { continue }
                    packets += 1
                    do {
                        let frame = try decoder.decode(data)
                        decodedHere += 1; size = "\(frame.width)×\(frame.height)"
                        if decodedHere == 5 { save(frame.image, folder.appendingPathComponent("remote-screen-h264.png")) }
                    } catch H264Decoder.Problem.waitingForKeyframe { continue } catch { failure = "\(error)"; break }
                }
                seconds = Date().timeIntervalSince(start)
                check("pictures decoded on this Mac with VideoToolbox (\(decodedHere) of \(packets) H.264 packets in \(Int(seconds)) s, \(size))", decodedHere >= 2 && failure.isEmpty, failure)
                _ = try? await engine.nextVideo(session: id, compressed: false)
                var posted = 0
                for step in 0..<10 {
                    let event = "{\"kind\":\"pointer\",\"x\":\(20000 + step * 2000),\"y\":30000}"
                    if (try? await engine.input(session: id, json: event)) == true { posted += 1 }
                }
                for down in [true, false] { if (try? await engine.input(session: id, json: "{\"kind\":\"key\",\"usage\":225,\"down\":\(down)}")) == true { posted += 1 } }
                check("input applied on the lab computer (\(posted)/12)", posted == 12)
                let chat = await call(engine, "remote_chat_messages", ["session": id])
                print("chat available: \((try? chat.get()) != nil)")
                if let consent = active["consent"] as? String {
                    let stop = await call(engine, "remote_stop_session", ["consent": consent])
                    check("end session", (try? stop.get()) != nil, "\(stop)")
                }
                check("back to ready after the session", await wait(engine, seconds: 20) { ($0["phase"] as? String) == "ready" } != nil)
            }
        }
        _ = await call(engine, "remote_disconnect")
        _ = await wait(engine, seconds: 15) { ["offline", "error"].contains($0["phase"] as? String ?? "") }
        if let device = added?["device_id"] as? String {
            let removal = await call(engine, "device_self_remove", ["confirmed": true, "registration": ["device": device, "connection_id": mine, "generation": added?["generation"] as? Int ?? 0]])
            check("this Mac removes itself", (try? removal.get()) as? String == "removed", "\(removal)")
        }
        let deleted = await call(engine, "saas_delete_company", ["tenant": tenant, "name": company])
        check("QA company deleted (the lab computer's enrolment with it)", (try? deleted.get()) != nil, "\(deleted)")
        try? await engine.shutdown()
    }

    /// The in-place update 0.1.22 → 2.0: run by the updated app on a Mac that 0.1.22 enrolled
    /// (`--signin-enroll`). The device key from the Keychain, the data folder and the Remote ID carry
    /// over; the Mac then removes itself with that key (which proves the server still knows it).
    static func identity(expected: UInt32) async {
        guard let engine = try? CoreEngine(dataDirectory: CoreEngine.defaultDirectory()) else { check("engine starts", false); return }
        check("updated app runs as \(engine.info.version) from \(Bundle.main.bundlePath)", engine.info.version == "2.0.0")
        let key = await call(engine, "open_device_identity", ["create": false])
        let identity = (try? key.get()) as? [String: Any]
        check("the device key created by 0.1.22 is read from the Keychain (no new key)", identity?["public_key"] as? String != nil, "\(key)")
        check("sign-in with the QA session", await signIn(engine))
        let registered = await call(engine, "remote_register")
        let ready = await wait(engine, seconds: 30) { ($0["phase"] as? String) == "ready" }
        check("online with the Remote ID enrolled by 0.1.22 (\(Format.remoteId(expected)))", (ready?["host"] as? Int) == Int(expected), "\(registered) \(String(describing: ready))")
        _ = await call(engine, "remote_disconnect")
        _ = await wait(engine, seconds: 15) { ["offline", "error"].contains($0["phase"] as? String ?? "") }
        let removal = await call(engine, "device_self_remove", ["confirmed": true])
        check("this Mac removes itself, signed with the carried-over key", (try? removal.get()) as? String == "removed", "\(removal)")
        try? await engine.shutdown()
    }

    static func save(_ image: CGImage, _ url: URL) {
        let rep = NSBitmapImageRep(cgImage: image)
        try? rep.representation(using: .png, properties: [:])?.write(to: url)
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

        // This Mac joins the company with its own new identity (lab bundle ch.mixel.remote.labtest).
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

        // Update check on the signed update channel (lab builds read it only with a test channel set).
        let update = await call(engine, "update_check")
        check("update check answers (lab build without a test channel: no update offered)", ((try? update.get()) ?? nil) == nil, "\(update)")
        // Sign-in screen access: this build carries no entitled host, so it waits for Apple.
        let signin = (try? await call(engine, "signin_screen_status").get()) as? [String: Any]
        check("sign-in screen access waits for Apple's virtual keyboard entitlement", signin?["availability"] as? String == "waiting_for_apple", "\(String(describing: signin))")

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
