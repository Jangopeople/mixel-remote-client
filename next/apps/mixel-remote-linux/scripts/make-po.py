#!/usr/bin/env python3
"""make-po.py: builds po/de.po, po/fr.po, po/it.po (gettext catalogs) and data/engine-texts.json.

Every screen text is an English `t("…")` / `tf("…", …)` message id in src/. German, French and
Italian come, in this order, from the native Apple apps' String Catalog (the same English keys),
from the desktop app's tables (frontends/desktop/i18n-strings.ts, via engine-texts.json) and, for
texts only the Linux app has, from LINUX below. The script fails when a text has no translation, so
nothing ships half-translated. Run after adding or changing screen texts.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLE = os.path.join(HERE, '..', 'mixel-remote-apple', 'Sources', 'MixelRemoteKit', 'Resources')
CATALOG = json.load(open(os.path.join(APPLE, 'Localizable.xcstrings'), encoding='utf-8'))['strings']
TABLES = json.load(open(os.path.join(APPLE, 'engine-texts.json'), encoding='utf-8'))
LANGS = ['de', 'fr', 'it']

# English: (German (Swiss spelling), French, Italian) — texts only the Linux app shows.
LINUX = {
    "Address of your AI server (empty: find it)": ("Adresse Ihres KI-Servers (leer: suchen)", "Adresse de votre serveur d’IA (vide : le trouver)", "Indirizzo del Suo server IA (vuoto: cercarlo)"),
    "Answer": ("Antwort", "Réponse", "Risposta"),
    "Ask about this screen": ("Frage zu diesem Bildschirm", "Question sur cet écran", "Domanda su questo schermo"),
    "ISM ticket": ("ISM-Ticket", "Ticket ISM", "Ticket ISM"),
    "Save in ticket": ("Im Ticket speichern", "Enregistrer dans le ticket", "Salva nel ticket"),
    "Saved as internal note in %@": ("Als interne Notiz gespeichert in %@", "Enregistré comme note interne dans %@", "Salvato come nota interna in %@"),
    "Write the ticket note": ("Ticketnotiz schreiben", "Rédiger la note du ticket", "Scrivi la nota del ticket"),
    "Your company's own AI server explains the screen, suggests steps and writes the ticket note. The picture is sent only to that server.": ("Der eigene KI-Server Ihrer Firma erklärt den Bildschirm, schlägt Schritte vor und schreibt die Ticketnotiz. Das Bild geht nur an diesen Server.", "Le propre serveur d’IA de votre entreprise explique l’écran, propose des étapes et rédige la note du ticket. L’image n’est envoyée qu’à ce serveur.", "Il server IA della Sua azienda spiega lo schermo, propone i passi e scrive la nota del ticket. L’immagine viene inviata solo a quel server."),
    "A technician is connected through it right now.": ("Gerade ist eine Technikerin oder ein Techniker darüber verbunden.", "Un technicien y est connecté en ce moment.", "In questo momento un tecnico è collegato tramite questo accesso."),
    "About Mixel Remote": ("Über Mixel Remote", "À propos de Mixel Remote", "Informazioni su Mixel Remote"),
    "Always-on access is installed (Remote ID %@), but this computer’s sign-in screen uses Wayland, which cannot be shared. Switch the sign-in screen to Xorg (GDM: WaylandEnable=false) to use it.": ("Der Zugriff ist installiert (Remote-ID %@), aber der Anmeldebildschirm dieses Computers verwendet Wayland und kann nicht geteilt werden. Stellen Sie den Anmeldebildschirm auf Xorg um (GDM: WaylandEnable=false), um ihn zu nutzen.", "L’accès permanent est installé (ID Remote %@), mais l’écran de connexion de cet ordinateur utilise Wayland et ne peut pas être partagé. Passez l’écran de connexion à Xorg (GDM : WaylandEnable=false) pour l’utiliser.", "L’accesso permanente è installato (ID Remote %@), ma la schermata di accesso di questo computer usa Wayland e non può essere condivisa. Passi la schermata di accesso a Xorg (GDM: WaylandEnable=false) per usarla."),
    "Check now": ("Jetzt prüfen", "Vérifier maintenant", "Verifica ora"),
    "Computers of a company get sign-in screen access with the company's deploy link (Devices › Add other computers).": ("Computer einer Firma erhalten den Zugriff auf den Anmeldebildschirm mit dem Verteil-Link der Firma (Geräte › Weitere Computer hinzufügen).", "Les ordinateurs d’une entreprise obtiennent l’accès à l’écran de connexion avec le lien de déploiement de l’entreprise (Appareils › Ajouter d’autres ordinateurs).", "I computer di un’azienda ottengono l’accesso alla schermata di accesso con il link di distribuzione dell’azienda (Dispositivi › Aggiungi altri computer)."),
    "Connection ID copied": ("Verbindungs-ID kopiert", "ID de connexion copié", "ID di connessione copiato"),
    "Ctrl+Alt+Del on the remote computer": ("Ctrl+Alt+Del auf dem entfernten Computer", "Ctrl+Alt+Suppr sur l’ordinateur distant", "Ctrl+Alt+Canc sul computer remoto"),
    "Group created. Open Move to group again to choose it.": ("Gruppe erstellt. Öffnen Sie «In Gruppe verschieben» erneut, um sie zu wählen.", "Groupe créé. Ouvrez à nouveau « Déplacer vers un groupe » pour le choisir.", "Gruppo creato. Apra di nuovo «Sposta nel gruppo» per sceglierlo."),
    "Installing Mixel Remote %@… The app restarts by itself.": ("Mixel Remote %@ wird installiert… Die App startet von selbst neu.", "Installation de Mixel Remote %@… L’app redémarre d’elle-même.", "Installazione di Mixel Remote %@… L’app si riavvia da sola."),
    "Keyboard and mouse": ("Tastatur und Maus", "Clavier et souris", "Tastiera e mouse"),
    "Keyboard and mouse cannot be shared in this desktop session.": ("In dieser Desktop-Sitzung können Tastatur und Maus nicht freigegeben werden.", "Le clavier et la souris ne peuvent pas être partagés dans cette session de bureau.", "In questa sessione desktop tastiera e mouse non possono essere condivisi."),
    "Keyboard back to this computer": ("Tastatur zurück an diesen Computer", "Rendre le clavier à cet ordinateur", "Tastiera di nuovo a questo computer"),
    "Keyboard shortcuts": ("Tastenkürzel", "Raccourcis clavier", "Scorciatoie da tastiera"),
    "Keys": ("Tasten", "Touches", "Tasti"),
    "Main menu": ("Hauptmenü", "Menu principal", "Menu principale"),
    "Mixel Remote %@ is ready.": ("Mixel Remote %@ ist bereit.", "Mixel Remote %@ est prêt.", "Mixel Remote %@ è pronto."),
    "No desktop session was found for sharing this screen.": ("Für das Teilen dieses Bildschirms wurde keine Desktop-Sitzung gefunden.", "Aucune session de bureau n’a été trouvée pour partager cet écran.", "Non è stata trovata alcuna sessione desktop per condividere questo schermo."),
    "No screen of this computer can be shared right now. Sign in to a desktop session (X11, or Wayland with the system's sharing dialog) and try again.": ("Im Moment kann kein Bildschirm dieses Computers geteilt werden. Melden Sie sich bei einer Desktop-Sitzung an (X11 oder Wayland mit dem Freigabedialog des Systems) und versuchen Sie es erneut.", "Aucun écran de cet ordinateur ne peut être partagé pour le moment. Connectez-vous à une session de bureau (X11, ou Wayland avec la boîte de dialogue de partage du système) et réessayez.", "Al momento non è possibile condividere nessuno schermo di questo computer. Acceda a una sessione desktop (X11, oppure Wayland con la finestra di condivisione del sistema) e riprovi."),
    "Not available": ("Nicht verfügbar", "Non disponible", "Non disponibile"),
    "On for %@ (Remote ID %@).": ("Eingeschaltet für %@ (Remote-ID %@).", "Activé pour %@ (ID Remote %@).", "Attivo per %@ (ID Remote %@)."),
    "Print screen": ("Druck-Taste", "Impr. écran", "Stamp"),
    "Remote support and access for your devices.": ("Fernsupport und Fernzugriff für Ihre Geräte.", "Assistance et accès à distance pour vos appareils.", "Assistenza e accesso remoti per i Suoi dispositivi."),
    "Role of %@": ("Rolle von %@", "Rôle de %@", "Ruolo di %@"),
    "Save": ("Speichern", "Enregistrer", "Salva"),
    "Screen sharing": ("Bildschirmfreigabe", "Partage d’écran", "Condivisione dello schermo"),
    "Sections": ("Bereiche", "Sections", "Sezioni"),
    "Send a key combination": ("Tastenkombination senden", "Envoyer une combinaison de touches", "Invia una combinazione di tasti"),
    "Session": ("Sitzung", "Session", "Sessione"),
    "Set up sign-in screen access…": ("Zugriff auf den Anmeldebildschirm einrichten…", "Configurer l’accès à l’écran de connexion…", "Configura l’accesso alla schermata di accesso…"),
    "Sharing this screen": ("Diesen Bildschirm teilen", "Partager cet écran", "Condividere questo schermo"),
    "Sign-in screen access is on (Remote ID %@).": ("Der Zugriff auf den Anmeldebildschirm ist eingeschaltet (Remote-ID %@).", "L’accès à l’écran de connexion est activé (ID Remote %@).", "L’accesso alla schermata di accesso è attivo (ID Remote %@)."),
    "Technicians of your company can reach this computer at its sign-in screen and while nobody is signed in (X11 sign-in screens only). Needs administrator approval on this computer.": ("Technikerinnen und Techniker Ihrer Firma erreichen diesen Computer auch am Anmeldebildschirm und wenn niemand angemeldet ist (nur X11-Anmeldebildschirme). Braucht die Zustimmung einer Administratorin oder eines Administrators auf diesem Computer.", "Les techniciens de votre entreprise peuvent joindre cet ordinateur à son écran de connexion et lorsque personne n’est connecté (écrans de connexion X11 uniquement). Nécessite l’autorisation d’un administrateur sur cet ordinateur.", "I tecnici della Sua azienda possono raggiungere questo computer alla schermata di accesso e quando nessuno ha effettuato l’accesso (solo schermate di accesso X11). Richiede l’approvazione di un amministratore su questo computer."),
    "The update could not be installed: %@": ("Das Update konnte nicht installiert werden: %@", "La mise à jour n’a pas pu être installée : %@", "Non è stato possibile installare l’aggiornamento: %@"),
    "This computer uses Wayland. Each time a technician connects, the system asks which screen to share. Wayland lets no program darken the screen, block keyboard and mouse or show another person's pointer, and the sign-in screen cannot be shared. Sign in with an Xorg session (for example “Ubuntu on Xorg”) for these.": ("Dieser Computer verwendet Wayland. Bei jeder Verbindung fragt das System, welcher Bildschirm geteilt wird. Unter Wayland darf kein Programm den Bildschirm abdunkeln, Tastatur und Maus sperren oder den Zeiger einer anderen Person zeigen, und der Anmeldebildschirm kann nicht geteilt werden. Melden Sie sich dafür mit einer Xorg-Sitzung an (zum Beispiel «Ubuntu on Xorg»).", "Cet ordinateur utilise Wayland. À chaque connexion, le système demande quel écran partager. Sous Wayland, aucun programme ne peut assombrir l’écran, bloquer le clavier et la souris ni afficher le pointeur d’une autre personne, et l’écran de connexion ne peut pas être partagé. Connectez-vous avec une session Xorg (par exemple « Ubuntu on Xorg ») pour cela.", "Questo computer usa Wayland. A ogni connessione il sistema chiede quale schermo condividere. Con Wayland nessun programma può oscurare lo schermo, bloccare tastiera e mouse o mostrare il puntatore di un’altra persona, e la schermata di accesso non può essere condivisa. Per queste funzioni acceda con una sessione Xorg (per esempio «Ubuntu on Xorg»)."),
    "This computer uses Wayland: the system asks you to choose the screen each time it is shared. The privacy screen, blocking keyboard and mouse and the technician's pointer are not possible on Wayland; sign in with an Xorg session for them.": ("Dieser Computer verwendet Wayland: Das System fragt bei jeder Freigabe, welcher Bildschirm geteilt wird. Sichtschutz, Sperren von Tastatur und Maus und der Zeiger der Technikerin oder des Technikers sind unter Wayland nicht möglich; melden Sie sich dafür mit einer Xorg-Sitzung an.", "Cet ordinateur utilise Wayland : le système vous demande de choisir l’écran à chaque partage. L’écran de confidentialité, le blocage du clavier et de la souris et le pointeur du technicien ne sont pas possibles sous Wayland ; connectez-vous avec une session Xorg pour cela.", "Questo computer usa Wayland: il sistema Le chiede di scegliere lo schermo a ogni condivisione. Lo schermo privacy, il blocco di tastiera e mouse e il puntatore del tecnico non sono possibili con Wayland; per queste funzioni acceda con una sessione Xorg."),
    "This computer uses X11: screen, keyboard and mouse can be shared, and the privacy screen, input blocking and the technician's pointer work.": ("Dieser Computer verwendet X11: Bildschirm, Tastatur und Maus können geteilt werden, und Sichtschutz, Eingabesperre und der Zeiger der Technikerin oder des Technikers funktionieren.", "Cet ordinateur utilise X11 : l’écran, le clavier et la souris peuvent être partagés, et l’écran de confidentialité, le blocage de la saisie et le pointeur du technicien fonctionnent.", "Questo computer usa X11: schermo, tastiera e mouse possono essere condivisi, e lo schermo privacy, il blocco dell’input e il puntatore del tecnico funzionano."),
    "Update sign-in screen access": ("Zugriff auf den Anmeldebildschirm aktualisieren", "Mettre à jour l’accès à l’écran de connexion", "Aggiorna l’accesso alla schermata di accesso"),
    "Valid for (days)": ("Gültig (Tage)", "Valable (jours)", "Valido (giorni)"),
    "Version %@ for Linux": ("Version %@ für Linux", "Version %@ pour Linux", "Versione %@ per Linux"),
    "Wake %@ with a network signal": ("%@ mit einem Netzwerksignal aufwecken", "Réveiller %@ par un signal réseau", "Riattiva %@ con un segnale di rete"),
    "Wayland requires local confirmation for each sharing session.": ("Wayland verlangt für jede Freigabe eine Bestätigung an diesem Computer.", "Wayland exige une confirmation locale pour chaque session de partage.", "Wayland richiede una conferma locale per ogni sessione di condivisione."),
    "Wayland: after Allow, the system asks you once more which screen to share.": ("Wayland: Nach «Erlauben» fragt das System noch einmal, welcher Bildschirm geteilt wird.", "Wayland : après « Autoriser », le système vous demande encore quel écran partager.", "Wayland: dopo «Consenti» il sistema chiede ancora quale schermo condividere."),
    "Windows / Super key": ("Windows-/Super-Taste", "Touche Windows / Super", "Tasto Windows / Super"),
}

def keys():
    found = set()
    pattern = re.compile(r'\b(?:t|tf)\(\s*"((?:[^"\\]|\\.)*)"')
    for root, _, files in os.walk(os.path.join(HERE, 'src')):
        for name in files:
            if name.endswith('.rs'):
                text = open(os.path.join(root, name), encoding='utf-8').read()
                for match in pattern.finditer(text):
                    found.add(match.group(1).encode().decode('unicode_escape').encode('latin-1').decode('utf-8'))
    return sorted(found)

def lookup(key, lang, index):
    item = CATALOG.get(key, {}).get('localizations', {}).get(lang, {}).get('stringUnit', {}).get('value')
    if item:
        return item
    if key in LINUX:
        return LINUX[key][index]
    return TABLES.get(lang, {}).get('texts', {}).get(key)

def quote(text):
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'

def main():
    all_keys = keys()
    missing = []
    for index, lang in enumerate(LANGS):
        lines = ['# Mixel Remote for Linux — %s. Generated by scripts/make-po.py; edit LINUX there.' % lang,
                 'msgid ""', 'msgstr "Content-Type: text/plain; charset=UTF-8\\nLanguage: %s\\n"' % lang, '']
        for key in all_keys:
            value = lookup(key, lang, index)
            if not value:
                missing.append((lang, key))
                continue
            if key.count('%@') != value.count('%@') or key.count('%d') != value.count('%d'):
                missing.append((lang, key + '   [placeholders differ: ' + value + ']'))
                continue
            lines += ['msgid ' + quote(key), 'msgstr ' + quote(value), '']
        open(os.path.join(HERE, 'po', lang + '.po'), 'w', encoding='utf-8').write('\n'.join(lines))
    json.dump(TABLES, open(os.path.join(HERE, 'data', 'engine-texts.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    if missing:
        seen = sorted({key for _, key in missing})
        print('MISSING %d texts:' % len(seen))
        for key in seen:
            print('    %s: ("", "", ""),' % json.dumps(key, ensure_ascii=False))
        sys.exit(1)
    print('%d texts, %d languages: complete' % (len(all_keys), len(LANGS)))

main()
