#!/usr/bin/env python3
"""make-strings.py: builds app/src/main/assets/texts.json (German, French, Italian screen texts).

Every screen text is an `L("…")` key in English, as in the Apple apps. Translations come, in this
order, from the Apple apps' String Catalog (apps/mixel-remote-apple/.../Localizable.xcstrings), the
desktop app's tables (engine-texts.json) and, for texts only the Android app has, ANDROID below. The
script fails when a key has no translation, so nothing ships half-translated. Run after adding or
changing screen texts (Gradle's build does not run it).
"""
import glob, json, os, re, shutil, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPLE = os.path.join(HERE, '..', 'mixel-remote-apple', 'Sources', 'MixelRemoteKit', 'Resources')
CATALOG = json.load(open(os.path.join(APPLE, 'Localizable.xcstrings'), encoding='utf-8'))['strings']
# The engine's message table is the Apple apps' copy of the desktop table; the app bundles the same file.
shutil.copyfile(os.path.join(APPLE, 'engine-texts.json'), os.path.join(HERE, 'app/src/main/assets/engine-texts.json'))
ENGINE = json.load(open(os.path.join(HERE, 'app/src/main/assets/engine-texts.json'), encoding='utf-8'))
LANGS = ['de', 'fr', 'it']

# Keys used through variables (KeyMap.Special.spoken).
EXTRA = ['Escape', 'Tab', 'Enter', 'Backspace', 'Delete', 'Arrow up', 'Arrow down', 'Arrow left', 'Arrow right',
         'Home', 'End', 'Page up', 'Page down']

