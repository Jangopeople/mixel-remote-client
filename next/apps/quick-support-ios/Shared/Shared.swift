import Foundation
import Security
#if canImport(UIKit)
import UIKit
#endif

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

    /// The name the technician sees for this phone.
    static var deviceName: String {
        #if canImport(UIKit)
        return UIDevice.current.userInterfaceIdiom == .pad ? "iPad" : "iPhone"
        #else
        return "iPhone"
        #endif
    }

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
        "ready": ["Code accepted. Now share your screen with the technician:", "Code angenommen. Teilen Sie jetzt Ihren Bildschirm mit dem Techniker:", "Code accepté. Partagez maintenant votre écran avec le technicien :", "Codice accettato. Ora condividi lo schermo con il tecnico:"],
        "number": ["Device number, if the technician asks for it:", "Gerätenummer, falls der Techniker danach fragt:", "Numéro de l’appareil, si le technicien le demande :", "Numero del dispositivo, se il tecnico lo chiede:"],
        "share": ["Start sharing my screen", "Bildschirm teilen", "Partager mon écran", "Condividi il mio schermo"],
        "share_hint": ["Tap the button, choose “Mixel Quick Support” and then “Start Broadcast”. The technician who has your ID then sees your screen. They cannot operate your iPhone. To stop, tap the red mark at the top of the screen.", "Tippen Sie auf die Schaltfläche, wählen Sie «Mixel Quick Support» und dann «Übertragung starten». Der Techniker mit Ihrer ID sieht dann Ihren Bildschirm. Er kann Ihr iPhone nicht bedienen. Zum Beenden tippen Sie oben auf die rote Markierung.", "Touchez le bouton, choisissez « Mixel Quick Support » puis « Lancer la diffusion ». Le technicien qui a votre ID voit alors votre écran. Il ne peut pas utiliser votre iPhone. Pour arrêter, touchez la marque rouge en haut de l’écran.", "Tocca il pulsante, scegli «Mixel Quick Support» e poi «Avvia trasmissione». Il tecnico che ha il tuo ID vede allora il tuo schermo. Non può usare il tuo iPhone. Per interrompere, tocca il segno rosso in alto."],
        "forget": ["Use another code", "Anderen Code verwenden", "Utiliser un autre code", "Usa un altro codice"],
        "invalid": ["This support code is not valid or has expired.", "Dieser Support-Code ist ungültig oder abgelaufen.", "Ce code d’assistance est invalide ou a expiré.", "Questo codice di assistenza non è valido o è scaduto."],
        "unreachable": ["Mixel cannot be reached. Check the internet connection and try again.", "Mixel ist nicht erreichbar. Prüfen Sie die Internetverbindung und versuchen Sie es erneut.", "Mixel est injoignable. Vérifiez la connexion Internet et réessayez.", "Mixel non è raggiungibile. Controlla la connessione a Internet e riprova."],
        "failed": ["That did not work. Please try again.", "Das hat nicht funktioniert. Bitte versuchen Sie es erneut.", "Cela n’a pas fonctionné. Veuillez réessayer.", "Non ha funzionato. Riprova."],
        "privacy": ["The session is encrypted. Only a Mixel technician you give this ID to can connect, and only after you allow it.", "Die Sitzung ist verschlüsselt. Nur ein Mixel-Techniker, dem Sie diese ID nennen, kann sich verbinden – und erst, wenn Sie es erlauben.", "La session est chiffrée. Seul un technicien Mixel à qui vous donnez cet ID peut se connecter, et seulement après votre accord.", "La sessione è cifrata. Solo un tecnico Mixel a cui comunichi questo ID può collegarsi, e solo dopo la tua autorizzazione."],
        "your_id": ["Your ID", "Ihre ID", "Votre ID", "Il tuo ID"],
        "tell_id": ["Tell this number to your Mixel technician. Then tap the button below.", "Nennen Sie diese Nummer Ihrem Mixel-Techniker. Tippen Sie dann auf die Schaltfläche unten.", "Communiquez ce numéro à votre technicien Mixel, puis touchez le bouton ci-dessous.", "Comunica questo numero al tuo tecnico Mixel, poi tocca il pulsante qui sotto."],
        "getting_id": ["Getting your ID…", "Ihre ID wird abgerufen…", "Récupération de votre ID…", "Recupero del tuo ID…"],
        "not_claimed": ["The technician did not connect. Tell them your ID again and start sharing once more.", "Der Techniker hat sich nicht verbunden. Nennen Sie ihm Ihre ID erneut und starten Sie die Freigabe noch einmal.", "Le technicien ne s’est pas connecté. Redonnez-lui votre ID et relancez le partage.", "Il tecnico non si è collegato. Comunicagli di nuovo il tuo ID e avvia di nuovo la condivisione."],
        "no_code": ["Open Mixel Quick Support first, then start sharing again.", "Öffnen Sie zuerst Mixel Quick Support und starten Sie die Freigabe erneut.", "Ouvrez d’abord Mixel Quick Support, puis relancez le partage.", "Apri prima Mixel Quick Support, poi avvia di nuovo la condivisione."],
        "ended": ["The support session has ended.", "Die Support-Sitzung wurde beendet.", "La session d’assistance est terminée.", "La sessione di assistenza è terminata."],
        "offline": ["The connection to Mixel was lost. Start sharing again.", "Die Verbindung zu Mixel wurde unterbrochen. Starten Sie die Freigabe erneut.", "La connexion à Mixel a été perdue. Relancez le partage.", "La connessione a Mixel è stata interrotta. Avvia di nuovo la condivisione."],
    ]
    static func of(_ key: String) -> String {
        let language = Locale.preferredLanguages.first.map { String($0.prefix(2)) } ?? "en"
        let index = ["en", "de", "fr", "it"].firstIndex(of: language) ?? 0
        return all[key]?[index] ?? key
    }
}
