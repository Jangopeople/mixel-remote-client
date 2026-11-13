#!/usr/bin/env python3
"""make-strings.py: builds Sources/MixelRemoteKit/Resources/Localizable.xcstrings (the String Catalog).

Every screen text is a `L("…")` key in English. German, French and Italian come from the desktop
app's tables (frontends/desktop/i18n-strings.ts, bundled as engine-texts.json) and, for texts only the
native apps have, from NATIVE below. The script fails when a key has no translation, so nothing ships
half-translated. Run after adding or changing screen texts.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = json.load(open(os.path.join(HERE, 'Sources/MixelRemoteKit/Resources/engine-texts.json'), encoding='utf-8'))

# English key: (German (Swiss spelling), French, Italian)
NATIVE = {
    "%@ trial until %@": ("%@ Testphase bis %@", "%@ essai jusqu’au %@", "%@ prova fino al %@"),
    "%@ · read-only": ("%@ · nur lesen", "%@ · lecture seule", "%@ · sola lettura"),
    "%d devices": ("%d Geräte", "%d appareils", "%d dispositivi"),
    "%d of %d devices": ("%d von %d Geräten", "%d sur %d appareils", "%d di %d dispositivi"),
    "%d of %d technicians": ("%d von %d Technikern", "%d sur %d techniciens", "%d di %d tecnici"),
    "%d technicians": ("%d Techniker", "%d techniciens", "%d tecnici"),
    "A colleague invites you to a session": ("Eine Kollegin oder ein Kollege lädt Sie zu einer Sitzung ein", "Un collègue vous invite à une session", "Un collega La invita a una sessione"),
    "Add this computer to %@ so you can connect from it and to it.": ("Fügen Sie diesen Computer zu %@ hinzu, um von ihm aus und zu ihm zu verbinden.", "Ajoutez cet ordinateur à %@ pour vous connecter depuis et vers lui.", "Aggiunga questo computer a %@ per connettersi da e verso di esso."),
    "Allow keyboard and mouse control": ("Steuerung mit Tastatur und Maus erlauben", "Autoriser le contrôle du clavier et de la souris", "Consenti il controllo di tastiera e mouse"),
    "Allow recording of this session": ("Aufzeichnung dieser Sitzung erlauben", "Autoriser l’enregistrement de cette session", "Consenti la registrazione di questa sessione"),
    "Allow screen recording for Mixel Remote in System Settings to share a screen.": ("Erlauben Sie Mixel Remote die Bildschirmaufnahme in den Systemeinstellungen, um einen Bildschirm zu teilen.", "Autorisez l’enregistrement de l’écran pour Mixel Remote dans les Réglages Système pour partager un écran.", "Consenta la registrazione dello schermo a Mixel Remote nelle Impostazioni di Sistema per condividere uno schermo."),
    "Allowed": ("Erlaubt", "Autorisé", "Consentito"),
    "Block input": ("Eingabe sperren", "Bloquer la saisie", "Blocca l’input"),
    "Check for updates": ("Nach Updates suchen", "Rechercher des mises à jour", "Cerca aggiornamenti"),
    "Choose a display and confirm it before enabling unattended sharing.": ("Wählen und bestätigen Sie einen Bildschirm, bevor Sie die unbeaufsichtigte Freigabe einschalten.", "Choisissez et confirmez un écran avant d’activer le partage sans surveillance.", "Scelga e confermi uno schermo prima di attivare la condivisione non presidiata."),
    "Choose a file to send": ("Datei zum Senden wählen", "Choisir un fichier à envoyer", "Scegli un file da inviare"),
    "Choose where to save the received file": ("Wählen, wo die empfangene Datei gespeichert wird", "Choisir où enregistrer le fichier reçu", "Scegli dove salvare il file ricevuto"),
    "Colleague %@": ("Kollege %@", "Collègue %@", "Collega %@"),
    "Company devices": ("Firmengeräte", "Appareils de l’entreprise", "Dispositivi dell’azienda"),
    "Computer %@": ("Computer %@", "Ordinateur %@", "Computer %@"),
    "Computers: %d": ("Computer: %d", "Ordinateurs : %d", "Computer: %d"),
    "Connect this controller to the Mixel network.": ("Verbinden Sie diese Steuerung mit dem Mixel-Netzwerk.", "Connectez ce contrôleur au réseau Mixel.", "Connetta questo controller alla rete Mixel."),
    "Connect to %@": ("Mit %@ verbinden", "Se connecter à %@", "Connetti a %@"),
    "Connect to Mixel Remote": ("Mit Mixel Remote verbinden", "Se connecter à Mixel Remote", "Connetti a Mixel Remote"),
    "Connect without confirmation (unattended access)": ("Ohne Bestätigung verbinden (unbeaufsichtigter Zugriff)", "Se connecter sans confirmation (accès sans surveillance)", "Connetti senza conferma (accesso non presidiato)"),
    "Connecting to %@…": ("Verbindung mit %@ wird hergestellt…", "Connexion à %@…", "Connessione a %@…"),
    "Connection": ("Verbindung", "Connexion", "Connessione"),
    "Connection ID": ("Verbindungs-ID", "ID de connexion", "ID di connessione"),
    "Connection problem": ("Verbindungsproblem", "Problème de connexion", "Problema di connessione"),
    "Copy": ("Kopieren", "Copier", "Copia"),
    "Could not confirm that the session ended. Try End session again.": ("Das Ende der Sitzung konnte nicht bestätigt werden. Wählen Sie nochmals «Sitzung beenden».", "La fin de la session n’a pas pu être confirmée. Réessayez « Terminer la session ».", "Non è stato possibile confermare la fine della sessione. Riprovi «Termina la sessione»."),
    "Create a company to manage your devices and team, or a personal space for your own devices.": ("Erstellen Sie eine Firma, um Geräte und Team zu verwalten, oder einen persönlichen Bereich für Ihre eigenen Geräte.", "Créez une entreprise pour gérer vos appareils et votre équipe, ou un espace personnel pour vos propres appareils.", "Crei un’azienda per gestire dispositivi e team, oppure uno spazio personale per i Suoi dispositivi."),
    "Create group": ("Gruppe erstellen", "Créer un groupe", "Crea gruppo"),
    "Ctrl+Alt+Del": ("Ctrl+Alt+Del", "Ctrl+Alt+Suppr", "Ctrl+Alt+Canc"),
    "Delete company": ("Firma löschen", "Supprimer l’entreprise", "Cancella azienda"),
    "Deletes the company at once: devices, invitations, members and their accounts. The subscription ends. Type the company name to confirm.": ("Löscht die Firma sofort: Geräte, Einladungen, Mitglieder und ihre Konten. Das Abonnement endet. Geben Sie zur Bestätigung den Firmennamen ein.", "Supprime l’entreprise immédiatement : appareils, invitations, membres et leurs comptes. L’abonnement prend fin. Saisissez le nom de l’entreprise pour confirmer.", "Cancella subito l’azienda: dispositivi, inviti, membri e i loro account. L’abbonamento termina. Digiti il nome dell’azienda per confermare."),
    "Deletes your Mixel Remote account and signs you out on every device. Company data stays with the company.": ("Löscht Ihr Mixel-Remote-Konto und meldet Sie auf allen Geräten ab. Firmendaten bleiben bei der Firma.", "Supprime votre compte Mixel Remote et vous déconnecte sur tous les appareils. Les données de l’entreprise restent à l’entreprise.", "Cancella il Suo account Mixel Remote e La disconnette su tutti i dispositivi. I dati aziendali restano all’azienda."),
    "Device %@ requests to connect. Its enrolled identity has been verified with Mixel.": ("Gerät %@ möchte sich verbinden. Seine registrierte Identität wurde bei Mixel geprüft.", "L’appareil %@ demande à se connecter. Son identité enregistrée a été vérifiée auprès de Mixel.", "Il dispositivo %@ chiede di connettersi. La sua identità registrata è stata verificata con Mixel."),
    "Device limit of your plan reached. Remove a device or upgrade the plan to add more.": ("Gerätelimit Ihres Plans erreicht. Entfernen Sie ein Gerät oder wechseln Sie den Plan, um weitere hinzuzufügen.", "Limite d’appareils de votre forfait atteinte. Retirez un appareil ou passez à un forfait supérieur.", "Limite di dispositivi del Suo piano raggiunto. Rimuova un dispositivo o passi a un piano superiore."),
    "Devices of": ("Geräte von", "Appareils de", "Dispositivi di"),
    "Display %d": ("Bildschirm %d", "Écran %d", "Schermo %d"),
    "E-mail": ("E-Mail", "E-mail", "E-mail"),
    "E-mail failed": ("E-Mail fehlgeschlagen", "Échec de l’e-mail", "E-mail non riuscita"),
    "E-mail on its way": ("E-Mail unterwegs", "E-mail en cours d’envoi", "E-mail in invio"),
    "E-mail sent": ("E-Mail gesendet", "E-mail envoyé", "E-mail inviata"),
    "Enable Accessibility in macOS to allow keyboard and mouse control.": ("Aktivieren Sie die Bedienungshilfen in macOS, um Tastatur- und Maussteuerung zu erlauben.", "Activez l’accessibilité dans macOS pour autoriser le contrôle du clavier et de la souris.", "Attivi l’accessibilità in macOS per consentire il controllo di tastiera e mouse."),
    "Enable keyboard control in macOS…": ("Tastatursteuerung in macOS aktivieren…", "Activer le contrôle du clavier dans macOS…", "Attiva il controllo della tastiera in macOS…"),
    "Enable screen sharing in macOS…": ("Bildschirmfreigabe in macOS aktivieren…", "Activer le partage d’écran dans macOS…", "Attiva la condivisione dello schermo in macOS…"),
    "End session?": ("Sitzung beenden?", "Terminer la session ?", "Terminare la sessione?"),
    "Enroll this computer against its existing ISM record, or add it to a company.": ("Registrieren Sie diesen Computer mit seinem bestehenden ISM-Eintrag oder fügen Sie ihn einer Firma hinzu.", "Enregistrez cet ordinateur avec sa fiche ISM existante ou ajoutez-le à une entreprise.", "Registri questo computer con la sua scheda ISM esistente o lo aggiunga a un’azienda."),
    "Enter a company name": ("Geben Sie einen Firmennamen ein", "Saisissez un nom d’entreprise", "Inserisca il nome dell’azienda"),
    "Enter a computer name of up to 60 characters": ("Geben Sie einen Computernamen mit höchstens 60 Zeichen ein", "Saisissez un nom d’ordinateur de 60 caractères au maximum", "Inserisca un nome di computer di al massimo 60 caratteri"),
    "Enter a device name": ("Geben Sie einen Gerätenamen ein", "Saisissez un nom d’appareil", "Inserisca un nome di dispositivo"),
    "Enter a nine-digit connection ID": ("Geben Sie eine neunstellige Verbindungs-ID ein", "Saisissez un ID de connexion à neuf chiffres", "Inserisca un ID di connessione di nove cifre"),
    "Enter an e-mail address": ("Geben Sie eine E-Mail-Adresse ein", "Saisissez une adresse e-mail", "Inserisca un indirizzo e-mail"),
    "Enter the 6-digit code": ("Geben Sie den 6-stelligen Code ein", "Saisissez le code à 6 chiffres", "Inserisca il codice di 6 cifre"),
    "Enter the 9-digit ID shown by the Mixel Remote host.": ("Geben Sie die 9-stellige ID ein, die Mixel Remote auf dem anderen Gerät zeigt.", "Saisissez l’ID à 9 chiffres affiché par Mixel Remote sur l’autre appareil.", "Inserisca l’ID di 9 cifre mostrato da Mixel Remote sull’altro dispositivo."),
    "Enter the 9-digit ID shown on the other computer.": ("Geben Sie die 9-stellige ID ein, die der andere Computer zeigt.", "Saisissez l’ID à 9 chiffres affiché sur l’autre ordinateur.", "Inserisca l’ID di 9 cifre mostrato sull’altro computer."),
    "Enter the code from the authenticator entry “%@”.": ("Geben Sie den Code aus dem Authenticator-Eintrag «%@» ein.", "Saisissez le code de l’entrée d’authentificateur « %@ ».", "Inserisca il codice della voce dell’autenticatore «%@»."),
    "Expires %@": ("Läuft ab am %@", "Expire le %@", "Scade il %@"),
    "File transfer was not allowed for this session.": ("Die Dateiübertragung wurde für diese Sitzung nicht erlaubt.", "Le transfert de fichiers n’a pas été autorisé pour cette session.", "Il trasferimento di file non è stato consentito per questa sessione."),
    "General": ("Allgemein", "Général", "Generale"),
    "How do I help someone on their phone?": ("Wie helfe ich jemandem auf dem Handy?", "Comment aider quelqu’un sur son téléphone ?", "Come aiuto qualcuno sul suo telefono?"),
    "I accept the Terms of Service and the Data Processing Agreement": ("Ich akzeptiere die Nutzungsbedingungen und den Auftragsverarbeitungsvertrag", "J’accepte les conditions d’utilisation et l’accord de traitement des données", "Accetto le condizioni d’uso e l’accordo sul trattamento dei dati"),
    "I confirm sharing this whole display": ("Ich bestätige, diesen ganzen Bildschirm zu teilen", "Je confirme le partage de tout cet écran", "Confermo la condivisione dell’intero schermo"),
    "I confirm that approved users may see this display without asking.": ("Ich bestätige, dass berechtigte Personen diesen Bildschirm ohne Rückfrage sehen dürfen.", "Je confirme que les personnes autorisées peuvent voir cet écran sans demander.", "Confermo che le persone autorizzate possono vedere questo schermo senza chiedere."),
    "ID from the phone": ("ID vom Handy", "ID du téléphone", "ID dal telefono"),
    "Input delivery stalled; the session has been stopped": ("Die Eingaben kamen nicht mehr an; die Sitzung wurde beendet", "Les saisies ne passaient plus ; la session a été arrêtée", "Gli input non arrivavano più; la sessione è stata interrotta"),
    "Invalid native video frame": ("Ungültiges Videobild", "Image vidéo invalide", "Fotogramma video non valido"),
    "Invitation sent again to %@.": ("Einladung erneut an %@ gesendet.", "Invitation renvoyée à %@.", "Invito inviato di nuovo a %@."),
    "Invitation sent to %@.": ("Einladung an %@ gesendet.", "Invitation envoyée à %@.", "Invito inviato a %@."),
    "It can no longer be reached or connect until it is added again.": ("Er ist danach nicht mehr erreichbar und kann nicht verbinden, bis er wieder hinzugefügt wird.", "Il ne sera plus joignable et ne pourra plus se connecter tant qu’il n’est pas ajouté à nouveau.", "Non sarà più raggiungibile né potrà connettersi finché non verrà aggiunto di nuovo."),
    "Its running sessions end at once. It can be added again later.": ("Laufende Sitzungen enden sofort. Es kann später wieder hinzugefügt werden.", "Ses sessions en cours se terminent immédiatement. Il pourra être ajouté à nouveau plus tard.", "Le sessioni in corso terminano subito. Potrà essere aggiunto di nuovo in seguito."),
    "Keyboard": ("Tastatur", "Clavier", "Tastiera"),
    "Last seen %d days ago": ("Zuletzt gesehen vor %d Tagen", "Vu pour la dernière fois il y a %d jours", "Visto l’ultima volta %d giorni fa"),
    "Last seen %d hours ago": ("Zuletzt gesehen vor %d Stunden", "Vu pour la dernière fois il y a %d heures", "Visto l’ultima volta %d ore fa"),
    "Last seen %d minutes ago": ("Zuletzt gesehen vor %d Minuten", "Vu pour la dernière fois il y a %d minutes", "Visto l’ultima volta %d minuti fa"),
    "Leave": ("Verlassen", "Quitter", "Esci"),
    "Lock when done": ("Am Ende sperren", "Verrouiller à la fin", "Blocca alla fine"),
    "Looking up this ID…": ("Diese ID wird gesucht…", "Recherche de cet ID…", "Ricerca di questo ID…"),
    "Manage %@": ("%@ verwalten", "Gérer %@", "Gestisci %@"),
    "Meeting link": ("Meeting-Link", "Lien de réunion", "Link della riunione"),
    "Messages appear here.": ("Nachrichten erscheinen hier.", "Les messages apparaissent ici.", "I messaggi appaiono qui."),
    "Mixel Remote Help": ("Mixel Remote Hilfe", "Aide Mixel Remote", "Aiuto di Mixel Remote"),
    "Mixel Remote e-mails the invitation link. Administrators and technicians count against the technicians of your plan.": ("Mixel Remote sendet den Einladungslink per E-Mail. Administratoren und Techniker zählen zu den Technikern Ihres Plans.", "Mixel Remote envoie le lien d’invitation par e-mail. Les administrateurs et techniciens comptent parmi les techniciens de votre forfait.", "Mixel Remote invia il link d’invito per e-mail. Amministratori e tecnici contano tra i tecnici del Suo piano."),
    "Mixel Remote reconnects by itself. Device status appears once it is connected; you can still choose Connect.": ("Mixel Remote verbindet sich selbst wieder. Der Gerätestatus erscheint, sobald die Verbindung steht; Verbinden ist trotzdem möglich.", "Mixel Remote se reconnecte tout seul. L’état des appareils s’affiche une fois connecté ; vous pouvez quand même choisir Connecter.", "Mixel Remote si riconnette da solo. Lo stato dei dispositivi appare quando è connesso; può comunque scegliere Connetti."),
    "Mixel account": ("Mixel-Konto", "Compte Mixel", "Account Mixel"),
    "Mixel team account": ("Mixel-Teamkonto", "Compte de l’équipe Mixel", "Account del team Mixel"),
    "More": ("Mehr", "Plus", "Altro"),
    "Move": ("Verschieben", "Déplacer", "Sposta"),
    "Move %@ to a group": ("%@ in eine Gruppe verschieben", "Déplacer %@ dans un groupe", "Sposta %@ in un gruppo"),
    "Name of this computer": ("Name dieses Computers", "Nom de cet ordinateur", "Nome di questo computer"),
    "Native preview %@ · for testing only, not for customers yet.": ("Native Vorschau %@ · nur zum Testen, noch nicht für Kunden.", "Aperçu natif %@ · pour les tests uniquement, pas encore pour les clients.", "Anteprima nativa %@ · solo per test, non ancora per i clienti."),
    "Needed so a technician can use keyboard and mouse here.": ("Nötig, damit ein Techniker hier Tastatur und Maus verwenden kann.", "Nécessaire pour qu’un technicien puisse utiliser le clavier et la souris ici.", "Necessario perché un tecnico possa usare qui tastiera e mouse."),
    "Needed to share this screen with a technician.": ("Nötig, um diesen Bildschirm mit einem Techniker zu teilen.", "Nécessaire pour partager cet écran avec un technicien.", "Necessario per condividere questo schermo con un tecnico."),
    "New group": ("Neue Gruppe", "Nouveau groupe", "Nuovo gruppo"),
    "On an iPhone the person taps “Start sharing my screen”; on Android they allow the session.": ("Auf dem iPhone tippt die Person auf «Bildschirm teilen starten»; auf Android erlaubt sie die Sitzung.", "Sur iPhone, la personne touche « Commencer le partage de mon écran » ; sur Android, elle autorise la session.", "Su iPhone la persona tocca «Avvia condivisione dello schermo»; su Android consente la sessione."),
    "One-time password, if provided": ("Einmalpasswort, falls erhalten", "Mot de passe à usage unique, si fourni", "Password monouso, se fornita"),
    "Online · ID %@": ("Online · ID %@", "En ligne · ID %@", "Online · ID %@"),
    "Open Mixel Remote": ("Mixel Remote öffnen", "Ouvrir Mixel Remote", "Apri Mixel Remote"),
    "Or a personal space for your own devices": ("Oder ein persönlicher Bereich für Ihre eigenen Geräte", "Ou un espace personnel pour vos propres appareils", "Oppure uno spazio personale per i Suoi dispositivi"),
    "Page %d · %d devices.": ("Seite %d · %d Geräte.", "Page %d · %d appareils.", "Pagina %d · %d dispositivi."),
    "Pair this computer with %@ for %@?": ("Diesen Computer mit %@ für %@ koppeln?", "Associer cet ordinateur à %@ pour %@ ?", "Associare questo computer a %@ per %@?"),
    "Partner connection ID": ("Verbindungs-ID des Partners", "ID de connexion du partenaire", "ID di connessione del partner"),
    "Personal account": ("Persönliches Konto", "Compte personnel", "Account personale"),
    "Personal devices": ("Persönliche Geräte", "Appareils personnels", "Dispositivi personali"),
    "Preview for testing": ("Vorschau zum Testen", "Aperçu pour les tests", "Anteprima per i test"),
    "Print jobs": ("Druckaufträge", "Travaux d’impression", "Stampe"),
    "Privacy screen": ("Sichtschutz", "Écran de confidentialité", "Schermo privato"),
    "Quit Mixel Remote": ("Mixel Remote beenden", "Quitter Mixel Remote", "Esci da Mixel Remote"),
    "Record": ("Aufzeichnen", "Enregistrer", "Registra"),
    "Recording saved": ("Aufzeichnung gespeichert", "Enregistrement sauvegardé", "Registrazione salvata"),
    "Recording video": ("Video wird aufgezeichnet", "Enregistrement vidéo en cours", "Registrazione video in corso"),
    "Register this computer with your personal account.": ("Registrieren Sie diesen Computer mit Ihrem persönlichen Konto.", "Enregistrez cet ordinateur avec votre compte personnel.", "Registri questo computer con il Suo account personale."),
    "Remote screen": ("Entfernter Bildschirm", "Écran distant", "Schermo remoto"),
    "Remove %@": ("%@ entfernen", "Retirer %@", "Rimuovi %@"),
    "Remove from company": ("Aus der Firma entfernen", "Retirer de l’entreprise", "Rimuovi dall’azienda"),
    "Remove this device?": ("Dieses Gerät entfernen?", "Retirer cet appareil ?", "Rimuovere questo dispositivo?"),
    "Rollout": ("Verteilung", "Déploiement", "Distribuzione"),
    "Run the command on each computer (as administrator, or with your deployment tool). The link is shown only now.": ("Führen Sie den Befehl auf jedem Computer aus (als Administrator oder mit Ihrem Verteilwerkzeug). Der Link wird nur jetzt angezeigt.", "Exécutez la commande sur chaque ordinateur (en administrateur ou avec votre outil de déploiement). Le lien n’est affiché que maintenant.", "Esegua il comando su ogni computer (come amministratore o con il Suo strumento di distribuzione). Il link viene mostrato solo ora."),
    "Saved. This computer is removed as soon as Mixel Remote can be reached.": ("Gespeichert. Dieser Computer wird entfernt, sobald Mixel Remote erreichbar ist.", "Enregistré. Cet ordinateur sera retiré dès que Mixel Remote sera joignable.", "Salvato. Questo computer verrà rimosso non appena Mixel Remote sarà raggiungibile."),
    "Saving recording…": ("Aufzeichnung wird gespeichert…", "Enregistrement en cours de sauvegarde…", "Salvataggio della registrazione…"),
    "Set up this phone": ("Dieses Handy einrichten", "Configurer ce téléphone", "Configura questo telefono"),
    "Sharing is off. Confirming with the connection service.": ("Freigabe ist aus. Wird mit dem Verbindungsdienst bestätigt.", "Le partage est désactivé. Confirmation auprès du service de connexion.", "La condivisione è disattivata. Conferma con il servizio di connessione."),
    "Show pointer": ("Zeiger zeigen", "Afficher le pointeur", "Mostra puntatore"),
    "Sign in to access managed devices.": ("Melden Sie sich an, um auf verwaltete Geräte zuzugreifen.", "Connectez-vous pour accéder aux appareils gérés.", "Acceda per raggiungere i dispositivi gestiti."),
    "Sign in with your Mixel ID. Your browser opens; come back here when it says you are signed in.": ("Melden Sie sich mit Ihrer Mixel ID an. Ihr Browser öffnet sich; kommen Sie hierher zurück, sobald er die Anmeldung bestätigt.", "Connectez-vous avec votre Mixel ID. Votre navigateur s’ouvre ; revenez ici lorsqu’il indique que vous êtes connecté.", "Acceda con il Suo Mixel ID. Si apre il browser; torni qui quando indica che l’accesso è riuscito."),
    "Sign in…": ("Anmelden…", "Se connecter…", "Accedi…"),
    "Signed in": ("Angemeldet", "Connecté", "Accesso eseguito"),
    "Sound": ("Ton", "Son", "Audio"),
    "Start Mixel Remote when this computer starts": ("Mixel Remote beim Start dieses Computers öffnen", "Ouvrir Mixel Remote au démarrage de cet ordinateur", "Avvia Mixel Remote all’avvio di questo computer"),
    "Start a permitted recording after the remote screen appears": ("Starten Sie eine erlaubte Aufzeichnung, sobald der entfernte Bildschirm erscheint", "Lancez un enregistrement autorisé une fois l’écran distant affiché", "Avvii una registrazione consentita dopo che appare lo schermo remoto"),
    "Stays signed in on this computer": ("Bleibt auf diesem Computer angemeldet", "Reste connecté sur cet ordinateur", "Resta connesso su questo computer"),
    "Technician %@ was invited by the technician who is connected.": ("Techniker %@ wurde vom verbundenen Techniker eingeladen.", "Le technicien %@ a été invité par le technicien connecté.", "Il tecnico %@ è stato invitato dal tecnico connesso."),
    "Text to type on the remote computer": ("Text, der auf dem entfernten Computer getippt wird", "Texte à saisir sur l’ordinateur distant", "Testo da digitare sul computer remoto"),
    "The clipboard was not allowed for this session.": ("Die Zwischenablage wurde für diese Sitzung nicht erlaubt.", "Le presse-papiers n’a pas été autorisé pour cette session.", "Gli appunti non sono stati consentiti per questa sessione."),
    "The other computer cannot take a second technician directly. Use a meeting link instead.": ("Der andere Computer kann keinen zweiten Techniker direkt aufnehmen. Verwenden Sie stattdessen einen Meeting-Link.", "L’autre ordinateur ne peut pas accueillir directement un deuxième technicien. Utilisez plutôt un lien de réunion.", "L’altro computer non può accogliere direttamente un secondo tecnico. Usi invece un link di riunione."),
    "The person installs Mixel Quick Support from the App Store or Google Play.": ("Die Person installiert Mixel Quick Support aus dem App Store oder von Google Play.", "La personne installe Mixel Quick Support depuis l’App Store ou Google Play.", "La persona installa Mixel Quick Support dall’App Store o da Google Play."),
    "The person opens Mixel Quick Support on their phone and tells you the ID it shows.": ("Die Person öffnet Mixel Quick Support auf dem Handy und nennt Ihnen die angezeigte ID.", "La personne ouvre Mixel Quick Support sur son téléphone et vous indique l’ID affiché.", "La persona apre Mixel Quick Support sul telefono e Le comunica l’ID mostrato."),
    "The recording stopped early. The playable portion was saved.": ("Die Aufzeichnung endete vorzeitig. Der abspielbare Teil wurde gespeichert.", "L’enregistrement s’est arrêté plus tôt. La partie lisible a été sauvegardée.", "La registrazione si è interrotta prima. La parte riproducibile è stata salvata."),
    "The remote computer could not type this text.": ("Der entfernte Computer konnte diesen Text nicht tippen.", "L’ordinateur distant n’a pas pu saisir ce texte.", "Il computer remoto non ha potuto digitare questo testo."),
    "The technician wants to show you their screen.": ("Der Techniker möchte Ihnen seinen Bildschirm zeigen.", "Le technicien souhaite vous montrer son écran.", "Il tecnico desidera mostrarLe il suo schermo."),
    "They can see your screen and use keyboard and mouse.": ("Er sieht Ihren Bildschirm und kann Tastatur und Maus verwenden.", "Il voit votre écran et peut utiliser le clavier et la souris.", "Vede il Suo schermo e può usare tastiera e mouse."),
    "They can see your screen.": ("Er sieht Ihren Bildschirm.", "Il voit votre écran.", "Vede il Suo schermo."),
    "They open it and read you the 9-digit ID it shows.": ("Sie öffnet die App und liest Ihnen die 9-stellige ID vor.", "Elle l’ouvre et vous lit l’ID à 9 chiffres affiché.", "La apre e Le legge l’ID di 9 cifre mostrato."),
    "This app controls other devices; it cannot be controlled itself.": ("Diese App steuert andere Geräte; sie selbst kann nicht gesteuert werden.", "Cette app contrôle d’autres appareils ; elle ne peut pas être contrôlée elle-même.", "Questa app controlla altri dispositivi; non può essere controllata a sua volta."),
    "This computer is not set up yet.": ("Dieser Computer ist noch nicht eingerichtet.", "Cet ordinateur n’est pas encore configuré.", "Questo computer non è ancora configurato."),
    "This computer was removed from Mixel Remote.": ("Dieser Computer wurde aus Mixel Remote entfernt.", "Cet ordinateur a été retiré de Mixel Remote.", "Questo computer è stato rimosso da Mixel Remote."),
    "This computer's registration changed since; nothing was removed.": ("Die Registrierung dieses Computers hat sich inzwischen geändert; nichts wurde entfernt.", "L’enregistrement de cet ordinateur a changé entre-temps ; rien n’a été retiré.", "La registrazione di questo computer è cambiata nel frattempo; non è stato rimosso nulla."),
    "This is the newest version.": ("Dies ist die neueste Version.", "C’est la version la plus récente.", "Questa è la versione più recente."),
    "This phone": ("Dieses Handy", "Ce téléphone", "Questo telefono"),
    "Tools": ("Werkzeuge", "Outils", "Strumenti"),
    "Type your e-mail address to confirm. This cannot be undone.": ("Geben Sie zur Bestätigung Ihre E-Mail-Adresse ein. Dies lässt sich nicht rückgängig machen.", "Saisissez votre adresse e-mail pour confirmer. Cette action est irréversible.", "Digiti il Suo indirizzo e-mail per confermare. L’operazione non può essere annullata."),
    "Valid for %d days": ("%d Tage gültig", "Valable %d jours", "Valido per %d giorni"),
    "Version": ("Version", "Version", "Versione"),
    "Version %@ is available.": ("Version %@ ist verfügbar.", "La version %@ est disponible.", "La versione %@ è disponibile."),
    "View and control": ("Ansehen und steuern", "Voir et contrôler", "Visualizza e controlla"),
    "Wake signal sent to %@. It shows Online when it has started.": ("Wecksignal an %@ gesendet. Es zeigt Online, sobald es gestartet ist.", "Signal de réveil envoyé à %@. Il s’affiche En ligne une fois démarré.", "Segnale di riattivazione inviato a %@. Mostra Online quando si è avviato."),
    "Windows (PowerShell, as administrator)": ("Windows (PowerShell, als Administrator)", "Windows (PowerShell, en administrateur)", "Windows (PowerShell, come amministratore)"),
    "You allowed the other person to save a video of this session.": ("Sie haben der anderen Person erlaubt, ein Video dieser Sitzung zu speichern.", "Vous avez autorisé l’autre personne à enregistrer une vidéo de cette session.", "Ha consentito all’altra persona di salvare un video di questa sessione."),
    "You type the ID under Connect and choose Connect.": ("Sie geben die ID unter Verbinden ein und wählen Verbinden.", "Vous saisissez l’ID sous Connecter et choisissez Connecter.", "Digita l’ID in Connetti e sceglie Connetti."),
    "Your colleague gets the invitation in Mixel Remote and joins this session. The person at the computer is asked first, unless unattended access is set up.": ("Ihre Kollegin oder Ihr Kollege erhält die Einladung in Mixel Remote und kommt in diese Sitzung. Die Person am Computer wird zuerst gefragt, ausser unbeaufsichtigter Zugriff ist eingerichtet.", "Votre collègue reçoit l’invitation dans Mixel Remote et rejoint cette session. La personne devant l’ordinateur est d’abord sollicitée, sauf si l’accès sans surveillance est configuré.", "Il Suo collega riceve l’invito in Mixel Remote e si unisce a questa sessione. Alla persona al computer viene chiesto prima, a meno che sia configurato l’accesso non presidiato."),
    "Your company gets a 14-day Business trial, no card needed. You become its owner and can invite your team.": ("Ihre Firma erhält 14 Tage Business zum Testen, ohne Karte. Sie werden Inhaber und können Ihr Team einladen.", "Votre entreprise reçoit un essai Business de 14 jours, sans carte. Vous en devenez propriétaire et pouvez inviter votre équipe.", "La Sua azienda riceve una prova Business di 14 giorni, senza carta. Ne diventa titolare e può invitare il Suo team."),
    "Your connection ID": ("Ihre Verbindungs-ID", "Votre ID de connexion", "Il Suo ID di connessione"),
    "macOS permissions": ("macOS-Berechtigungen", "Autorisations macOS", "Autorizzazioni macOS"),
    "off": ("aus", "désactivé", "disattivato"),
    "on": ("ein", "activé", "attivato"),
}

LANGS = ["de", "fr", "it"]


def keys():
    found = set()
    for path in glob.glob(os.path.join(HERE, 'Sources/**/*.swift'), recursive=True) + glob.glob(os.path.join(HERE, 'Mac/*.swift')) + glob.glob(os.path.join(HERE, 'iOS/*.swift')):
        text = open(path, encoding='utf-8').read()
        for match in re.finditer(r'\bL\("((?:[^"\\]|\\.)*)"', text):
            found.add(match.group(1).replace('\\"', '"'))
    return sorted(found)


def main():
    strings, missing = {}, []
    for key in keys():
        units = {}
        for index, language in enumerate(LANGS):
            value = TABLES[language]['texts'].get(key)
            if value is None and key in NATIVE:
                value = NATIVE[key][index]
            if value is None:
                missing.append(f'{language}: {key}')
                continue
            units[language] = {"stringUnit": {"state": "translated", "value": value}}
        strings[key] = {"localizations": units}
    if missing:
        print("Missing translations:\n  " + "\n  ".join(missing), file=sys.stderr)
        sys.exit(1)
    catalog = {"sourceLanguage": "en", "strings": strings, "version": "1.0"}
    out = os.path.join(HERE, 'Sources/MixelRemoteKit/Resources/Localizable.xcstrings')
    with open(out, 'w', encoding='utf-8') as handle:
        json.dump(catalog, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write('\n')
    print(f"{len(strings)} texts in {out} (en, {', '.join(LANGS)})")


if __name__ == '__main__':
    main()