# English key: (German (Swiss spelling), French, Italian)
ANDROID = {
    "Add and enroll this phone": ("Dieses Telefon hinzufügen und registrieren", "Ajouter et enregistrer ce téléphone", "Aggiungi e registra questo telefono"),
    "Add this phone": ("Dieses Telefon hinzufügen", "Ajouter ce téléphone", "Aggiungi questo telefono"),
    "Add this phone to %@ so you can connect from it.": ("Fügen Sie dieses Telefon zu %@ hinzu, um von ihm aus zu verbinden.", "Ajoutez ce téléphone à %@ pour vous connecter depuis celui-ci.", "Aggiunga questo telefono a %@ per connettersi da esso."),
    "Arrow down": ("Pfeil nach unten", "Flèche vers le bas", "Freccia giù"),
    "Arrow left": ("Pfeil nach links", "Flèche vers la gauche", "Freccia sinistra"),
    "Arrow right": ("Pfeil nach rechts", "Flèche vers la droite", "Freccia destra"),
    "Arrow up": ("Pfeil nach oben", "Flèche vers le haut", "Freccia su"),
    "Automatic (this phone’s language)": ("Automatisch (Sprache dieses Telefons)", "Automatique (langue de ce téléphone)", "Automatica (lingua di questo telefono)"),
    "Back": ("Zurück", "Retour", "Indietro"),
    "Backspace": ("Rücktaste", "Retour arrière", "Backspace"),
    "Choose how you sign in. The sign-in page opens; Mixel Remote continues by itself when you are signed in.": ("Wählen Sie, wie Sie sich anmelden. Die Anmeldeseite öffnet sich; Mixel Remote fährt danach von selbst fort.", "Choisissez comment vous connecter. La page de connexion s’ouvre ; Mixel Remote continue tout seul une fois connecté.", "Scelga come accedere. Si apre la pagina di accesso; Mixel Remote prosegue da solo dopo l’accesso."),
    "Continue with %@": ("Weiter mit %@", "Continuer avec %@", "Continua con %@"),
    "Creates a deploy link. Run the command it shows on each computer; it joins your company without a sign-in.": ("Erstellt einen Verteillink. Führen Sie den angezeigten Befehl auf jedem Computer aus; er tritt Ihrer Firma ohne Anmeldung bei.", "Crée un lien de déploiement. Exécutez la commande affichée sur chaque ordinateur ; il rejoint votre entreprise sans connexion.", "Crea un link di distribuzione. Esegua il comando mostrato su ogni computer; si aggiunge alla Sua azienda senza accesso."),
    "Delete": ("Entf", "Suppr", "Canc"),
    "Deploy token (the Mac and Linux commands ask for it)": ("Verteil-Token (die Befehle für Mac und Linux fragen danach)", "Jeton de déploiement (les commandes Mac et Linux le demandent)", "Token di distribuzione (i comandi per Mac e Linux lo chiedono)"),
    "Deploy link created": ("Verteillink erstellt", "Lien de déploiement créé", "Link di distribuzione creato"),
    "End": ("Ende", "Fin", "Fine"),
    "Enroll this phone": ("Dieses Telefon registrieren", "Enregistrer ce téléphone", "Registra questo telefono"),
    "Enroll this phone against its existing ISM record, or add it to a company.": ("Registrieren Sie dieses Telefon mit seinem bestehenden ISM-Eintrag oder fügen Sie es einer Firma hinzu.", "Enregistrez ce téléphone avec son entrée ISM existante ou ajoutez-le à une entreprise.", "Registri questo telefono con la sua voce ISM esistente oppure lo aggiunga a un’azienda."),
    "Enter": ("Eingabe", "Entrée", "Invio"),
    "Escape": ("Escape", "Échap", "Esc"),
    "Get the remote clipboard": ("Entfernte Zwischenablage holen", "Récupérer le presse-papiers distant", "Prendi gli appunti remoti"),
    "Group name": ("Gruppenname", "Nom du groupe", "Nome del gruppo"),
    "Hide keyboard": ("Tastatur ausblenden", "Masquer le clavier", "Nascondi tastiera"),
    "Home": ("Pos1", "Début", "Inizio"),
    "It can no longer connect until it is added again.": ("Es kann sich nicht mehr verbinden, bis es wieder hinzugefügt wird.", "Il ne peut plus se connecter tant qu’il n’est pas ajouté à nouveau.", "Non potrà più connettersi finché non verrà aggiunto di nuovo."),
    "Keyboard for the remote computer": ("Tastatur für den entfernten Computer", "Clavier pour l’ordinateur distant", "Tastiera per il computer remoto"),
    "Label": ("Bezeichnung", "Libellé", "Etichetta"),
    "Lock when the session ends": ("Sperren, wenn die Sitzung endet", "Verrouiller à la fin de la session", "Blocca al termine della sessione"),
    "Move to group": ("In Gruppe verschieben", "Déplacer dans un groupe", "Sposta nel gruppo"),
    "Name of this phone": ("Name dieses Telefons", "Nom de ce téléphone", "Nome di questo telefono"),
    "No browser is available to open the page": ("Kein Browser verfügbar, um die Seite zu öffnen", "Aucun navigateur n’est disponible pour ouvrir la page", "Nessun browser disponibile per aprire la pagina"),
    "No messages yet.": ("Noch keine Nachrichten.", "Pas encore de messages.", "Ancora nessun messaggio."),
    "Number of computers": ("Anzahl Computer", "Nombre d’ordinateurs", "Numero di computer"),
    "Other side": ("Gegenseite", "Autre côté", "Altra parte"),
    "Page down": ("Bild ab", "Page suivante", "Pagina giù"),
    "Page up": ("Bild auf", "Page précédente", "Pagina su"),
    "Receive": ("Empfangen", "Recevoir", "Ricevi"),
    "Receiving %@": ("%@ wird empfangen", "Réception de %@", "Ricezione di %@"),
    "Register this phone": ("Dieses Telefon registrieren", "Enregistrer ce téléphone", "Registra questo telefono"),
    "Register this phone with your personal account.": ("Registrieren Sie dieses Telefon mit Ihrem persönlichen Konto.", "Enregistrez ce téléphone avec votre compte personnel.", "Registri questo telefono con il Suo account personale."),
    "Remove %@?": ("%@ entfernen?", "Retirer %@ ?", "Rimuovere %@?"),
    "Remove this phone": ("Dieses Telefon entfernen", "Retirer ce téléphone", "Rimuovi questo telefono"),
    "Remove this phone from Mixel Remote": ("Dieses Telefon aus Mixel Remote entfernen", "Retirer ce téléphone de Mixel Remote", "Rimuovi questo telefono da Mixel Remote"),
    "Remove this phone from Mixel Remote?": ("Dieses Telefon aus Mixel Remote entfernen?", "Retirer ce téléphone de Mixel Remote ?", "Rimuovere questo telefono da Mixel Remote?"),
    "Role of %@: %@": ("Rolle von %@: %@", "Rôle de %@ : %@", "Ruolo di %@: %@"),
    "Save to this phone…": ("Auf diesem Telefon speichern…", "Enregistrer sur ce téléphone…", "Salva su questo telefono…"),
    "Saved. This phone is removed as soon as Mixel Remote can be reached.": ("Gespeichert. Dieses Telefon wird entfernt, sobald Mixel Remote erreichbar ist.", "Enregistré. Ce téléphone sera retiré dès que Mixel Remote sera joignable.", "Salvato. Questo telefono verrà rimosso non appena Mixel Remote sarà raggiungibile."),
    "Search devices": ("Geräte suchen", "Rechercher des appareils", "Cerca dispositivi"),
    "Sending %@": ("%@ wird gesendet", "Envoi de %@", "Invio di %@"),
    "Set up Mixel Remote": ("Mixel Remote einrichten", "Configurer Mixel Remote", "Configura Mixel Remote"),
    "Shown once this phone is connected.": ("Wird angezeigt, sobald dieses Telefon verbunden ist.", "Affiché dès que ce téléphone est connecté.", "Mostrato non appena questo telefono è connesso."),
    "Shown only now. Run the command on each computer as administrator.": ("Wird nur jetzt angezeigt. Führen Sie den Befehl auf jedem Computer als Administrator aus.", "Affiché uniquement maintenant. Exécutez la commande sur chaque ordinateur en tant qu’administrateur.", "Mostrato solo ora. Esegua il comando su ogni computer come amministratore."),
    "Stay signed in on this phone": ("Auf diesem Telefon angemeldet bleiben", "Rester connecté sur ce téléphone", "Resta connesso su questo telefono"),
    "Stays signed in on this phone": ("Bleibt auf diesem Telefon angemeldet", "Reste connecté sur ce téléphone", "Resta connesso su questo telefono"),
    "Tab": ("Tab", "Tab", "Tab"),
    "Tap to click, touch and hold for a right click, two fingers to scroll, pinch to zoom.": ("Tippen zum Klicken, gedrückt halten für einen Rechtsklick, mit zwei Fingern scrollen, mit zwei Fingern zoomen.", "Touchez pour cliquer, maintenez pour un clic droit, deux doigts pour faire défiler, pincez pour zoomer.", "Tocchi per fare clic, tenga premuto per il clic destro, due dita per scorrere, pizzichi per lo zoom."),
    "The connection to %@ ends for both sides.": ("Die Verbindung mit %@ endet für beide Seiten.", "La connexion avec %@ se termine des deux côtés.", "La connessione con %@ termina per entrambe le parti."),
    "The file could not be saved": ("Die Datei konnte nicht gespeichert werden", "Le fichier n’a pas pu être enregistré", "Non è stato possibile salvare il file"),
    "This device": ("Dieses Gerät", "Cet appareil", "Questo dispositivo"),
    "This device · not connected": ("Dieses Gerät · nicht verbunden", "Cet appareil · non connecté", "Questo dispositivo · non connesso"),
    "This is the device you are using.": ("Dies ist das Gerät, das Sie gerade verwenden.", "C’est l’appareil que vous utilisez.", "Questo è il dispositivo che sta usando."),
    "This phone is being removed from Mixel Remote. It is removed as soon as it is online.": ("Dieses Telefon wird aus Mixel Remote entfernt, sobald es online ist.", "Ce téléphone sera retiré de Mixel Remote dès qu’il sera en ligne.", "Questo telefono verrà rimosso da Mixel Remote non appena sarà online."),
    "This phone is not in the list? Add it": ("Dieses Telefon ist nicht in der Liste? Fügen Sie es hinzu", "Ce téléphone n’est pas dans la liste ? Ajoutez-le", "Questo telefono non è nell’elenco? Lo aggiunga"),
    "This phone is not set up yet.": ("Dieses Telefon ist noch nicht eingerichtet.", "Ce téléphone n’est pas encore configuré.", "Questo telefono non è ancora configurato."),
    "This phone was removed from Mixel Remote.": ("Dieses Telefon wurde aus Mixel Remote entfernt.", "Ce téléphone a été retiré de Mixel Remote.", "Questo telefono è stato rimosso da Mixel Remote."),
    "This phone's registration changed since; nothing was removed.": ("Die Registrierung dieses Telefons hat sich inzwischen geändert; es wurde nichts entfernt.", "L’enregistrement de ce téléphone a changé entre-temps ; rien n’a été retiré.", "La registrazione di questo telefono è cambiata nel frattempo; non è stato rimosso nulla."),
    "Version %@": ("Version %@", "Version %@", "Versione %@"),
    "Wake up": ("Aufwecken", "Réveiller", "Riattiva"),
    "Windows or Command key": ("Windows- oder Befehlstaste", "Touche Windows ou Commande", "Tasto Windows o Comando"),
    "held": ("gehalten", "maintenue", "premuto"),
    "not held": ("nicht gehalten", "non maintenue", "non premuto"),
}


