import Foundation
import SQLite3

/// Mixel Remote 2.0 replaces the desktop app 0.1.x in place (same bundle identifier, same Keychain
/// items, launch agents and data folder). Its few window settings lived in the web view's local
/// storage; they are carried over once into this app's settings, under the same names:
/// language, automatic updates, unattended access (screen and control), this computer's
/// registration and the network addresses learned for Wake-on-LAN.
public enum LegacySettings {
    static let doneKey = "mixel.remote.legacy-settings-migrated.v1"

    /// The local storage databases of the desktop app (WebKit, per origin).
    static func databases(bundle: String, home: URL = URL(fileURLWithPath: NSHomeDirectory())) -> [URL] {
        let root = home.appendingPathComponent("Library/WebKit/\(bundle)/WebsiteData", isDirectory: true)
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.isRegularFileKey]) else { return [] }
        var found: [URL] = []
        for case let url as URL in walker where url.lastPathComponent == "localstorage.sqlite3" { found.append(url) }
        return found.sorted { $0.path < $1.path }
    }

    /// Reads the `mixel.remote.*` items of one local storage database (values are UTF-16 text).
    static func read(_ database: URL) -> [String: String] {
        var handle: OpaquePointer?
        guard sqlite3_open_v2(database.path, &handle, SQLITE_OPEN_READONLY, nil) == SQLITE_OK, let handle else { return [:] }
        defer { sqlite3_close(handle) }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(handle, "SELECT key, value FROM ItemTable WHERE key LIKE 'mixel.remote.%'", -1, &statement, nil) == SQLITE_OK else { return [:] }
        defer { sqlite3_finalize(statement) }
        var items: [String: String] = [:]
        while sqlite3_step(statement) == SQLITE_ROW {
            guard let key = sqlite3_column_text(statement, 0), let blob = sqlite3_column_blob(statement, 1) else { continue }
            let size = Int(sqlite3_column_bytes(statement, 1))
            guard size > 0, size <= 64 * 1024 else { continue }
            let data = Data(bytes: blob, count: size)
            if let text = String(data: data, encoding: .utf16LittleEndian) { items[String(cString: key)] = text }
        }
        return items
    }

    /// Applies the old settings to `defaults` (never overwriting a value this app already has).
    static func apply(_ items: [String: String], to defaults: UserDefaults) {
        func absent(_ key: String) -> Bool { defaults.object(forKey: key) == nil }
        if let language = items["mixel.remote.language.v1"], ["en", "de", "fr", "it"].contains(language), absent("mixel.remote.language.v1") {
            defaults.set(language, forKey: "mixel.remote.language.v1")
        }
        if let auto = items["mixel.remote.autoupdate.v1"], ["on", "off"].contains(auto), absent("mixel.remote.autoupdate.v1") {
            defaults.set(auto, forKey: "mixel.remote.autoupdate.v1")
        }
        if let raw = items["mixel.remote.unattended.v1"], absent("mixel.remote.unattended.v1"),
           let value = object(raw), let source = value["source"] as? String, !source.isEmpty, source.count <= 200,
           let control = value["control"] as? Bool {
            defaults.set(["source": source, "control": control], forKey: "mixel.remote.unattended.v1")
        }
        if let raw = items["mixel.remote.this-computer.v1"], absent("mixel.remote.this-computer.v1"),
           let value = object(raw), let device = value["device"] as? String, let connection = value["connection_id"] as? Int,
           connection > 0, connection <= Int(UInt32.max) {
            var registration: [String: Any] = ["device": device, "connectionId": connection, "generation": value["generation"] as? Int ?? 0]
            if let tenant = value["tenant"] as? String { registration["tenant"] = tenant }
            if let data = try? JSONSerialization.data(withJSONObject: registration) { defaults.set(data, forKey: "mixel.remote.this-computer.v1") }
        }
        if let raw = items["mixel.remote.macs.v1"], absent("mixel.remote.macs.v1"),
           let value = object(raw) as? [String: [String]] {
            let valid = value.filter { UInt32($0.key) != nil }.mapValues { Array($0.prefix(4)) }
            if !valid.isEmpty { defaults.set(valid, forKey: "mixel.remote.macs.v1") }
        }
    }

    static func object(_ text: String) -> [String: Any]? {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any]
    }

    /// Once per Mac account, at the first start of 2.0.
    public static func migrateOnce(bundle: String, defaults: UserDefaults = .standard) {
        guard !defaults.bool(forKey: doneKey) else { return }
        var items: [String: String] = [:]
        for database in databases(bundle: bundle) { items.merge(read(database)) { current, _ in current } }
        apply(items, to: defaults)
        defaults.set(true, forKey: doneKey)
    }
}
