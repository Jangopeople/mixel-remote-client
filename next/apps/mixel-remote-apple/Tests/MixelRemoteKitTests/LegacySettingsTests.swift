import SQLite3
import XCTest
@testable import MixelRemoteKit

/// The desktop app 0.1.x settings (WebKit local storage) carried over to 2.0.
final class LegacySettingsTests: XCTestCase {
    /// A local storage database as WebKit writes it: UTF-16 values.
    func makeDatabase(_ items: [String: String], at url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        XCTAssertEqual(sqlite3_open(url.path, &handle), SQLITE_OK)
        defer { sqlite3_close(handle) }
        XCTAssertEqual(sqlite3_exec(handle, "CREATE TABLE ItemTable (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB NOT NULL ON CONFLICT FAIL)", nil, nil, nil), SQLITE_OK)
        for (key, value) in items {
            var statement: OpaquePointer?
            sqlite3_prepare_v2(handle, "INSERT INTO ItemTable VALUES (?, ?)", -1, &statement, nil)
            let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
            sqlite3_bind_text(statement, 1, key, -1, transient)
            let data = value.data(using: .utf16LittleEndian)!
            _ = data.withUnsafeBytes { sqlite3_bind_blob(statement, 2, $0.baseAddress, Int32(data.count), transient) }
            XCTAssertEqual(sqlite3_step(statement), SQLITE_DONE)
            sqlite3_finalize(statement)
        }
    }

    func testTheDesktopAppsSettingsAreCarriedOverOnce() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent("legacy-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: home) }
        let database = home.appendingPathComponent("Library/WebKit/ch.mixel.remote.next.qa/WebsiteData/Default/abc/abc/LocalStorage/localstorage.sqlite3")
        try makeDatabase([
            "mixel.remote.language.v1": "fr",
            "mixel.remote.autoupdate.v1": "off",
            "mixel.remote.unattended.v1": #"{"source":"display:1","control":true}"#,
            "mixel.remote.this-computer.v1": #"{"device":"7b0c5d1e-2f43-4c1a-9d55-0f6f2b9a1c33","connection_id":482913557,"generation":3,"tenant":"t-1"}"#,
            "mixel.remote.macs.v1": #"{"731204118":["aa:bb:cc:dd:ee:ff"]}"#,
            "mixel.remote.ai.v1": #"{"address":"","model":""}"#,
            "other.app": "x",
        ], at: database)
        XCTAssertEqual(LegacySettings.databases(bundle: "ch.mixel.remote.next.qa", home: home).map { $0.resolvingSymlinksInPath().path }, [database.resolvingSymlinksInPath().path])
        let items = LegacySettings.read(database)
        XCTAssertEqual(items["mixel.remote.language.v1"], "fr")
        XCTAssertNil(items["other.app"])

        let suite = "legacy-test-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("de", forKey: "mixel.remote.language.v1")  // set in 2.0 already: kept
        LegacySettings.apply(items, to: defaults)
        XCTAssertEqual(defaults.string(forKey: "mixel.remote.language.v1"), "de")
        XCTAssertEqual(defaults.string(forKey: "mixel.remote.autoupdate.v1"), "off")
        let unattended = try XCTUnwrap(defaults.dictionary(forKey: "mixel.remote.unattended.v1"))
        XCTAssertEqual(unattended["source"] as? String, "display:1")
        XCTAssertEqual(unattended["control"] as? Bool, true)
        // The registration in the format of this app (EnrollmentModel.Registration).
        let data = try XCTUnwrap(defaults.data(forKey: "mixel.remote.this-computer.v1"))
        let registration = try JSONDecoder().decode(EnrollmentModel.Registration.self, from: data)
        XCTAssertEqual(registration.connectionId, 482_913_557)
        XCTAssertEqual(registration.generation, 3)
        XCTAssertEqual(registration.tenant, "t-1")
        XCTAssertEqual(defaults.dictionary(forKey: "mixel.remote.macs.v1") as? [String: [String]], ["731204118": ["aa:bb:cc:dd:ee:ff"]])
    }

    func testBrokenOrMissingValuesAreIgnored() {
        let suite = "legacy-test-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        LegacySettings.apply([
            "mixel.remote.language.v1": "xx",
            "mixel.remote.unattended.v1": "not json",
            "mixel.remote.this-computer.v1": #"{"device":"d","connection_id":-4}"#,
        ], to: defaults)
        XCTAssertNil(defaults.object(forKey: "mixel.remote.language.v1"))
        XCTAssertNil(defaults.object(forKey: "mixel.remote.unattended.v1"))
        XCTAssertNil(defaults.object(forKey: "mixel.remote.this-computer.v1"))
        XCTAssertTrue(LegacySettings.databases(bundle: "ch.mixel.remote.none", home: URL(fileURLWithPath: "/nonexistent")).isEmpty)
    }
}
