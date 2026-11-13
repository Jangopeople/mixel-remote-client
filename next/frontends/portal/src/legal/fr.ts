import type { LegalTexts } from './types';

const P = 'Mixel IT and Corporate Services GmbH, Rüterspüelstrasse 17, 8173 Neerach, Suisse (IDE CHE-184.355.981)';

export const fr: LegalTexts = {
  ui: {
    centre: 'Informations juridiques',
    centreLead: 'Conditions d’utilisation, protection des données et sous-traitance pour Mixel Remote.',
    draftTitle: 'Projet – pas en vigueur',
    draftText: 'Ces textes sont des projets en cours d’examen et ne sont pas encore contraignants. La version définitive sera publiée ici avec sa date.',
    version: 'Version',
    updated: 'Mise à jour',
    contents: 'Sommaire',
    otherLanguages: 'Aussi disponible en',
    columns: { name: 'Sous-traitant', purpose: 'Finalité', data: 'Données', location: 'Lieu', safeguard: 'Garantie pour le transfert' },
    print: 'Imprimer ou enregistrer en PDF',
  },

  terms: {
    title: 'Conditions d’utilisation de Mixel Remote',
    summary: 'Le contrat entre votre entreprise et Mixel pour l’utilisation de Mixel Remote : abonnements, essai, facturation, résiliation, suppression, utilisation admise, disponibilité et responsabilité.',
    sections: [
      { heading: '1. Prestataire et champ d’application', body: [
        `Mixel Remote est fourni par ${P} (« Mixel », « nous »). Les présentes conditions s’appliquent au service Mixel Remote : le portail remote.mixel.ch, les applications Mixel Remote pour ordinateurs et téléphones, le visualiseur web, les réunions et le service de connexion (ensemble le « Service »).`,
        'Le client est l’entreprise ou la personne qui ouvre un compte Mixel Remote (le « Client », « vous »). Quiconque accepte ces conditions pour une entreprise confirme y être autorisé. L’accord de sous-traitance et la politique de confidentialité font partie de ces conditions. Les conditions divergentes du Client ne s’appliquent que si Mixel les accepte par écrit.',
      ] },
      { heading: '2. Compte, propriétaires et membres', body: [
        'La personne qui s’inscrit devient propriétaire du compte de l’entreprise. Les propriétaires et administrateurs peuvent inviter des personnes comme administrateurs, techniciens ou membres et décider quelles méthodes de connexion sont autorisées et si la connexion en deux étapes est obligatoire. Le Client répond de tout ce qui est fait avec son compte et du respect de ces conditions par ses membres.',
        'Gardez vos identifiants confidentiels, utilisez si possible la connexion en deux étapes et signalez sans délai tout soupçon d’abus à info@mixel.ch. Chaque connexion est personnelle ; un identifiant ne peut pas être partagé entre plusieurs personnes.',
      ] },
      { heading: '3. Abonnements et prix', body: [
        'Les abonnements et leurs limites sont ceux indiqués sur remote.mixel.ch/pricing au moment de la commande :',
        { list: [
          'Personal : gratuit, uniquement pour un usage privé et non commercial ; 1 technicien, 10 appareils, 1 session à la fois.',
          'Solo : CHF 12 par mois ; 1 technicien, 100 appareils, 1 session à la fois.',
          'Team : CHF 39 par mois ; 5 techniciens, 500 appareils, 3 sessions à la fois ; groupes d’appareils et droits d’accès.',
          'Business : CHF 89 par mois ; 15 techniciens, 2000 appareils, 10 sessions à la fois ; authentification unique d’entreprise, journal, règles, connexion en deux étapes obligatoire.',
          'Enterprise : sur offre individuelle.',
          'Technicien supplémentaire : CHF 8 par mois.',
        ] },
        'Les prix sont en francs suisses, TVA suisse de 8,1 % incluse. Ils s’appliquent à la facturation annuelle ; avec la facturation mensuelle, ils sont 20 % plus élevés. Mixel peut modifier les prix et les prestations pour les périodes de facturation futures ; nous en informons les propriétaires par e-mail au moins 30 jours à l’avance et le Client peut résilier avant l’entrée en vigueur.',
      ] },
      { heading: '4. Essai gratuit', body: [
        'Une nouvelle entreprise bénéficie d’un essai gratuit de 14 jours de l’abonnement Business. Aucune donnée de paiement n’est nécessaire. L’essai est accordé une fois par entreprise.',
        'Si aucun abonnement payant n’est choisi à la fin de l’essai, le compte passe en lecture seule : personne ne peut se connecter, et les appareils, membres et réglages peuvent seulement être consultés, retirés ou exportés. Les appareils sont conservés 30 jours après le passage en lecture seule, puis retirés automatiquement. Choisir un abonnement payant pendant ces 30 jours rétablit l’usage complet.',
      ] },
      { heading: '5. Commande, facturation et paiement', body: [
        'Les abonnements payants se commandent dans le portail et se paient par carte via notre prestataire de paiement Stripe. Un abonnement acheté pendant l’essai s’applique immédiatement ; le premier paiement est dû le jour où l’essai aurait pris fin. Les abonnements se renouvellent automatiquement pour la même période (mois ou année) jusqu’à leur résiliation.',
        'Les changements vers un abonnement supérieur et les techniciens supplémentaires s’appliquent immédiatement et sont facturés au prorata sans attendre. Un abonnement inférieur doit correspondre au nombre actuel de techniciens et d’appareils. Les factures sont disponibles dans la rubrique Facturation du portail.',
        'Si un paiement échoue, le compte reste utilisable pendant un court délai durant lequel Stripe réessaie le paiement. Si le montant reste impayé, le compte passe en lecture seule comme décrit au chiffre 4 jusqu’au paiement.',
      ] },
      { heading: '6. Durée et résiliation', body: [
        'Le contrat est conclu pour une durée indéterminée. Les propriétaires et administrateurs peuvent résilier un abonnement payant à tout moment dans la rubrique Facturation ; la résiliation prend effet à la fin de la période de facturation en cours, puis le compte passe en lecture seule. Les montants déjà payés ne sont pas remboursés, sauf si la loi l’exige.',
        'Mixel peut résilier le contrat moyennant un préavis de 30 jours pour la fin d’une période de facturation, et sans préavis pour juste motif, notamment en cas de violation grave ou répétée du chiffre 8, de défaut de paiement ou d’ordre légal ou officiel.',
      ] },
      { heading: '7. Suppression du compte et de vos données', body: [
        'Le propriétaire peut supprimer à tout moment l’ensemble du compte de l’entreprise dans le portail (Réglages › Données et suppression) ou dans l’application. La suppression est immédiate : l’abonnement prend fin aussitôt sans remboursement de la période restante, chaque appareil est déconnecté et retiré, les invitations ouvertes sont révoquées, et les données de l’entreprise, les membres et leurs comptes de connexion Mixel Remote sont supprimés. Chaque membre peut supprimer son propre compte de la même manière ; le dernier propriétaire supprime d’abord l’entreprise ou désigne un autre propriétaire.',
        'Avant la suppression, le propriétaire peut télécharger les données de l’entreprise (entreprise, membres, appareils, groupes et journal) en JSON ou CSV. Cet export est également possible lorsque le compte est en lecture seule.',
        'Mixel ne conserve que ce que la loi exige : les pièces comptables (nom de l’entreprise, références Stripe, factures et paiements) pendant 10 ans (art. 958f CO). Les sauvegardes chiffrées sont écrasées dans un délai de 12 mois ; les données supprimées ne sont pas restaurées depuis les sauvegardes, sauf pour remédier à un sinistre, et sont alors supprimées à nouveau.',
      ] },
      { heading: '8. Utilisation admise et consentement de la personne aidée', body: [
        'Mixel Remote donne accès aux ordinateurs et téléphones d’autres personnes. Le Client ne peut se connecter qu’à des appareils qui lui appartiennent ou qu’il administre, ou dont l’utilisateur a donné son accord. En particulier :',
        { list: [
          'Sessions accompagnées : la personne devant l’appareil doit accepter chaque session (Mixel Remote le lui demande) et peut y mettre fin à tout moment.',
          'L’accès sans surveillance, l’accès à l’écran de connexion et l’accès permanent ne peuvent être configurés que sur des appareils que le Client est en droit d’administrer, au su de leurs utilisateurs habituels et conformément aux règles internes du Client ainsi qu’au droit du travail et de la protection des données.',
          'Les enregistrements, transferts de fichiers, captures d’écran et l’assistant IA ne peuvent être utilisés que lorsque c’est licite ; si une session est enregistrée, les personnes concernées doivent en être informées au préalable.',
          'Le Service ne peut pas être utilisé pour un accès non autorisé, la surveillance de personnes sans base légale, la fraude (par exemple de faux appels d’assistance), la diffusion de logiciels malveillants, la violation de droits de tiers, l’envoi de spam ou tout autre but illicite.',
          'N’attaquez pas le Service, ne le surchargez pas, ne le décompilez pas, ne contournez pas ses mesures de sécurité ou ses limites et ne le revendez pas sans accord écrit avec Mixel.',
        ] },
        'Mixel peut bloquer l’accès en tout ou partie en présence d’indices concrets d’abus et en informe le Client, sauf si la loi ou la protection de tiers s’y oppose. Le Client garantit Mixel contre les prétentions de tiers résultant de son utilisation illicite du Service.',
      ] },
      { heading: '9. Disponibilité et assistance', body: [
        'Mixel exploite le Service avec diligence professionnelle et vise une haute disponibilité, sans garantir un fonctionnement ininterrompu. La maintenance a lieu si possible en dehors des heures de bureau suisses. Les connexions dépendent des réseaux et appareils des deux côtés, sur lesquels Mixel n’a pas d’influence. Les abonnements payants comprennent une assistance par e-mail et téléphone pendant les heures de bureau suisses. Des niveaux de service ne s’appliquent que s’ils sont convenus par écrit (Enterprise).',
        'Mixel développe le Service et peut modifier ou remplacer des fonctions, pour autant que les fonctions essentielles de l’abonnement choisi restent disponibles. Les applications doivent être tenues à jour ; les anciennes versions peuvent cesser de fonctionner pour des raisons de sécurité.',
      ] },
      { heading: '10. Données du Client et confidentialité', body: [
        'Le Client conserve tous les droits sur ses données. Mixel n’utilise les données du Client que pour fournir le Service, comme le prévoit l’accord de sous-traitance. Le contenu de l’écran, les saisies clavier et souris, les fichiers, le presse-papiers et le chat d’une session sont transmis chiffrés entre les deux appareils ; Mixel ne les enregistre pas. Les enregistrements sont sauvegardés uniquement sur l’appareil du technicien.',
        'Les deux parties traitent de manière confidentielle les informations non publiques de l’autre partie. Cette obligation subsiste après la fin du contrat.',
      ] },
      { heading: '11. Protection des données', body: [
        'Mixel traite les données personnelles conformément à la loi fédérale sur la protection des données (LPD) et, lorsqu’il est applicable, au règlement général européen sur la protection des données (RGPD). Pour les données que le Client traite avec le Service, le Client est responsable du traitement et Mixel son sous-traitant ; l’accord de sous-traitance s’applique. Pour ses propres finalités (compte, facturation, sécurité), Mixel est responsable du traitement ; voir la politique de confidentialité.',
      ] },
      { heading: '12. Droits sur le Service', body: [
        'Tous les droits sur le Service, les applications et leur contenu restent à Mixel ou à ses concédants. Le Client reçoit, pour la durée du contrat, un droit non exclusif et non transmissible d’utiliser le Service dans le cadre de son abonnement. Les composants open source sont soumis à leurs propres licences.',
      ] },
      { heading: '13. Garantie et responsabilité', body: [
        'Mixel corrige les défauts signalés dans un délai raisonnable. Dans la mesure permise par la loi, Mixel ne répond que des dommages causés intentionnellement ou par négligence grave ; la responsabilité pour négligence légère, pour les dommages indirects et consécutifs, le manque à gagner, la perte de données et l’interruption d’exploitation est exclue. La responsabilité est dans tous les cas limitée aux montants payés par le Client pour le Service au cours des 12 mois précédant le dommage. La responsabilité pour les dommages corporels et la responsabilité légale impérative sont réservées. L’abonnement gratuit Personal et l’essai sont fournis en l’état.',
        'Le Client est responsable de la sauvegarde des données sur ses appareils. Mixel ne répond pas des actes des techniciens sur les appareils connectés.',
      ] },
      { heading: '14. Modification des présentes conditions', body: [
        'Mixel peut modifier ces conditions. Les propriétaires en sont informés par e-mail ou dans le portail au moins 30 jours avant l’entrée en vigueur ; la modification est réputée acceptée si le Client continue d’utiliser le Service après cette date sans résilier. Les modifications en faveur du Client ou imposées par la loi peuvent prendre effet plus tôt.',
      ] },
      { heading: '15. Droit applicable et for', body: [
        'Le droit suisse s’applique, à l’exclusion des règles de conflit de lois et de la Convention de Vienne sur la vente internationale de marchandises. Le for est à Zurich, Suisse. Les dispositions impératives de protection des consommateurs du pays de domicile d’un consommateur sont réservées. Si une disposition est invalide, les autres restent valables.',
      ] },
    ],
  },

  privacy: {
    title: 'Politique de confidentialité de Mixel Remote',
    summary: 'Quelles données personnelles Mixel traite lorsque vous utilisez Mixel Remote, pourquoi, où elles sont stockées, combien de temps elles sont conservées et quels sont vos droits.',
    sections: [
      { heading: '1. Responsable du traitement', body: [
        `Responsable du traitement décrit ici : ${P}. Contact pour la protection des données : info@mixel.ch, +41 44 433 14 00, ou par courrier à l’adresse ci-dessus.`,
        'Cette politique couvre le portail Mixel Remote (remote.mixel.ch), les applications Mixel Remote, le visualiseur web, les réunions et le service de connexion. Lorsqu’une entreprise utilise Mixel Remote pour assister ses propres utilisateurs ou appareils, cette entreprise décide de ces données et en est responsable ; Mixel les traite pour son compte selon l’accord de sous-traitance. Adressez de préférence vos questions sur ces données d’abord à cette entreprise.',
      ] },
      { heading: '2. Données traitées', body: [
        { list: [
          'Données du compte : nom, adresse e-mail, méthode de connexion, mot de passe haché (si vous en utilisez un), réglages de la connexion en deux étapes, langue et le fournisseur de connexion choisi (Mixel ID, Google, Microsoft ou Apple, s’ils sont proposés).',
          'Données de l’entreprise : nom de l’entreprise, abonnement, état de l’essai et de la facturation, membres et leurs rôles, invitations, groupes d’appareils, droits d’accès, règles de connexion, domaines e-mail, liens de déploiement (seulement une empreinte de leur secret) et l’acceptation des conditions (version et date).',
          'Données des appareils : nom de l’appareil, système d’exploitation, ID Remote, clé publique de l’appareil, qui l’a ajouté et quand, son groupe et – pour l’inventaire et l’état, s’ils sont utilisés – des caractéristiques techniques de l’appareil.',
          'Métadonnées de connexion : quel compte ou appareil s’est connecté à quel appareil et quand, début et fin d’une session, type d’accès (accompagné ou sans surveillance), consentement donné, adresses IP et données réseau nécessaires à l’établissement de la connexion, et journaux techniques du service de connexion.',
          'Présence : si un appareil est en ligne, depuis quand et quand il a été vu pour la dernière fois. Le service de connexion le signale toutes les 30 secondes.',
          'Journal : actions dans l’entreprise (appareils ajoutés, renommés, déplacés ou retirés, membres invités ou retirés, réglages modifiés, données exportées) avec la date et la personne qui les a effectuées.',
          'Contenu des sessions : l’écran, le son, les saisies clavier et souris, les fichiers, le presse-papiers et le chat sont transmis chiffrés de bout en bout entre les deux appareils (WebRTC avec des clés liées aux appareils). Les serveurs relais ne transmettent que des données chiffrées ; Mixel ne peut pas les lire et ne les enregistre pas.',
          'Enregistrements : un technicien peut enregistrer une session ; l’enregistrement est sauvegardé uniquement sur son appareil et n’est jamais envoyé à Mixel.',
          'Données de facturation : nom de l’entreprise, adresse de facturation, numéro de TVA, e-mail pour les factures, abonnement choisi, factures et paiements. Les données de carte sont saisies directement chez Stripe ; Mixel ne voit jamais les numéros de carte complets.',
          'Données du site web : lors d’une visite de remote.mixel.ch, des données techniquement nécessaires (adresse IP, heure, page, navigateur) sont traitées par Cloudflare pour fournir et protéger le site. Nous n’utilisons aucun cookie de suivi ou publicitaire ; le navigateur enregistre uniquement votre session de connexion, la langue et le thème de couleur.',
        ] },
      ] },
      { heading: '3. Finalités et bases juridiques', body: [
        { list: [
          'Fourniture du Service, connexion des appareils, application des droits d’accès et des limites d’abonnement – exécution du contrat (art. 6, par. 1, let. b RGPD).',
          'Sécurité : protection contre les abus et les attaques, journal, journaux techniques – intérêt légitime de Mixel et de ses clients à un service sûr (art. 6, par. 1, let. f RGPD).',
          'Facturation et comptabilité, conservation – obligation légale (art. 6, par. 1, let. c RGPD, art. 958f CO) et contrat.',
          'E-mails de service (codes de connexion, invitations, avis concernant le compte) – contrat et intérêt légitime. Nous n’envoyons pas de newsletter sans votre consentement.',
          'Demandes d’assistance – contrat et intérêt légitime.',
        ] },
        'Selon la LPD suisse, nous traitons les données personnelles conformément à ses principes (licéité, bonne foi, proportionnalité, finalité, transparence et sécurité des données).',
      ] },
      { heading: '4. Lieu de stockage', body: [
        'La base de données, le service de connexion à votre compte, le service de connexion entre appareils et les serveurs relais de Mixel Remote fonctionnent sur des serveurs exploités par Mixel chez Hetzner en Allemagne (UE). Les sauvegardes chiffrées sont stockées chez Hetzner en Allemagne. Le portail et la diffusion du site passent par le réseau mondial de Cloudflare. Les paiements sont traités par Stripe. Les e-mails de service sont envoyés via Resend. La liste complète avec lieux et garanties figure sur la page des sous-traitants.',
        'La Suisse reconnaît l’Allemagne et l’UE comme assurant une protection adéquate des données. Lorsque des données sont traitées aux États-Unis (Cloudflare, Stripe, Resend), le transfert repose sur le Swiss-U.S. et l’EU-U.S. Data Privacy Framework lorsque le destinataire est certifié, ainsi que sur les clauses contractuelles types de l’UE avec l’avenant suisse.',
      ] },
      { heading: '5. Destinataires', body: [
        'Au sein d’une entreprise : les propriétaires et administrateurs voient les membres, appareils et le journal de l’entreprise ; les techniciens voient les appareils qu’ils peuvent gérer ; les membres voient les appareils qu’ils ont ajoutés. La personne devant un appareil voit qui se connecte.',
        'À l’extérieur : uniquement les sous-traitants listés sur la page des sous-traitants, dans la mesure nécessaire à leur tâche et liés par contrat ; Stripe traite certaines données (prévention de la fraude, obligations réglementaires) en tant que responsable propre ; Google, Microsoft ou Apple si vous vous connectez avec eux, selon leurs propres politiques ; les autorités lorsque la loi nous y oblige. Nous ne vendons pas de données personnelles.',
      ] },
      { heading: '6. Durée de conservation', body: [
        { list: [
          'Données du compte et de l’entreprise : tant que le compte existe. Lorsque le propriétaire supprime l’entreprise, ou que vous supprimez votre compte, les données sont supprimées immédiatement.',
          'Appareils : jusqu’à leur retrait ; les appareils d’un compte en lecture seule sont retirés automatiquement 30 jours après le passage en lecture seule. La clé et l’ID Remote de l’appareil restent bloqués afin de ne jamais être attribués à un autre ordinateur.',
          'Présence : l’heure de dernière présence est conservée jusqu’au retrait de l’appareil.',
          'Journal : tant que le compte de l’entreprise existe.',
          'Journaux techniques du service de connexion : dans des fichiers de taille limitée, écrasés automatiquement.',
          'Pièces comptables : 10 ans (art. 958f CO) ; après la suppression d’une entreprise, seuls le nom de l’entreprise, les références Stripe et des nombres sont conservés, sans données personnelles des membres.',
          'Sauvegardes : chiffrées ; quotidiennes pendant 7 jours, hebdomadaires pendant 4 semaines, mensuelles pendant 12 mois, puis écrasées.',
        ] },
      ] },
      { heading: '7. Vos droits', body: [
        'Selon la LPD et le RGPD, vous pouvez demander l’accès à vos données, la rectification de données inexactes, l’effacement, la limitation du traitement, la remise ou la portabilité des données, et vous opposer à un traitement fondé sur l’intérêt légitime. Vous pouvez retirer un consentement à tout moment pour l’avenir.',
        'Une grande partie se fait vous-même : vous consultez et corrigez votre profil dans le portail, vous supprimez votre propre compte sous Compte, et le propriétaire exporte et supprime les données de l’entreprise sous Réglages › Données et suppression. Pour le reste, écrivez à info@mixel.ch ; nous répondons dans les 30 jours. Nous pouvons vous demander de confirmer votre identité.',
        'Vous pouvez déposer une plainte auprès du Préposé fédéral à la protection des données et à la transparence (PFPDT, www.edoeb.admin.ch) ou, dans l’UE, auprès de l’autorité de contrôle de votre pays.',
      ] },
      { heading: '8. Sécurité', body: [
        'Toutes les connexions sont chiffrées (TLS ; contenu des sessions de bout en bout avec WebRTC DTLS/SRTP). Les appareils s’identifient avec leur propre clé ; chaque action est vérifiée dans la base de données selon les rôles et règles de l’entreprise ; la connexion en deux étapes et des règles de connexion d’entreprise sont disponibles ; les sauvegardes sont chiffrées ; l’accès du personnel de Mixel est limité au nécessaire et journalisé.',
      ] },
      { heading: '9. Enfants', body: [
        'Mixel Remote s’adresse aux entreprises et aux adultes. Les personnes de moins de 16 ans ne peuvent utiliser l’abonnement Personal qu’avec l’accord d’un parent.',
      ] },
      { heading: '10. Modifications', body: [
        'Nous adaptons cette politique lorsque le Service ou le droit change. La version actuelle est toujours disponible ici ; nous informons les propriétaires des changements importants par e-mail ou dans le portail.',
      ] },
    ],
  },

  dpa: {
    title: 'Accord de sous-traitance (DPA) pour Mixel Remote',
    summary: 'L’accord selon l’art. 9 LPD et l’art. 28 RGPD relatif aux données personnelles que Mixel traite pour le compte du Client en fournissant Mixel Remote.',
    sections: [
      { heading: '1. Parties et champ d’application', body: [
        `Le présent accord de sous-traitance (« Accord ») est conclu entre le Client (responsable du traitement) et ${P} (sous-traitant, « Mixel »). Il fait partie des conditions d’utilisation de Mixel Remote et est accepté avec elles lors de l’ouverture du compte de l’entreprise. Il s’applique à toutes les données personnelles que Mixel traite pour le compte du Client en fournissant le Service (« Données du Client »). En cas de divergence entre cet Accord et les conditions d’utilisation en matière de protection des données, cet Accord prévaut.`,
      ] },
      { heading: '2. Objet, nature et finalité', body: [
        'Objet : fourniture de Mixel Remote – comptes d’entreprise, gestion des appareils, connexions entre appareils, présence, journal, invitations et assistance. Nature : stockage, organisation, transmission et suppression de données ; relais de données de session chiffrées. Finalité : permettre au Client d’assister et d’administrer à distance ses appareils et utilisateurs. Durée : durée du contrat plus les délais de suppression du chiffre 10.',
      ] },
      { heading: '3. Personnes concernées et catégories de données', body: [
        { list: [
          'Personnes concernées : propriétaires, administrateurs, techniciens et membres du Client ; utilisateurs des appareils que le Client gère ou assiste ; personnes invitées par le Client.',
          'Catégories : noms, adresses e-mail, rôles, données de connexion ; noms d’appareils, systèmes d’exploitation, ID Remote, clés d’appareil, caractéristiques techniques ; métadonnées de connexion (qui s’est connecté à quel appareil et quand, adresses IP, consentement) ; présence (en ligne, dernière présence) ; entrées du journal ; invitations.',
          'Le contenu des sessions (écran, son, saisies, fichiers, presse-papiers, chat) est transmis chiffré de bout en bout entre les appareils. Mixel n’y a pas accès et ne l’enregistre pas. Les enregistrements existent uniquement sur l’appareil du technicien.',
          'Les données sensibles ne sont pas traitées de manière ciblée. Si de telles données apparaissent à l’écran pendant une session, Mixel ne les enregistre pas.',
        ] },
      ] },
      { heading: '4. Instructions', body: [
        'Mixel ne traite les Données du Client que sur instruction documentée du Client. Les conditions d’utilisation, cet Accord ainsi que les réglages et actions du Client dans le Service constituent les instructions complètes. Mixel informe le Client sans délai si elle estime qu’une instruction est illicite. Un autre traitement n’a lieu que si le droit suisse ou européen l’exige ; Mixel en informe le Client au préalable, sauf si la loi l’interdit.',
      ] },
      { heading: '5. Confidentialité', body: [
        'Mixel veille à ce que les personnes autorisées à traiter les Données du Client soient tenues à la confidentialité et ne les traitent que dans la mesure nécessaire au Service.',
      ] },
      { heading: '6. Sécurité du traitement', body: [
        'Mixel prend les mesures techniques et organisationnelles appropriées décrites à l’annexe 1 et les adapte à l’état de la technique. Mixel peut remplacer des mesures par d’autres d’un niveau au moins équivalent.',
      ] },
      { heading: '7. Sous-traitants ultérieurs', body: [
        'Le Client autorise Mixel à recourir aux sous-traitants listés sous remote.mixel.ch/legal/subprocessors (annexe 2). Mixel informe le Client au moins 30 jours à l’avance de tout sous-traitant nouveau ou remplacé (par e-mail aux propriétaires ou dans le portail). Le Client peut s’y opposer dans ce délai pour des motifs importants de protection des données ; à défaut de solution, il peut résilier sans frais le Service concerné pour la date du changement.',
        'Mixel impose contractuellement à chaque sous-traitant des obligations de protection des données équivalentes à cet Accord et en reste responsable envers le Client.',
      ] },
      { heading: '8. Transferts à l’étranger', body: [
        'Les Données du Client sont stockées en Allemagne (UE). Un transfert vers un pays sans protection adéquate n’a lieu qu’avec les garanties de l’art. 16 LPD et des art. 44 ss RGPD, notamment les clauses contractuelles types de l’UE avec l’avenant suisse ou une certification au titre du Swiss-U.S. / EU-U.S. Data Privacy Framework. L’annexe 2 indique la garantie par sous-traitant.',
      ] },
      { heading: '9. Assistance au Client', body: [
        { list: [
          'Droits des personnes concernées : le Service permet au Client de consulter, rectifier, exporter (Réglages › Données et suppression) et supprimer lui-même les données. Mixel transmet au Client les demandes qu’elle reçoit et l’aide lorsqu’il ne peut pas y répondre lui-même.',
          'Violations de la sécurité des données : Mixel informe le Client sans retard injustifié, si possible dans les 48 heures après en avoir eu connaissance, d’une violation de la sécurité des Données du Client, avec les informations disponibles, et l’aide dans ses obligations d’annonce au PFPDT, aux autorités de contrôle et aux personnes concernées.',
          'Mixel aide aux analyses d’impact relatives à la protection des données et aux consultations des autorités, dans la mesure où elles concernent le Service.',
        ] },
        'L’assistance allant au-delà des fonctions en libre-service peut être facturée aux tarifs usuels de Mixel si elle est importante ou causée par le Client.',
      ] },
      { heading: '10. Suppression et restitution', body: [
        'Le Client peut exporter ses données à tout moment et supprimer lui-même le compte de l’entreprise. Lors de la suppression, les Données du Client sont supprimées immédiatement des systèmes de production ; les appareils sont déconnectés et retirés. Les sauvegardes chiffrées sont écrasées dans un délai de 12 mois. Seules les pièces comptables (10 ans), qui ne contiennent pas de Données du Client relatives aux membres ou aux utilisateurs d’appareils, sont conservées en vertu d’obligations légales. Si le contrat prend fin sans suppression par le Client, les données d’un compte en lecture seule sont supprimées selon les conditions d’utilisation.',
      ] },
      { heading: '11. Preuves et contrôles', body: [
        'Mixel fournit les informations nécessaires pour démontrer le respect de cet Accord. Le Client peut contrôler ce respect une fois par année civile, avec un préavis de 30 jours, pendant les heures de bureau, à ses frais et sans accès aux données d’autres clients ni aux secrets d’affaires ; les renseignements écrits et rapports existants priment sur les contrôles sur place. Les contrôles par une autorité compétente restent possibles en tout temps.',
      ] },
      { heading: '12. Responsabilité et durée', body: [
        'La responsabilité est régie par les conditions d’utilisation. Cet Accord s’applique aussi longtemps que Mixel traite des Données du Client. Le droit suisse s’applique ; le for est à Zurich.',
      ] },
      { heading: 'Annexe 1 – Mesures techniques et organisationnelles', body: [
        { list: [
          'Chiffrement : TLS 1.2 ou supérieur pour chaque connexion ; contenu des sessions chiffré de bout en bout (WebRTC DTLS/SRTP, clés liées aux identités des appareils) ; sauvegardes chiffrées avant de quitter le serveur.',
          'Contrôle d’accès : chaque action vérifiée dans la base de données (rôle, entreprise, méthode de connexion, règle des deux étapes) ; aucun accès direct à la base pour les applications ; comptes de service séparés avec droits minimaux pour la facturation, la présence et les e-mails.',
          'Identité des appareils : chaque appareil a sa propre clé ; les appareils retirés sont révoqués immédiatement et les sessions en cours se terminent au contrôle suivant ; les ID Remote ne sont jamais réutilisés.',
          'Authentification : mots de passe hachés, codes par e-mail, connexion en deux étapes facultative ou imposable, règles de connexion d’entreprise.',
          'Séparation : les données de chaque entreprise sont séparées par client (tenant) dans la base de données et vérifiées à chaque appel.',
          'Journalisation : journal par entreprise ; journal d’enregistrement des appareils ; journaux techniques de taille limitée.',
          'Disponibilité : sauvegardes chiffrées quotidiennes (7 quotidiennes, 4 hebdomadaires, 12 mensuelles) sur un stockage séparé ; surveillance des services.',
          'Organisation : personnel tenu à la confidentialité, accès selon le principe du moindre privilège, mises à jour de sécurité régulières, processus de gestion des incidents.',
          'Minimisation : aucun stockage du contenu des sessions ; enregistrements uniquement sur l’appareil du technicien ; secrets des invitations et liens de déploiement stockés uniquement sous forme d’empreinte.',
        ] },
      ] },
      { heading: 'Annexe 2 – Sous-traitants ultérieurs', body: [
        'La liste actuelle avec finalité, lieu et garantie :',
        { table: 'subprocessors' },
      ] },
    ],
  },

  subprocessors: {
    title: 'Sous-traitants de Mixel Remote',
    summary: 'Les entreprises qui traitent des données personnelles pour Mixel Remote pour le compte de Mixel, à quelle fin et où.',
    sections: [
      { heading: 'Liste actuelle', body: [
        { table: 'subprocessors' },
        'Mixel exploite elle-même sur ces serveurs la base de données, la connexion aux comptes, les services de connexion, de relais, de réunion et de facturation. Mixel informe ses clients au moins 30 jours avant d’ajouter ou de remplacer un sous-traitant (voir chiffre 7 de l’accord de sous-traitance).',
        'Ne sont pas des sous-traitants : Google, Microsoft et Apple lorsqu’une personne choisit de se connecter avec eux (ils agissent selon leurs propres conditions) ; le propre serveur IA de l’entreprise si elle utilise l’assistant IA ; les propres systèmes de messagerie et de gestion des appareils de l’entreprise.',
      ] },
    ],
  },

  subprocessorRows: [
    { purpose: 'Serveurs pour la base de données, la connexion aux comptes, les services de connexion, de relais, de réunion et de facturation ; sauvegardes chiffrées', data: 'Toutes les données du Service sauf les données de paiement', location: 'Allemagne (UE)', safeguard: 'Protection adéquate (UE)' },
    { purpose: 'Diffusion et protection de remote.mixel.ch et des interfaces (DNS, TLS, protection DDoS, diffusion de contenu, hébergement du portail)', data: 'Adresses IP, données des requêtes ; le trafic chiffré transite', location: 'Réseau mondial, États-Unis', safeguard: 'Data Privacy Framework (EU-U.S. et Swiss-U.S.) ; clauses contractuelles types de l’UE avec avenant suisse' },
    { purpose: 'Paiements, abonnements et factures', data: 'Nom de l’entreprise, adresse de facturation, numéro de TVA, e-mail de facturation, données de paiement, factures', location: 'Irlande (UE), États-Unis', safeguard: 'Protection adéquate (UE) ; Data Privacy Framework ; clauses contractuelles types de l’UE avec avenant suisse' },
    { purpose: 'Envoi des e-mails de service : codes de connexion, invitations, avis concernant le compte', data: 'Adresse e-mail, nom, nom de l’entreprise, contenu de l’e-mail', location: 'États-Unis', safeguard: 'Clauses contractuelles types de l’UE avec avenant suisse' },
  ],
};
