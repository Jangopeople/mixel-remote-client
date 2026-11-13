import type { LegalTexts } from './types';

const P = 'Mixel IT and Corporate Services GmbH, Rüterspüelstrasse 17, 8173 Neerach, Svizzera (IDI CHE-184.355.981)';

export const it: LegalTexts = {
  ui: {
    centre: 'Note legali',
    centreLead: 'Condizioni d’uso, protezione dei dati e trattamento per conto di terzi per Mixel Remote.',
    draftTitle: 'Bozza – non in vigore',
    draftText: 'Questi testi sono bozze in fase di revisione e non sono ancora vincolanti. La versione definitiva sarà pubblicata qui con la sua data.',
    version: 'Versione',
    updated: 'Aggiornato al',
    contents: 'Indice',
    otherLanguages: 'Disponibile anche in',
    columns: { name: 'Subresponsabile', purpose: 'Scopo', data: 'Dati', location: 'Luogo', safeguard: 'Garanzia per il trasferimento' },
    print: 'Stampa o salva come PDF',
  },

  terms: {
    title: 'Condizioni d’uso di Mixel Remote',
    summary: 'Il contratto tra la vostra azienda e Mixel per l’uso di Mixel Remote: abbonamenti, prova, fatturazione, disdetta, cancellazione, uso consentito, disponibilità e responsabilità.',
    sections: [
      { heading: '1. Fornitore e campo d’applicazione', body: [
        `Mixel Remote è fornito da ${P} («Mixel», «noi»). Le presenti condizioni si applicano al servizio Mixel Remote: il portale remote.mixel.ch, le app Mixel Remote per computer e telefoni, il visualizzatore web, le riunioni e il servizio di connessione (insieme il «Servizio»).`,
        'Il cliente è l’azienda o la persona che apre un account Mixel Remote (il «Cliente», «voi»). Chi accetta queste condizioni per un’azienda conferma di esserne autorizzato. L’accordo sul trattamento dei dati e l’informativa sulla privacy fanno parte di queste condizioni. Condizioni divergenti del Cliente si applicano solo se Mixel le accetta per iscritto.',
      ] },
      { heading: '2. Account, proprietari e membri', body: [
        'Chi si registra diventa proprietario dell’account aziendale. Proprietari e amministratori possono invitare persone come amministratori, tecnici o membri e decidere quali metodi di accesso sono consentiti e se l’accesso in due passaggi è obbligatorio. Il Cliente risponde di tutto ciò che viene fatto con il suo account e del rispetto di queste condizioni da parte dei suoi membri.',
        'Mantenete riservati i dati di accesso, usate se possibile l’accesso in due passaggi e segnalate senza indugio ogni sospetto di abuso a info@mixel.ch. Ogni accesso è personale; un login non può essere condiviso tra più persone.',
      ] },
      { heading: '3. Abbonamenti e prezzi', body: [
        'Valgono gli abbonamenti e i limiti indicati su remote.mixel.ch/pricing al momento dell’ordine:',
        { list: [
          'Personal: gratuito, solo per uso privato e non commerciale; 1 tecnico, 10 dispositivi, 1 sessione alla volta.',
          'Solo: CHF 12 al mese; 1 tecnico, 100 dispositivi, 1 sessione alla volta.',
          'Team: CHF 39 al mese; 5 tecnici, 500 dispositivi, 3 sessioni alla volta; gruppi di dispositivi e diritti di accesso.',
          'Business: CHF 89 al mese; 15 tecnici, 2000 dispositivi, 10 sessioni alla volta; single sign-on aziendale, registro, regole, accesso in due passaggi obbligatorio.',
          'Enterprise: su offerta individuale.',
          'Tecnico aggiuntivo: CHF 8 al mese.',
        ] },
        'I prezzi sono in franchi svizzeri, IVA svizzera dell’8,1 % inclusa. Valgono per la fatturazione annuale; con la fatturazione mensile sono più alti del 20 %. Mixel può modificare prezzi e prestazioni per i periodi di fatturazione futuri; ne informiamo i proprietari per e-mail almeno 30 giorni prima e il Cliente può disdire prima dell’entrata in vigore.',
      ] },
      { heading: '4. Prova gratuita', body: [
        'Una nuova azienda riceve una prova gratuita di 14 giorni dell’abbonamento Business. Non sono necessari dati di pagamento. La prova è concessa una volta per azienda.',
        'Se entro la fine della prova non viene scelto un abbonamento a pagamento, l’account diventa di sola lettura: nessuno può connettersi e dispositivi, membri e impostazioni possono solo essere visualizzati, rimossi o esportati. I dispositivi sono conservati per 30 giorni dall’inizio della sola lettura e poi rimossi automaticamente. Scegliendo un abbonamento a pagamento entro questi 30 giorni si ripristina l’uso completo.',
      ] },
      { heading: '5. Ordine, fatturazione e pagamento', body: [
        'Gli abbonamenti a pagamento si ordinano nel portale e si pagano con carta tramite il nostro fornitore di pagamenti Stripe. Un abbonamento acquistato durante la prova vale subito; il primo pagamento è dovuto il giorno in cui la prova sarebbe terminata. Gli abbonamenti si rinnovano automaticamente per lo stesso periodo (mese o anno) fino alla disdetta.',
        'Il passaggio a un abbonamento superiore e i tecnici aggiuntivi valgono subito e sono addebitati subito pro rata. Un abbonamento inferiore deve corrispondere al numero attuale di tecnici e dispositivi. Le fatture sono disponibili nella sezione Fatturazione del portale.',
        'Se un pagamento non riesce, l’account resta utilizzabile per un breve periodo in cui Stripe ritenta il pagamento. Se l’importo resta scoperto, l’account diventa di sola lettura come descritto al punto 4 fino al pagamento.',
      ] },
      { heading: '6. Durata e disdetta', body: [
        'Il contratto ha durata indeterminata. Proprietari e amministratori possono disdire un abbonamento a pagamento in qualsiasi momento nella sezione Fatturazione; la disdetta ha effetto alla fine del periodo di fatturazione in corso, dopodiché l’account diventa di sola lettura. Gli importi già pagati non vengono rimborsati, salvo diversa disposizione di legge.',
        'Mixel può disdire il contratto con un preavviso di 30 giorni per la fine di un periodo di fatturazione, e senza preavviso per giusti motivi, in particolare in caso di violazione grave o ripetuta del punto 8, di mancato pagamento o di un ordine legale o delle autorità.',
      ] },
      { heading: '7. Cancellazione dell’account e dei vostri dati', body: [
        'Il proprietario può cancellare l’intero account aziendale in qualsiasi momento nel portale (Impostazioni › Dati e cancellazione) o nell’app. La cancellazione è immediata: l’abbonamento termina subito senza rimborso per il periodo residuo, ogni dispositivo viene disconnesso e rimosso, gli inviti aperti vengono revocati e i dati dell’azienda, i membri e i loro account di accesso Mixel Remote vengono cancellati. Ogni membro può cancellare il proprio account allo stesso modo; l’ultimo proprietario cancella prima l’azienda o nomina un altro proprietario.',
        'Prima della cancellazione il proprietario può scaricare i dati dell’azienda (azienda, membri, dispositivi, gruppi e registro) in formato JSON o CSV. L’esportazione è possibile anche quando l’account è di sola lettura.',
        'Mixel conserva solo ciò che la legge richiede: i documenti contabili (nome dell’azienda, riferimenti Stripe, fatture e pagamenti) per 10 anni (art. 958f CO). I backup cifrati vengono sovrascritti entro 12 mesi; i dati cancellati non vengono ripristinati dai backup, salvo per rimediare a un disastro, e in tal caso vengono cancellati di nuovo.',
      ] },
      { heading: '8. Uso consentito e consenso della persona assistita', body: [
        'Mixel Remote consente l’accesso a computer e telefoni di altre persone. Il Cliente può connettersi solo a dispositivi di sua proprietà o da lui amministrati, oppure il cui utente ha dato il consenso. In particolare:',
        { list: [
          'Sessioni assistite: la persona al dispositivo deve accettare ogni sessione (Mixel Remote glielo chiede) e può terminarla in qualsiasi momento.',
          'L’accesso non presidiato, l’accesso alla schermata di accesso e l’accesso permanente possono essere configurati solo su dispositivi che il Cliente ha il diritto di amministrare, con la conoscenza dei loro utenti abituali e nel rispetto delle regole interne del Cliente e del diritto del lavoro e della protezione dei dati.',
          'Registrazioni, trasferimenti di file, schermate e l’assistente IA possono essere usati solo dove è lecito; se una sessione viene registrata, le persone coinvolte devono esserne informate in anticipo.',
          'Il Servizio non può essere usato per accessi non autorizzati, sorveglianza di persone senza base legale, frodi (ad esempio false chiamate di assistenza), diffusione di malware, violazione di diritti di terzi, invio di spam o qualsiasi altro scopo illecito.',
          'Non attaccate, sovraccaricate o decompilate il Servizio, non aggirate le sue misure di sicurezza o i suoi limiti e non rivendetelo senza un accordo scritto con Mixel.',
        ] },
        'Mixel può bloccare l’accesso in tutto o in parte in presenza di indizi concreti di abuso e ne informa il Cliente, salvo che la legge o la protezione di terzi lo impediscano. Il Cliente manleva Mixel da pretese di terzi derivanti dal suo uso illecito del Servizio.',
      ] },
      { heading: '9. Disponibilità e assistenza', body: [
        'Mixel gestisce il Servizio con diligenza professionale e punta a un’elevata disponibilità, senza garantire un funzionamento ininterrotto. La manutenzione avviene se possibile fuori dagli orari d’ufficio svizzeri. Le connessioni dipendono dalle reti e dai dispositivi di entrambe le parti, su cui Mixel non ha influenza. Gli abbonamenti a pagamento comprendono assistenza per e-mail e telefono durante gli orari d’ufficio svizzeri. Livelli di servizio valgono solo se convenuti per iscritto (Enterprise).',
        'Mixel sviluppa il Servizio e può modificare o sostituire funzioni, purché le funzioni essenziali dell’abbonamento scelto restino disponibili. Le app vanno mantenute aggiornate; le versioni più vecchie possono smettere di funzionare per motivi di sicurezza.',
      ] },
      { heading: '10. Dati del Cliente e riservatezza', body: [
        'Il Cliente conserva tutti i diritti sui suoi dati. Mixel usa i dati del Cliente solo per fornire il Servizio, come previsto dall’accordo sul trattamento dei dati. Il contenuto dello schermo, gli input da tastiera e mouse, i file, gli appunti e la chat di una sessione sono trasmessi cifrati tra i due dispositivi; Mixel non li memorizza. Le registrazioni sono salvate solo sul dispositivo del tecnico.',
        'Entrambe le parti trattano in modo riservato le informazioni non pubbliche dell’altra parte. Questo obbligo continua dopo la fine del contratto.',
      ] },
      { heading: '11. Protezione dei dati', body: [
        'Mixel tratta i dati personali secondo la legge federale sulla protezione dei dati (LPD) e, ove applicabile, il regolamento generale europeo sulla protezione dei dati (RGPD). Per i dati che il Cliente tratta con il Servizio, il Cliente è titolare del trattamento e Mixel responsabile del trattamento; si applica l’accordo sul trattamento dei dati. Per i propri scopi (account, fatturazione, sicurezza) Mixel è titolare; si veda l’informativa sulla privacy.',
      ] },
      { heading: '12. Diritti sul Servizio', body: [
        'Tutti i diritti sul Servizio, sulle app e sui loro contenuti restano a Mixel o ai suoi licenzianti. Il Cliente riceve, per la durata del contratto, un diritto non esclusivo e non trasferibile di usare il Servizio nell’ambito del suo abbonamento. I componenti open source sono soggetti alle proprie licenze.',
      ] },
      { heading: '13. Garanzia e responsabilità', body: [
        'Mixel elimina i difetti segnalati entro un termine ragionevole. Nella misura consentita dalla legge, Mixel risponde solo dei danni causati intenzionalmente o per negligenza grave; è esclusa la responsabilità per negligenza lieve, per danni indiretti e conseguenti, mancato guadagno, perdita di dati e interruzione dell’attività. La responsabilità è in ogni caso limitata agli importi pagati dal Cliente per il Servizio nei 12 mesi precedenti il danno. Restano riservate la responsabilità per danni alle persone e la responsabilità legale imperativa. L’abbonamento gratuito Personal e la prova sono forniti così come sono.',
        'Il Cliente è responsabile del backup dei dati sui suoi dispositivi. Mixel non risponde delle azioni dei tecnici sui dispositivi connessi.',
      ] },
      { heading: '14. Modifiche delle presenti condizioni', body: [
        'Mixel può modificare queste condizioni. I proprietari ne vengono informati per e-mail o nel portale almeno 30 giorni prima dell’entrata in vigore; la modifica si considera accettata se il Cliente continua a usare il Servizio dopo tale data senza disdire. Le modifiche a favore del Cliente o imposte dalla legge possono avere effetto prima.',
      ] },
      { heading: '15. Diritto applicabile e foro', body: [
        'Si applica il diritto svizzero, escluse le norme sul conflitto di leggi e la Convenzione di Vienna sulla vendita internazionale di merci. Il foro è Zurigo, Svizzera. Restano riservate le norme imperative a tutela dei consumatori del paese di domicilio di un consumatore. Se una disposizione è invalida, le altre restano valide.',
      ] },
    ],
  },

  privacy: {
    title: 'Informativa sulla privacy di Mixel Remote',
    summary: 'Quali dati personali Mixel tratta quando usate Mixel Remote, perché, dove sono memorizzati, per quanto tempo sono conservati e quali sono i vostri diritti.',
    sections: [
      { heading: '1. Titolare del trattamento', body: [
        `Titolare del trattamento qui descritto: ${P}. Contatto per la protezione dei dati: info@mixel.ch, +41 44 433 14 00, o per posta all’indirizzo sopra indicato.`,
        'Questa informativa riguarda il portale Mixel Remote (remote.mixel.ch), le app Mixel Remote, il visualizzatore web, le riunioni e il servizio di connessione. Quando un’azienda usa Mixel Remote per assistere i propri utenti o dispositivi, è quest’azienda a decidere su questi dati e ne è titolare; Mixel li tratta per suo conto secondo l’accordo sul trattamento dei dati. Per domande su tali dati rivolgetevi preferibilmente prima a quest’azienda.',
      ] },
      { heading: '2. Quali dati trattiamo', body: [
        { list: [
          'Dati dell’account: nome, indirizzo e-mail, metodo di accesso, password con hash (se ne usate una), impostazioni dell’accesso in due passaggi, lingua e il fornitore di accesso scelto (Mixel ID, Google, Microsoft o Apple, se offerti).',
          'Dati dell’azienda: nome dell’azienda, abbonamento, stato della prova e della fatturazione, membri e loro ruoli, inviti, gruppi di dispositivi, diritti di accesso, regole di accesso, domini e-mail, link di distribuzione (solo un hash del loro segreto) e l’accettazione delle condizioni (versione e data).',
          'Dati dei dispositivi: nome del dispositivo, sistema operativo, Remote ID, chiave pubblica del dispositivo, chi l’ha aggiunto e quando, il suo gruppo e – per inventario e stato, se usati – caratteristiche tecniche del dispositivo.',
          'Metadati di connessione: quale account o dispositivo si è connesso a quale dispositivo e quando, inizio e fine di una sessione, tipo di accesso (assistito o non presidiato), consenso dato, indirizzi IP e dati di rete necessari a stabilire la connessione, e log tecnici del servizio di connessione.',
          'Presenza: se un dispositivo è online, da quando e quando è stato visto l’ultima volta. Il servizio di connessione lo segnala ogni 30 secondi.',
          'Registro: azioni nell’azienda (dispositivi aggiunti, rinominati, spostati o rimossi, membri invitati o rimossi, impostazioni modificate, dati esportati) con data e persona che le ha eseguite.',
          'Contenuto delle sessioni: schermo, audio, input da tastiera e mouse, file, appunti e chat sono trasmessi con cifratura end-to-end tra i due dispositivi (WebRTC con chiavi legate ai dispositivi). I server relay inoltrano solo dati cifrati; Mixel non può leggerli e non li memorizza.',
          'Registrazioni: un tecnico può registrare una sessione; la registrazione è salvata solo sul suo dispositivo e non viene mai caricata su Mixel.',
          'Dati di fatturazione: nome dell’azienda, indirizzo di fatturazione, numero IVA, e-mail per le fatture, abbonamento scelto, fatture e pagamenti. I dati della carta sono inseriti direttamente presso Stripe; Mixel non vede mai i numeri di carta completi.',
          'Dati del sito web: quando visitate remote.mixel.ch, dati tecnicamente necessari (indirizzo IP, ora, pagina, browser) sono trattati da Cloudflare per fornire e proteggere il sito. Non usiamo cookie di tracciamento o pubblicitari; il browser memorizza solo la vostra sessione di accesso, la lingua e il tema dei colori.',
        ] },
      ] },
      { heading: '3. Scopi e basi giuridiche', body: [
        { list: [
          'Fornitura del Servizio, connessione dei dispositivi, applicazione dei diritti di accesso e dei limiti dell’abbonamento – esecuzione del contratto (art. 6 par. 1 lett. b RGPD).',
          'Sicurezza: protezione da abusi e attacchi, registro, log tecnici – interesse legittimo di Mixel e dei suoi clienti a un servizio sicuro (art. 6 par. 1 lett. f RGPD).',
          'Fatturazione e contabilità, conservazione – obbligo legale (art. 6 par. 1 lett. c RGPD, art. 958f CO) e contratto.',
          'E-mail di servizio (codici di accesso, inviti, avvisi sull’account) – contratto e interesse legittimo. Non inviamo newsletter senza il vostro consenso.',
          'Richieste di assistenza – contratto e interesse legittimo.',
        ] },
        'Secondo la LPD svizzera trattiamo i dati personali nel rispetto dei suoi principi (liceità, buona fede, proporzionalità, finalità, trasparenza e sicurezza dei dati).',
      ] },
      { heading: '4. Dove sono memorizzati i dati', body: [
        'La banca dati, il servizio di accesso, il servizio di connessione e i server relay di Mixel Remote funzionano su server gestiti da Mixel presso Hetzner in Germania (UE). I backup cifrati sono conservati presso Hetzner in Germania. Il portale e la distribuzione del sito avvengono tramite la rete mondiale di Cloudflare. I pagamenti sono elaborati da Stripe. Le e-mail di servizio sono inviate tramite Resend. L’elenco completo con luoghi e garanzie si trova nella pagina dei subresponsabili.',
        'La Svizzera riconosce la Germania e l’UE come Stati con una protezione adeguata dei dati. Quando i dati sono trattati negli Stati Uniti (Cloudflare, Stripe, Resend), il trasferimento si basa sullo Swiss-U.S. e sull’EU-U.S. Data Privacy Framework se il destinatario è certificato, nonché sulle clausole contrattuali tipo dell’UE con l’addendum svizzero.',
      ] },
      { heading: '5. Destinatari', body: [
        'All’interno di un’azienda: proprietari e amministratori vedono membri, dispositivi e registro dell’azienda; i tecnici vedono i dispositivi che possono gestire; i membri vedono i dispositivi che hanno aggiunto. La persona al dispositivo vede chi si connette.',
        'All’esterno: solo i subresponsabili elencati nella relativa pagina, nella misura necessaria al loro compito e vincolati per contratto; Stripe tratta alcuni dati (prevenzione delle frodi, obblighi normativi) come titolare autonomo; Google, Microsoft o Apple se accedete con loro, secondo le loro informative; le autorità se la legge ci obbliga. Non vendiamo dati personali.',
      ] },
      { heading: '6. Per quanto tempo conserviamo i dati', body: [
        { list: [
          'Dati dell’account e dell’azienda: finché l’account esiste. Quando il proprietario cancella l’azienda, o voi cancellate il vostro account, i dati vengono cancellati immediatamente.',
          'Dispositivi: fino alla loro rimozione; i dispositivi di un account di sola lettura sono rimossi automaticamente 30 giorni dopo l’inizio della sola lettura. La chiave e il Remote ID del dispositivo restano bloccati, in modo da non essere mai assegnati a un altro computer.',
          'Presenza: l’ora dell’ultima presenza è conservata fino alla rimozione del dispositivo.',
          'Registro: finché esiste l’account aziendale.',
          'Log tecnici del servizio di connessione: in file di dimensione limitata, sovrascritti automaticamente.',
          'Documenti contabili: 10 anni (art. 958f CO); dopo la cancellazione di un’azienda restano solo il nome dell’azienda, i riferimenti Stripe e dei conteggi, senza dati personali dei membri.',
          'Backup: cifrati; giornalieri per 7 giorni, settimanali per 4 settimane, mensili per 12 mesi, poi sovrascritti.',
        ] },
      ] },
      { heading: '7. I vostri diritti', body: [
        'Secondo la LPD e il RGPD potete chiedere l’accesso ai vostri dati, la rettifica di dati inesatti, la cancellazione, la limitazione del trattamento, la consegna o la portabilità dei dati, e opporvi a un trattamento basato sull’interesse legittimo. Potete revocare un consenso in qualsiasi momento con effetto per il futuro.',
        'Molto potete farlo da soli: vedete e correggete il vostro profilo nel portale, cancellate il vostro account in Account, e il proprietario esporta e cancella i dati dell’azienda in Impostazioni › Dati e cancellazione. Per il resto scrivete a info@mixel.ch; rispondiamo entro 30 giorni. Possiamo chiedervi di confermare la vostra identità.',
        'Potete presentare reclamo all’Incaricato federale della protezione dei dati e della trasparenza (IFPDT, www.edoeb.admin.ch) o, nell’UE, all’autorità di controllo del vostro paese.',
      ] },
      { heading: '8. Sicurezza', body: [
        'Tutte le connessioni sono cifrate (TLS; contenuto delle sessioni end-to-end con WebRTC DTLS/SRTP). I dispositivi si identificano con la propria chiave; ogni azione è verificata nella banca dati in base ai ruoli e alle regole dell’azienda; sono disponibili l’accesso in due passaggi e regole di accesso aziendali; i backup sono cifrati; l’accesso del personale Mixel è limitato al necessario e registrato.',
      ] },
      { heading: '9. Minori', body: [
        'Mixel Remote si rivolge ad aziende e adulti. Le persone sotto i 16 anni possono usare l’abbonamento Personal solo con il consenso di un genitore.',
      ] },
      { heading: '10. Modifiche', body: [
        'Aggiorniamo questa informativa quando cambiano il Servizio o il diritto. La versione attuale è sempre disponibile qui; informiamo i proprietari delle modifiche importanti per e-mail o nel portale.',
      ] },
    ],
  },

  dpa: {
    title: 'Accordo sul trattamento dei dati (DPA) per Mixel Remote',
    summary: 'L’accordo ai sensi dell’art. 9 LPD e dell’art. 28 RGPD sui dati personali che Mixel tratta per conto del Cliente nel fornire Mixel Remote.',
    sections: [
      { heading: '1. Parti e campo d’applicazione', body: [
        `Il presente accordo sul trattamento dei dati («Accordo») è concluso tra il Cliente (titolare del trattamento) e ${P} (responsabile del trattamento, «Mixel»). Fa parte delle condizioni d’uso di Mixel Remote ed è accettato insieme ad esse all’apertura dell’account aziendale. Si applica a tutti i dati personali che Mixel tratta per conto del Cliente nel fornire il Servizio («Dati del Cliente»). Se questo Accordo e le condizioni d’uso divergono in materia di protezione dei dati, prevale questo Accordo.`,
      ] },
      { heading: '2. Oggetto, natura e scopo', body: [
        'Oggetto: fornitura di Mixel Remote – account aziendali, gestione dei dispositivi, connessioni tra dispositivi, presenza, registro, inviti e assistenza. Natura: memorizzazione, organizzazione, trasmissione e cancellazione di dati; inoltro di dati di sessione cifrati. Scopo: consentire al Cliente di assistere e amministrare a distanza i suoi dispositivi e utenti. Durata: durata del contratto più i termini di cancellazione del punto 10.',
      ] },
      { heading: '3. Interessati e categorie di dati', body: [
        { list: [
          'Interessati: proprietari, amministratori, tecnici e membri del Cliente; utenti dei dispositivi che il Cliente gestisce o assiste; persone invitate dal Cliente.',
          'Categorie: nomi, indirizzi e-mail, ruoli, dati di accesso; nomi dei dispositivi, sistemi operativi, Remote ID, chiavi dei dispositivi, caratteristiche tecniche; metadati di connessione (chi si è connesso a quale dispositivo e quando, indirizzi IP, consenso); presenza (online, ultima presenza); voci del registro; inviti.',
          'Il contenuto delle sessioni (schermo, audio, input, file, appunti, chat) è trasmesso con cifratura end-to-end tra i dispositivi. Mixel non vi ha accesso e non lo memorizza. Le registrazioni esistono solo sul dispositivo del tecnico.',
          'Dati particolarmente sensibili non sono trattati in modo mirato. Se tali dati compaiono su uno schermo durante una sessione, Mixel non li memorizza.',
        ] },
      ] },
      { heading: '4. Istruzioni', body: [
        'Mixel tratta i Dati del Cliente solo su istruzione documentata del Cliente. Le condizioni d’uso, questo Accordo e le impostazioni e azioni del Cliente nel Servizio costituiscono le istruzioni complete. Mixel informa senza indugio il Cliente se ritiene un’istruzione illecita. Un trattamento diverso avviene solo se richiesto dal diritto svizzero o europeo; Mixel ne informa prima il Cliente, salvo che la legge lo vieti.',
      ] },
      { heading: '5. Riservatezza', body: [
        'Mixel garantisce che le persone autorizzate a trattare i Dati del Cliente siano vincolate alla riservatezza e li trattino solo nella misura necessaria al Servizio.',
      ] },
      { heading: '6. Sicurezza del trattamento', body: [
        'Mixel adotta misure tecniche e organizzative adeguate descritte nell’allegato 1 e le adegua allo stato della tecnica. Mixel può sostituire misure con altre di livello almeno equivalente.',
      ] },
      { heading: '7. Subresponsabili', body: [
        'Il Cliente autorizza Mixel a ricorrere ai subresponsabili elencati su remote.mixel.ch/legal/subprocessors (allegato 2). Mixel informa il Cliente almeno 30 giorni prima di ogni subresponsabile nuovo o sostituito (per e-mail ai proprietari o nel portale). Il Cliente può opporsi entro tale termine per gravi motivi di protezione dei dati; in mancanza di una soluzione può disdire senza costi il Servizio interessato per la data della modifica.',
        'Mixel impone per contratto a ogni subresponsabile obblighi di protezione dei dati equivalenti a questo Accordo e ne resta responsabile verso il Cliente.',
      ] },
      { heading: '8. Trasferimenti all’estero', body: [
        'I Dati del Cliente sono memorizzati in Germania (UE). Un trasferimento verso un paese senza protezione adeguata avviene solo con le garanzie dell’art. 16 LPD e degli art. 44 segg. RGPD, in particolare le clausole contrattuali tipo dell’UE con l’addendum svizzero o una certificazione ai sensi dello Swiss-U.S. / EU-U.S. Data Privacy Framework. L’allegato 2 indica la garanzia per ogni subresponsabile.',
      ] },
      { heading: '9. Assistenza al Cliente', body: [
        { list: [
          'Diritti degli interessati: il Servizio consente al Cliente di consultare, rettificare, esportare (Impostazioni › Dati e cancellazione) e cancellare i dati in autonomia. Mixel inoltra al Cliente le richieste che riceve e lo aiuta quando non può rispondervi da solo.',
          'Violazioni della sicurezza dei dati: Mixel informa il Cliente senza ingiustificato ritardo, se possibile entro 48 ore dalla conoscenza, di una violazione della sicurezza dei Dati del Cliente, con le informazioni disponibili, e lo assiste nei suoi obblighi di notifica all’IFPDT, alle autorità di controllo e agli interessati.',
          'Mixel aiuta nelle valutazioni d’impatto sulla protezione dei dati e nelle consultazioni delle autorità, nella misura in cui riguardano il Servizio.',
        ] },
        'L’assistenza che va oltre le funzioni self-service può essere fatturata alle tariffe usuali di Mixel se è onerosa o causata dal Cliente.',
      ] },
      { heading: '10. Cancellazione e restituzione', body: [
        'Il Cliente può esportare i suoi dati in qualsiasi momento e cancellare l’account aziendale da solo. Alla cancellazione i Dati del Cliente vengono cancellati immediatamente dai sistemi di produzione; i dispositivi vengono disconnessi e rimossi. I backup cifrati vengono sovrascritti entro 12 mesi. In virtù di obblighi legali sono conservati solo i documenti contabili (10 anni), che non contengono Dati del Cliente relativi a membri o utenti dei dispositivi. Se il contratto termina senza cancellazione da parte del Cliente, i dati di un account di sola lettura sono cancellati secondo le condizioni d’uso.',
      ] },
      { heading: '11. Prove e controlli', body: [
        'Mixel fornisce le informazioni necessarie a dimostrare il rispetto di questo Accordo. Il Cliente può verificarne il rispetto una volta per anno civile, con 30 giorni di preavviso, durante gli orari d’ufficio, a proprie spese e senza accesso ai dati di altri clienti o a segreti commerciali; informazioni scritte e rapporti esistenti hanno la precedenza sui controlli in loco. I controlli di un’autorità competente restano possibili in ogni momento.',
      ] },
      { heading: '12. Responsabilità e durata', body: [
        'La responsabilità è disciplinata dalle condizioni d’uso. Questo Accordo vale finché Mixel tratta Dati del Cliente. Si applica il diritto svizzero; il foro è Zurigo.',
      ] },
      { heading: 'Allegato 1 – Misure tecniche e organizzative', body: [
        { list: [
          'Cifratura: TLS 1.2 o superiore per ogni connessione; contenuto delle sessioni cifrato end-to-end (WebRTC DTLS/SRTP, chiavi legate alle identità dei dispositivi); backup cifrati prima di lasciare il server.',
          'Controllo degli accessi: ogni azione verificata nella banca dati (ruolo, azienda, metodo di accesso, regola dei due passaggi); nessun accesso diretto alla banca dati per le app; account di servizio separati con diritti minimi per fatturazione, presenza ed e-mail.',
          'Identità dei dispositivi: ogni dispositivo ha la propria chiave; i dispositivi rimossi vengono revocati subito e le sessioni in corso terminano al controllo successivo; i Remote ID non vengono mai riutilizzati.',
          'Autenticazione: password con hash, codici via e-mail, accesso in due passaggi facoltativo o obbligatorio, regole di accesso aziendali.',
          'Separazione: i dati di ogni azienda sono separati per tenant nella banca dati e verificati a ogni chiamata.',
          'Registrazione: registro per azienda; registro di registrazione dei dispositivi; log tecnici di dimensione limitata.',
          'Disponibilità: backup cifrati giornalieri (7 giornalieri, 4 settimanali, 12 mensili) su una memoria separata; monitoraggio dei servizi.',
          'Organizzazione: personale vincolato alla riservatezza, accesso secondo il principio del privilegio minimo, aggiornamenti di sicurezza regolari, processo di gestione degli incidenti.',
          'Minimizzazione: nessuna memorizzazione del contenuto delle sessioni; registrazioni solo sul dispositivo del tecnico; segreti di inviti e link di distribuzione memorizzati solo come hash.',
        ] },
      ] },
      { heading: 'Allegato 2 – Subresponsabili', body: [
        'L’elenco attuale con scopo, luogo e garanzia:',
        { table: 'subprocessors' },
      ] },
    ],
  },

  subprocessors: {
    title: 'Subresponsabili di Mixel Remote',
    summary: 'Le aziende che trattano dati personali per Mixel Remote per conto di Mixel, a quale scopo e dove.',
    sections: [
      { heading: 'Elenco attuale', body: [
        { table: 'subprocessors' },
        'Mixel gestisce in proprio su questi server la banca dati, l’accesso, i servizi di connessione, relay, riunione e fatturazione. Mixel informa i clienti almeno 30 giorni prima di aggiungere o sostituire un subresponsabile (si veda il punto 7 dell’accordo sul trattamento dei dati).',
        'Non sono subresponsabili: Google, Microsoft e Apple quando una persona sceglie di accedere con loro (agiscono secondo le proprie condizioni); il server IA dell’azienda stessa, se usa l’assistente IA; i sistemi di posta e di gestione dei dispositivi dell’azienda stessa.',
      ] },
    ],
  },

  subprocessorRows: [
    { purpose: 'Server per banca dati, accesso, servizi di connessione, relay, riunione e fatturazione; backup cifrati', data: 'Tutti i dati del Servizio tranne i dati di pagamento', location: 'Germania (UE)', safeguard: 'Protezione adeguata (UE)' },
    { purpose: 'Distribuzione e protezione di remote.mixel.ch e delle interfacce (DNS, TLS, protezione DDoS, distribuzione dei contenuti, hosting del portale)', data: 'Indirizzi IP, dati delle richieste; il traffico cifrato transita', location: 'Rete mondiale, Stati Uniti', safeguard: 'Data Privacy Framework (EU-U.S. e Swiss-U.S.); clausole contrattuali tipo dell’UE con addendum svizzero' },
    { purpose: 'Pagamenti, abbonamenti e fatture', data: 'Nome dell’azienda, indirizzo di fatturazione, numero IVA, e-mail di fatturazione, dati di pagamento, fatture', location: 'Irlanda (UE), Stati Uniti', safeguard: 'Protezione adeguata (UE); Data Privacy Framework; clausole contrattuali tipo dell’UE con addendum svizzero' },
    { purpose: 'Invio di e-mail di servizio: codici di accesso, inviti, avvisi sull’account', data: 'Indirizzo e-mail, nome, nome dell’azienda, contenuto dell’e-mail', location: 'Stati Uniti', safeguard: 'Clausole contrattuali tipo dell’UE con addendum svizzero' },
  ],
};
