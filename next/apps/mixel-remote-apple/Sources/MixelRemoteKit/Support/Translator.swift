import Foundation

/// Mixel Remote in English, German, French or Italian: the computer's language, or the one chosen in
/// Settings. The screens use the String Catalog; texts that come from the engine (status lines, error
/// messages) are translated here with the same tables as the desktop app (`frontends/desktop/i18n-strings.ts`,
/// bundled as `engine-texts.json`). Device names, chat messages and typed values are never translated.
public final class Translator: @unchecked Sendable {
    public static let shared = Translator()
    public static let supported = ["en", "de", "fr", "it", "es", "pt", "nl"]
    private static let key = "mixel.remote.language.v1"

    private struct Table: Decodable {
        var texts: [String: String]
        var patterns: [[String]]
    }
    private var tables: [String: Table] = [:]
    private var compiled: [String: [(NSRegularExpression, String)]] = [:]
    private var cachedChoice: String?
    private var cachedLanguage: String?
    private var cachedLocale: Locale?
    private var cachedBundles: [String: Bundle?] = [:]
    private let lock = NSLock()

    init() {
        if let url = Bundle.module.url(forResource: "engine-texts", withExtension: "json"),
           let data = try? Data(contentsOf: url),
           let tables = try? JSONDecoder().decode([String: Table].self, from: data) {
            self.tables = tables
        }
    }

    /// `auto`, `en`, `de`, `fr` or `it`.
    public var choice: String {
        get {
            lock.withLock {
                if let cachedChoice { return cachedChoice }
                let value = UserDefaults.standard.string(forKey: Self.key) ?? "auto"
                cachedChoice = value
                return value
            }
        }
        set {
            if newValue == "auto" { UserDefaults.standard.removeObject(forKey: Self.key) }
            else { UserDefaults.standard.set(newValue, forKey: Self.key) }
            lock.withLock {
                cachedChoice = newValue
                cachedLanguage = nil
                cachedLocale = nil
            }
        }
    }

    /// The language shown now.
    public var language: String {
        lock.withLock {
            if let cachedLanguage { return cachedLanguage }
            let chosen = cachedChoice ?? (UserDefaults.standard.string(forKey: Self.key) ?? "auto")
            cachedChoice = chosen
            let resolved: String
            if chosen != "auto", Self.supported.contains(chosen) {
                resolved = chosen
            } else {
                let system = Locale.preferredLanguages.first.map { String($0.prefix(2)).lowercased() } ?? "en"
                resolved = Self.supported.contains(system) ? system : "en"
            }
            cachedLanguage = resolved
            cachedLocale = Locale(identifier: resolved)
            return resolved
        }
    }

    public var locale: Locale {
        if let hit = lock.withLock({ cachedLocale }) { return hit }
        return Locale(identifier: language)
    }

    /// An engine text in the shown language (unknown texts stay English).
    public func translate(_ text: String) -> String {
        translate(text, into: language)
    }

    public func translate(_ text: String, into language: String) -> String {
        let core = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard language != "en", !core.isEmpty, core.count <= 600, let table = tables[language] else { return text }
        if let hit = table.texts[core] { return text.replacingOccurrences(of: core, with: hit) }
        for (pattern, target) in patterns(language, table) {
            let range = NSRange(core.startIndex..., in: core)
            if pattern.firstMatch(in: core, range: range) != nil {
                // The tables use JavaScript replacement syntax ($1); NSRegularExpression uses the same.
                let replaced = pattern.stringByReplacingMatches(in: core, range: range, withTemplate: target)
                return text.replacingOccurrences(of: core, with: replaced)
            }
        }
        return text
    }

    private func patterns(_ language: String, _ table: Table) -> [(NSRegularExpression, String)] {
        lock.withLock {
            if let ready = compiled[language] { return ready }
            let ready = table.patterns.compactMap { pair -> (NSRegularExpression, String)? in
                guard pair.count == 2, let expression = try? NSRegularExpression(pattern: pair[0]) else { return nil }
                return (expression, pair[1])
            }
            compiled[language] = ready
            return ready
        }
    }
}

/// A screen text in the shown language: the String Catalog (`Localizable.xcstrings`) first, then the
/// desktop app's tables, else English. `%@` and `%d` placeholders are filled from `args`.
public func L(_ key: String, _ args: CVarArg...) -> String {
    let text = Translator.shared.screenText(key)
    return args.isEmpty ? text : String(format: text, locale: Translator.shared.locale, arguments: args)
}

extension Translator {
    private func lprojBundle(for language: String) -> Bundle? {
        lock.withLock {
            if let hit = cachedBundles[language] { return hit }
            let bundle = Bundle.module.path(forResource: language, ofType: "lproj").flatMap(Bundle.init(path:))
            cachedBundles[language] = bundle
            return bundle
        }
    }

    func screenText(_ key: String) -> String {
        let language = self.language
        if language != "en", let bundle = lprojBundle(for: language) {
            let missing = "\u{1}"
            let value = bundle.localizedString(forKey: key, value: missing, table: nil)
            if value != missing { return value }
        }
        return translate(key, into: language)
    }
}
