import Foundation
import Security

/// What the app and its screen-broadcast extension share: this device's key and its registration.
/// Kept in the keychain (both belong to the same keychain group), never in plain files.
enum Shared {
    private static let service = "ch.mixel.quicksupport"
    private static let group = "5277F8NDH4.ch.mixel.quicksupport.shared"

    private static func query(_ name: String) -> [CFString: Any] {
        [kSecClass: kSecClassGenericPassword, kSecAttrService: service, kSecAttrAccount: name, kSecAttrAccessGroup: group]
    }
    static func data(_ name: String) -> Data? {
        var found: CFTypeRef?
        var request = query(name)
        request[kSecReturnData] = true
        request[kSecMatchLimit] = kSecMatchLimitOne
        return SecItemCopyMatching(request as CFDictionary, &found) == errSecSuccess ? found as? Data : nil
    }
    static func text(_ name: String) -> String? { data(name).flatMap { String(data: $0, encoding: .utf8) } }
    @discardableResult static func set(_ name: String, _ value: Data?) -> Bool {
        SecItemDelete(query(name) as CFDictionary)
        guard let value else { return true }
        var item = query(name)
        item[kSecValueData] = value
        // The broadcast may start while the phone was locked since the code was typed.
        item[kSecAttrAccessible] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }
    @discardableResult static func set(_ name: String, _ value: String?) -> Bool { set(name, value?.data(using: .utf8)) }

    /// The picture: the screen fitted into 1088 x 1920 (or 1920 x 1088), even numbers.
    static func pictureSize(width: Int, height: Int) -> (Int, Int) {
        let scale = min(1.0, 1088.0 / Double(min(width, height)), 1920.0 / Double(max(width, height)))
        return (max(16, Int(Double(width) * scale) / 2 * 2), max(16, Int(Double(height) * scale) / 2 * 2))
    }
}

/// Texts in the phone's language (English, German, French, Italian).
enum Texts {
    private static let all: [String: [String]] = [
        "intro": ["A Mixel technician can see your screen and help you. Ask the technician for a support code and type it here.", "Ein Mixel-Techniker kann Ihren Bildschirm sehen und Ihnen helfen. Lassen Sie sich vom Techniker einen Support-Code geben und geben Sie ihn hier ein.", "Un technicien Mixel peut voir votre écran et vous aider. Demandez-lui un code d’assistance et saisissez-le ici.", "Un tecnico Mixel può vedere il tuo schermo e aiutarti. Chiedi al tecnico un codice di assistenza e inseriscilo qui."],
        "code": ["Support code", "Support-Code", "Code d’assistance", "Codice di assistenza"],
        "connect": ["Connect", "Verbinden", "Se connecter", "Connetti"],
        "connecting": ["Connecting…", "Verbindung wird hergestellt…", "Connexion…", "Connessione…"],
        "ready": ["Ready. Tell the technician this number:", "Bereit. Nennen Sie dem Techniker diese Nummer:", "Prêt. Indiquez ce numéro au technicien :", "Pronto. Comunica questo numero al tecnico:"],
        "share": ["Start sharing my screen", "Bildschirm teilen", "Partager mon écran", "Condividi il mio schermo"],
        "share_hint": ["Tap the button, choose “Mixel Quick Support” and then “Start Broadcast”. The technician who gave you the code can then see your screen. They cannot operate your iPhone. To stop, tap the red mark at the top of the screen.", "Tippen Sie auf die Schaltfläche, wählen Sie «Mixel Quick Support» und dann «Übertragung starten». Der Techniker, der Ihnen den Code gegeben hat, sieht dann Ihren Bildschirm. Er kann Ihr iPhone nicht bedienen. Zum Beenden tippen Sie oben auf die rote Markierung.", "Touchez le bouton, choisissez « Mixel Quick Support » puis « Lancer la diffusion ». Le technicien qui vous a donné le code voit alors votre écran. Il ne peut pas utiliser votre iPhone. Pour arrêter, touchez la marque rouge en haut de l’écran.", "Tocca il pulsante, scegli «Mixel Quick Support» e poi «Avvia trasmissione». Il tecnico che ti ha dato il codice vede allora il tuo schermo. Non può usare il tuo iPhone. Per interrompere, tocca il segno rosso in alto."],
        "forget": ["Use another code", "Anderen Code verwenden", "Utiliser un autre code", "Usa un altro codice"],
        "invalid": ["This support code is not valid or has expired.", "Dieser Support-Code ist ungültig oder abgelaufen.", "Ce code d’assistance est invalide ou a expiré.", "Questo codice di assistenza non è valido o è scaduto."],
        "failed": ["That did not work. Please try again.", "Das hat nicht funktioniert. Bitte versuchen Sie es erneut.", "Cela n’a pas fonctionné. Veuillez réessayer.", "Non ha funzionato. Riprova."],
        "privacy": ["The session is encrypted. The registration of this iPhone is removed automatically afterwards.", "Die Sitzung ist verschlüsselt. Die Registrierung dieses iPhones wird danach automatisch entfernt.", "La session est chiffrée. L’enregistrement de cet iPhone est ensuite supprimé automatiquement.", "La sessione è cifrata. La registrazione di questo iPhone viene poi rimossa automaticamente."],
        "no_code": ["Open Mixel Quick Support and enter the technician’s support code first.", "Öffnen Sie zuerst Mixel Quick Support und geben Sie den Support-Code des Technikers ein.", "Ouvrez d’abord Mixel Quick Support et saisissez le code d’assistance du technicien.", "Apri prima Mixel Quick Support e inserisci il codice di assistenza del tecnico."],
        "ended": ["The support session has ended.", "Die Support-Sitzung wurde beendet.", "La session d’assistance est terminée.", "La sessione di assistenza è terminata."],
        "offline": ["The connection to Mixel was lost. Start sharing again.", "Die Verbindung zu Mixel wurde unterbrochen. Starten Sie die Freigabe erneut.", "La connexion à Mixel a été perdue. Relancez le partage.", "La connessione a Mixel è stata interrotta. Avvia di nuovo la condivisione."],
    ]
    static func of(_ key: String) -> String {
        let language = Locale.preferredLanguages.first.map { String($0.prefix(2)) } ?? "en"
        let index = ["en", "de", "fr", "it"].firstIndex(of: language) ?? 0
        return all[key]?[index] ?? key
    }
}