def keys():
    found = set(EXTRA)
    pattern = re.compile(r'\bL\(\s*"((?:[^"\\]|\\.)*)"')
    for path in glob.glob(os.path.join(HERE, 'app/src/main/java/ch/**/*.kt'), recursive=True):
        text = open(path, encoding='utf-8').read()
        for match in pattern.finditer(text):
            found.add(match.group(1).replace('\\"', '"').replace('\\$', '$'))
    return sorted(found)


def lookup(key, lang, index):
    if key in ANDROID:
        return ANDROID[key][index]
    entry = CATALOG.get(key, {}).get('localizations', {}).get(lang, {}).get('stringUnit', {}).get('value')
    if entry:
        return entry
    return ENGINE.get(lang, {}).get('texts', {}).get(key)


def main():
    out = {lang: {} for lang in LANGS}
    missing = []
    for key in keys():
        for index, lang in enumerate(LANGS):
            value = lookup(key, lang, index)
            if value is None:
                missing.append(key)
                break
            out[lang][key] = value
    if missing:
        print('missing translations:', file=sys.stderr)
        for key in sorted(set(missing)):
            print(f'    {json.dumps(key, ensure_ascii=False)}: ("", "", ""),', file=sys.stderr)
        sys.exit(1)
    path = os.path.join(HERE, 'app/src/main/assets/texts.json')
    with open(path, 'w', encoding='utf-8') as file:
        json.dump(out, file, ensure_ascii=False, indent=1, sort_keys=True)
        file.write('\n')
    print(f'{path}: {len(out["de"])} texts in {", ".join(LANGS)}')


if __name__ == '__main__':
    main()
