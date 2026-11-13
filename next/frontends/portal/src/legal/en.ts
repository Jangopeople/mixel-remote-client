import type { LegalTexts } from './types';

const P = 'Mixel IT and Corporate Services GmbH, Rüterspüelstrasse 17, 8173 Neerach, Switzerland (UID CHE-184.355.981)';

export const en: LegalTexts = {
  ui: {
    centre: 'Legal',
    centreLead: 'Terms, privacy and data processing for Mixel Remote.',
    draftTitle: 'Draft — not in force',
    draftText: 'These texts are drafts under review and not yet binding. The final version will be published here with its date.',
    version: 'Version',
    updated: 'Last updated',
    contents: 'Contents',
    otherLanguages: 'Also available in',
    columns: { name: 'Sub-processor', purpose: 'Purpose', data: 'Data', location: 'Where', safeguard: 'Safeguard for transfers' },
    print: 'Print or save as PDF',
  },

  terms: {
    title: 'Terms of Service for Mixel Remote',
    summary: 'The contract between your company and Mixel for the use of Mixel Remote: plans, trial, billing, cancellation, deletion, acceptable use, availability and liability.',
    sections: [
      { heading: '1. Provider and scope', body: [
        `Mixel Remote is provided by ${P} ("Mixel", "we"). These Terms apply to the Mixel Remote service: the portal at remote.mixel.ch, the Mixel Remote apps for computers and phones, the browser viewer, meetings and the connection service (together the "Service").`,
        'The customer is the company or person that creates a Mixel Remote account (the "Customer", "you"). Whoever accepts these Terms for a company confirms that they may do so on its behalf. The Data Processing Agreement and the Privacy Policy are part of these Terms. Different or additional terms of the Customer do not apply unless Mixel agrees to them in writing.',
      ] },
      { heading: '2. Account, owners and members', body: [
        'The person who signs up becomes the owner of the company account. Owners and administrators can invite people as administrators, technicians or members and decide which sign-in methods are allowed and whether two-step sign-in is required. The Customer is responsible for everything done with its account and for its members complying with these Terms.',
        'Keep sign-in details confidential, use two-step sign-in where possible and tell us without delay at info@mixel.ch if you suspect misuse. Each sign-in is personal; one login may not be shared by several people.',
      ] },
      { heading: '3. Plans and prices', body: [
        'The plans and their limits are shown on remote.mixel.ch/pricing at the time of ordering:',
        { list: [
          'Personal: free, for private, non-commercial use only; 1 technician, 10 devices, 1 session at a time.',
          'Solo: CHF 12 per month; 1 technician, 100 devices, 1 session at a time.',
          'Team: CHF 39 per month; 5 technicians, 500 devices, 3 sessions at a time; device groups and access rights.',
          'Business: CHF 89 per month; 15 technicians, 2,000 devices, 10 sessions at a time; company single sign-on, audit log, policies, required two-step sign-in.',
          'Enterprise: by individual offer.',
          'Additional technician: CHF 8 per month.',
        ] },
        'Prices are in Swiss francs and include Swiss VAT (8.1 %). They apply to yearly billing; with monthly billing they are 20 % higher. Mixel may change prices and plan contents for future billing periods; we will tell owners at least 30 days in advance by e-mail, and the Customer may cancel before the change takes effect.',
      ] },
      { heading: '4. Free trial', body: [
        'A new company receives a free 14-day trial of the Business plan. No payment details are needed. A trial is offered once per company.',
        'If no paid plan is chosen by the end of the trial, the account becomes read-only: nobody can connect, and devices, members and settings can only be viewed, removed or exported. Devices are kept for 30 days after the account became read-only and are then removed automatically. Choosing a paid plan within these 30 days restores full use.',
      ] },
      { heading: '5. Ordering, billing and payment', body: [
        'Paid plans are ordered in the portal and paid through our payment provider Stripe by card. A plan bought during the trial starts at once; the first payment is due when the trial would have ended. Subscriptions renew automatically for the same period (month or year) until cancelled.',
        'Upgrades and additional technicians apply immediately and are charged pro rata at once. Downgrades must fit the current number of technicians and devices. Invoices are available in the billing area of the portal.',
        'If a payment fails, the account stays usable for a short grace period while Stripe retries the payment. If the payment remains outstanding, the account becomes read-only as described in section 4 until the amount is paid.',
      ] },
      { heading: '6. Term and cancellation', body: [
        'The contract runs for an indefinite period. Owners and administrators can cancel a paid plan at any time in the billing area; the cancellation takes effect at the end of the current billing period and the account then becomes read-only. Fees already paid are not refunded, unless the law requires otherwise.',
        'Mixel may terminate the contract with 30 days\' notice to the end of a billing period, and without notice for good cause, in particular a serious or repeated breach of section 8, non-payment, or a legal or official order.',
      ] },
      { heading: '7. Deleting the account and your data', body: [
        'The owner can delete the whole company account at any time in the portal (Settings › Data and deletion) or in the app. The deletion takes effect immediately: the subscription ends at once without a refund for the remaining period, every device is signed out and removed, open invitations are revoked, and the company data, members and their Mixel Remote sign-in accounts are deleted. Each member can delete their own account in the same way; the last owner first deletes the company or appoints another owner.',
        'Before deleting, the owner can download the company\'s data (company, members, devices, groups and audit log) as JSON or CSV. This export is also available while the account is read-only.',
        'Mixel keeps only what the law requires: billing records (company name, Stripe references, invoices and payments) for 10 years (art. 958f Swiss Code of Obligations). Encrypted backups are overwritten within 12 months; deleted data is not restored from backups except to recover from a disaster, in which case it is deleted again.',
      ] },
      { heading: '8. Acceptable use and consent of the person being helped', body: [
        'Mixel Remote gives access to other people\'s computers and phones. The Customer may only connect to devices that it owns or administers, or whose user has agreed. In particular:',
        { list: [
          'Attended sessions: the person at the device must agree to each session (Mixel Remote asks them) and can end it at any time.',
          'Unattended access, sign-in screen access and always-on access may only be set up on devices that the Customer is entitled to administer, with the knowledge of their regular users, and in line with the Customer\'s internal rules and employment and data protection law.',
          'Recordings, file transfers, screenshots and the AI assistant may only be used where this is lawful; where a session is recorded, the people involved must be informed in advance.',
          'The Service may not be used for unauthorised access, surveillance or monitoring of people without a legal basis, fraud (for example fake support calls), spreading malware, infringing third-party rights, sending spam, or any other unlawful purpose.',
          'Do not attack, overload, reverse engineer or work around the security or the limits of the Service, and do not resell it without a written agreement with Mixel.',
        ] },
        'Mixel may suspend access in whole or in part where there are concrete indications of misuse, and will inform the Customer unless the law or the protection of others forbids it. The Customer indemnifies Mixel against third-party claims arising from its unlawful use of the Service.',
      ] },
      { heading: '9. Availability and support', body: [
        'Mixel operates the Service with professional care and aims for high availability, but does not guarantee uninterrupted operation. Maintenance is done outside Swiss business hours where possible. Connections depend on the networks and devices at both ends, which are outside Mixel\'s control. Paid plans include support by e-mail and phone during Swiss business hours. Service levels apply only if agreed in writing (Enterprise).',
        'Mixel develops the Service further and may change or replace functions, provided the essential functions of the booked plan remain available. Apps must be kept up to date; older versions may stop working for security reasons.',
      ] },
      { heading: '10. Customer data and confidentiality', body: [
        'The Customer keeps all rights in its data. Mixel uses customer data only to provide the Service, as set out in the Data Processing Agreement. Screen content, keyboard and mouse input, files, clipboard and chat of a session are sent encrypted between the two devices; Mixel does not store them. Recordings are saved only on the technician\'s device.',
        'Both parties keep non-public information of the other party confidential. This obligation continues after the end of the contract.',
      ] },
      { heading: '11. Data protection', body: [
        'Mixel processes personal data in accordance with the Swiss Federal Act on Data Protection (FADP) and, where applicable, the EU General Data Protection Regulation (GDPR). For the data that the Customer processes with the Service, the Customer is the controller and Mixel its processor; the Data Processing Agreement applies. For its own purposes (account, billing, security) Mixel is controller; see the Privacy Policy.',
      ] },
      { heading: '12. Rights to the Service', body: [
        'All rights in the Service, the apps and their content remain with Mixel or its licensors. For the term of the contract the Customer receives a non-exclusive, non-transferable right to use the Service within its plan. Open-source components are subject to their own licences.',
      ] },
      { heading: '13. Warranty and liability', body: [
        'Mixel remedies reported defects within a reasonable time. To the extent permitted by law, Mixel is liable only for damage caused intentionally or by gross negligence; liability for slight negligence, for indirect and consequential damage, lost profit, data loss and business interruption is excluded. In any case liability is limited to the fees paid by the Customer for the Service in the 12 months before the damage occurred. Liability for personal injury and mandatory statutory liability remain unaffected. The free Personal plan and the trial are provided as they are.',
        'The Customer is responsible for backing up the data on its devices. Mixel is not liable for what technicians do on connected devices.',
      ] },
      { heading: '14. Changes to these Terms', body: [
        'Mixel may change these Terms. Owners will be informed by e-mail or in the portal at least 30 days before a change takes effect; the change is accepted if the Customer continues to use the Service after that date without cancelling. Changes in the Customer\'s favour or required by law may take effect earlier.',
      ] },
      { heading: '15. Applicable law and place of jurisdiction', body: [
        'Swiss law applies, excluding its conflict-of-law rules and the UN Convention on Contracts for the International Sale of Goods. The place of jurisdiction is Zurich, Switzerland. Mandatory consumer protection rules of the country where a consumer lives remain unaffected. If a provision is invalid, the rest of these Terms remains in force.',
      ] },
    ],
  },

  privacy: {
    title: 'Privacy Policy for Mixel Remote',
    summary: 'Which personal data Mixel processes when you use Mixel Remote, why, where it is stored, how long it is kept, and your rights.',
    sections: [
      { heading: '1. Who is responsible', body: [
        `Controller for the processing described here: ${P}. Contact for data protection: info@mixel.ch, +41 44 433 14 00, or by post to the address above.`,
        'This policy covers the Mixel Remote portal (remote.mixel.ch), the Mixel Remote apps, the browser viewer, meetings and the connection service. When a company uses Mixel Remote to support its own users or devices, that company decides about this data and is the controller; Mixel processes it on the company\'s behalf under the Data Processing Agreement. Questions about such data are best addressed to that company first.',
      ] },
      { heading: '2. Which data we process', body: [
        { list: [
          'Account data: name, e-mail address, sign-in method, hashed password (if you use one), two-step sign-in settings, language, and the sign-in provider you chose (Mixel ID, Google, Microsoft or Apple, if offered).',
          'Company data: company name, plan, trial and billing state, members and their roles, invitations, device groups, access rights, sign-in rules, e-mail domains, deploy links (only a hash of their secret) and the acceptance of the Terms (version and time).',
          'Device data: device name, operating system, Remote ID, the device\'s public key, the person who added it, when it was added, its group, and — for inventory and health, if used — technical properties of the device.',
          'Connection metadata: which account or device connected to which device, when a session started and ended, the type of access (attended or unattended), consent given, IP addresses and network data needed to set up the connection, and technical logs of the connection service.',
          'Presence: whether a device is online, since when, and when it was last seen. The connection service reports this every 30 seconds.',
          'Session audit: actions in the company (devices added, renamed, moved or removed, members invited or removed, settings changed, data exported) with time and the person who did them.',
          'Session content: screen, sound, keyboard and mouse input, files, clipboard and chat are sent end-to-end encrypted between the two devices (WebRTC with keys bound to the devices). Relay servers forward encrypted data only; Mixel cannot read it and does not store it.',
          'Recordings: a technician can record a session; the recording is saved only on the technician\'s device and is never uploaded to Mixel.',
          'Billing data: company name, billing address, VAT number, e-mail for invoices, the chosen plan, invoices and payments. Card details are entered directly at Stripe; Mixel never sees full card numbers.',
          'Website data: when you visit remote.mixel.ch, technically necessary data (IP address, time, page, browser) is processed by Cloudflare to deliver and protect the site. We use no tracking or advertising cookies; the browser stores only your sign-in session, language and colour theme.',
        ] },
      ] },
      { heading: '3. Purposes and legal bases', body: [
        { list: [
          'Providing the Service, connecting devices, enforcing access rights and plan limits — performance of the contract (art. 6(1)(b) GDPR).',
          'Security: protection against misuse and attacks, audit log, technical logs — our and our customers\' legitimate interest in a secure service (art. 6(1)(f) GDPR).',
          'Billing and accounting, keeping records — legal obligation (art. 6(1)(c) GDPR, art. 958f Swiss Code of Obligations) and contract.',
          'Service e-mails (sign-in codes, invitations, notices about the account) — contract and legitimate interest. We do not send newsletters without your consent.',
          'Support requests — contract and legitimate interest.',
        ] },
        'Under the Swiss FADP, we process personal data in line with its principles (lawfulness, good faith, proportionality, purpose limitation, transparency and data security).',
      ] },
      { heading: '4. Where your data is stored', body: [
        'The Mixel Remote database, sign-in service, connection service and relay servers run on servers operated by Mixel at Hetzner in Germany (EU). Encrypted backups are stored at Hetzner in Germany. The portal and the delivery of the website are provided by Cloudflare\'s worldwide network. Payments are processed by Stripe. Service e-mails are sent through Resend. The full list with locations and safeguards is on the sub-processor page.',
        'Switzerland recognises Germany and the EU as providing adequate data protection. Where data is processed in the USA (Cloudflare, Stripe, Resend), the transfer is based on the Swiss-U.S. and EU-U.S. Data Privacy Framework where the recipient is certified, and on the EU Standard Contractual Clauses with the Swiss addendum.',
      ] },
      { heading: '5. Who receives data', body: [
        'Inside a company: owners and administrators see the company\'s members, devices and audit log; technicians see the devices they are allowed to manage; members see the devices they added. The person at a device sees who is connecting.',
        'Outside: only the sub-processors listed on the sub-processor page, as far as needed for their task and bound by contract; Stripe processes some data (fraud prevention, regulatory duties) as its own controller; Google, Microsoft or Apple, if you sign in with them, under their own privacy policies; authorities where we are legally obliged. We do not sell personal data.',
      ] },
      { heading: '6. How long we keep data', body: [
        { list: [
          'Account and company data: as long as the account exists. When the owner deletes the company, or you delete your account, the data is deleted immediately.',
          'Devices: until they are removed; devices of a read-only account are removed automatically 30 days after it became read-only. The device\'s key and Remote ID stay blocked so that they are never given to another computer.',
          'Presence: the last-seen time is kept until the device is removed.',
          'Audit log: as long as the company account exists.',
          'Technical logs of the connection service: kept in size-limited files that are overwritten automatically.',
          'Billing records: 10 years (art. 958f Swiss Code of Obligations); after deleting a company only the company name, Stripe references and counts are kept, without personal data of the members.',
          'Backups: encrypted; daily backups for 7 days, weekly for 4 weeks, monthly for 12 months, then overwritten.',
        ] },
      ] },
      { heading: '7. Your rights', body: [
        'Under the FADP and the GDPR you can request access to your data, correction of incorrect data, deletion, restriction of processing, data portability, and object to processing based on legitimate interest. You can withdraw a consent at any time with effect for the future.',
        'Most of this is self-service: you can see and correct your profile in the portal, delete your own account under Account, and the owner can export and delete the company\'s data under Settings › Data and deletion. For anything else write to info@mixel.ch; we answer within 30 days. We may ask you to confirm your identity.',
        'You can complain to the Swiss Federal Data Protection and Information Commissioner (FDPIC, www.edoeb.admin.ch) or, in the EU, to the supervisory authority of your country.',
      ] },
      { heading: '8. Security', body: [
        'All connections are encrypted (TLS; session content end-to-end with WebRTC DTLS/SRTP). Devices prove their identity with their own key; every action is checked in the database against the company\'s roles and rules; two-step sign-in and company sign-in rules are available; backups are encrypted; access by Mixel staff is limited to what is needed and logged.',
      ] },
      { heading: '9. Children', body: [
        'Mixel Remote is intended for companies and adults. People under 16 may use the Personal plan only with the consent of a parent.',
      ] },
      { heading: '10. Changes', body: [
        'We update this policy when the Service or the law changes. The current version is always available here; we inform owners of important changes by e-mail or in the portal.',
      ] },
    ],
  },

  dpa: {
    title: 'Data Processing Agreement (DPA) for Mixel Remote',
    summary: 'The agreement under art. 9 FADP and art. 28 GDPR for the personal data that Mixel processes on behalf of the Customer when providing Mixel Remote.',
    sections: [
      { heading: '1. Parties and scope', body: [
        `This Data Processing Agreement ("DPA") is concluded between the Customer (controller) and ${P} (processor, "Mixel"). It is part of the Terms of Service for Mixel Remote and is accepted together with them when the company account is created. It applies to all personal data that Mixel processes on behalf of the Customer when providing the Service ("Customer Personal Data"). If this DPA and the Terms differ on data protection, this DPA prevails.`,
      ] },
      { heading: '2. Subject, nature and purpose', body: [
        'Subject: providing Mixel Remote — company accounts, device management, connections between devices, presence, audit log, invitations and support. Nature: storage, organisation, transmission and deletion of data; relaying of encrypted session data. Purpose: enabling the Customer to support and administer its devices and users remotely. Duration: the term of the contract plus the deletion periods in section 10.',
      ] },
      { heading: '3. Data subjects and categories of data', body: [
        { list: [
          'Data subjects: the Customer\'s owners, administrators, technicians and members; users of the devices the Customer manages or supports; people the Customer invites.',
          'Categories: names, e-mail addresses, roles, sign-in data; device names, operating systems, Remote IDs, device keys, technical device properties; connection metadata (who connected to which device and when, IP addresses, consent); presence (online, last seen); audit log entries; invitations.',
          'Session content (screen, sound, input, files, clipboard, chat) is transmitted end-to-end encrypted between the devices. Mixel cannot access it and does not store it. Recordings exist only on the technician\'s device.',
          'Special categories of personal data are not processed intentionally. Should such data appear on a screen during a session, it is not stored by Mixel.',
        ] },
      ] },
      { heading: '4. Instructions', body: [
        'Mixel processes Customer Personal Data only on documented instructions from the Customer. The Terms, this DPA and the Customer\'s settings and actions in the Service are the complete instructions. Mixel informs the Customer without delay if it considers an instruction unlawful. Mixel processes data otherwise only where required by Swiss or EU law, and tells the Customer before doing so unless the law prohibits it.',
      ] },
      { heading: '5. Confidentiality', body: [
        'Mixel ensures that persons authorised to process Customer Personal Data are bound to confidentiality and process it only as needed for the Service.',
      ] },
      { heading: '6. Security of processing', body: [
        'Mixel takes appropriate technical and organisational measures, described in Annex 1, and adapts them to the state of the art. Mixel may replace measures by others of at least the same level.',
      ] },
      { heading: '7. Sub-processors', body: [
        'The Customer authorises Mixel to engage the sub-processors listed on remote.mixel.ch/legal/subprocessors (Annex 2). Mixel informs the Customer at least 30 days in advance of a new or replaced sub-processor (by e-mail to the owners or in the portal). The Customer may object for important data protection reasons within that time; if no solution is found, the Customer may terminate the affected Service at the date of the change without penalty.',
        'Mixel imposes on each sub-processor by contract data protection obligations equivalent to this DPA and remains responsible to the Customer for them.',
      ] },
      { heading: '8. Transfers abroad', body: [
        'Customer Personal Data is stored in Germany (EU). A transfer to a country without adequate data protection takes place only with the safeguards of art. 16 FADP and art. 44 et seq. GDPR, in particular the EU Standard Contractual Clauses with the Swiss addendum or a certification under the Swiss-U.S. / EU-U.S. Data Privacy Framework. Annex 2 names the safeguard per sub-processor.',
      ] },
      { heading: '9. Assistance to the Customer', body: [
        { list: [
          'Data subject rights: the Service lets the Customer view, correct, export (Settings › Data and deletion) and delete data itself. Mixel forwards requests that reach it to the Customer and helps where the Customer cannot answer them itself.',
          'Data breaches: Mixel informs the Customer without undue delay, where possible within 48 hours, after becoming aware of a breach of the security of Customer Personal Data, with the information available, and supports the Customer in its duties to notify the FDPIC, supervisory authorities and data subjects.',
          'Mixel helps with data protection impact assessments and consultations of authorities as far as they concern the Service.',
        ] },
        'Assistance beyond the self-service functions may be charged at Mixel\'s usual rates if it is extensive or caused by the Customer.',
      ] },
      { heading: '10. Deletion and return', body: [
        'The Customer can export its data at any time and delete the company account itself. On deletion, Customer Personal Data is deleted from the live systems immediately; devices are signed out and removed. Encrypted backups are overwritten within 12 months. Retained under statutory duties are only billing records (10 years), which contain no Customer Personal Data of members or device users. At the end of the contract without deletion by the Customer, the data of a read-only account is deleted as described in the Terms.',
      ] },
      { heading: '11. Evidence and audits', body: [
        'Mixel provides the information needed to demonstrate compliance with this DPA. The Customer may audit compliance once per calendar year, with 30 days\' notice, during business hours, at its own cost and without access to other customers\' data or to trade secrets; written information and existing reports take precedence over on-site inspections. Audits by a competent authority remain possible at any time.',
      ] },
      { heading: '12. Liability and term', body: [
        'Liability is governed by the Terms of Service. This DPA applies as long as Mixel processes Customer Personal Data. Swiss law applies; the place of jurisdiction is Zurich.',
      ] },
      { heading: 'Annex 1 — Technical and organisational measures', body: [
        { list: [
          'Encryption: TLS 1.2 or higher for every connection; session content end-to-end encrypted (WebRTC DTLS/SRTP, keys bound to device identities); backups encrypted before they leave the server.',
          'Access control: every action checked in the database (role, company, sign-in method, two-step rule); no direct database access for apps; separate service accounts with minimal rights for billing, presence and e-mail.',
          'Device identity: each device has its own key; removed devices are revoked at once and running sessions end at the next check; Remote IDs are never reused.',
          'Authentication: hashed passwords, e-mail codes, optional and enforceable two-step sign-in, company sign-in rules.',
          'Separation: each company\'s data is separated by tenant in the database and checked on every call.',
          'Logging: audit log per company; enrolment audit for devices; technical logs of limited size.',
          'Availability: daily encrypted backups (7 daily, 4 weekly, 12 monthly) on separate storage; monitoring of the services.',
          'Organisation: staff bound to confidentiality, least-privilege access, security updates applied regularly, incident process.',
          'Data minimisation: no storage of session content; recordings only on the technician\'s device; invitation and deploy-link secrets stored only as hashes.',
        ] },
      ] },
      { heading: 'Annex 2 — Sub-processors', body: [
        'The current list with purpose, location and safeguard:',
        { table: 'subprocessors' },
      ] },
    ],
  },

  subprocessors: {
    title: 'Sub-processors of Mixel Remote',
    summary: 'The companies that process personal data for Mixel Remote on behalf of Mixel, what for, and where.',
    sections: [
      { heading: 'Current list', body: [
        { table: 'subprocessors' },
        'Mixel operates the database, sign-in, connection, relay, meetings and billing services itself on these servers. Mixel informs customers at least 30 days before adding or replacing a sub-processor (see section 7 of the Data Processing Agreement).',
        'Not sub-processors: Google, Microsoft and Apple when a person chooses to sign in with them (they act under their own terms); the company\'s own AI server, if it uses the AI assistant; the company\'s own mail and device management systems.',
      ] },
    ],
  },

  subprocessorRows: [
    { purpose: 'Servers for the database, sign-in, connection, relay, meetings and billing services; encrypted backups', data: 'All data of the Service except payment details', location: 'Germany (EU)', safeguard: 'Adequate protection (EU)' },
    { purpose: 'Delivery and protection of remote.mixel.ch and the APIs (DNS, TLS, DDoS protection, content delivery, hosting of the portal)', data: 'IP addresses, request data; encrypted traffic passes through', location: 'Worldwide network, USA', safeguard: 'Data Privacy Framework (EU-U.S. and Swiss-U.S.); EU Standard Contractual Clauses with Swiss addendum' },
    { purpose: 'Payments, subscriptions and invoices', data: 'Company name, billing address, VAT number, billing e-mail, payment details, invoices', location: 'Ireland (EU), USA', safeguard: 'Adequate protection (EU); Data Privacy Framework; EU Standard Contractual Clauses with Swiss addendum' },
    { purpose: 'Sending service e-mails: sign-in codes, invitations, account notices', data: 'E-mail address, name, company name, content of the e-mail', location: 'USA', safeguard: 'EU Standard Contractual Clauses with Swiss addendum' },
  ],
};
