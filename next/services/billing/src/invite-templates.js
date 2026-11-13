// The invitation e-mail of Mixel Remote in English, German, French and Italian (formal address).
// Every value from the database is escaped; the link is the only place the secret appears.

/** @typedef {"en" | "de" | "fr" | "it"} Locale */
/** @typedef {"admin" | "technician" | "member"} Role */

/**
 * @typedef {{
 *   subject: string, heading: string, intro: string, roles: Record<Role, string>, roleText: Record<Role, string>,
 *   button: string, validity: string, fallback: string, ignore: string, footer: string, dateLocale: string,
 * }} Strings
 */

/** @type {Record<Locale, Strings>} */
const STRINGS = {
  en: {
    subject: "{inviter} invited you to {company} on Mixel Remote",
    heading: "You are invited to {company}",
    intro: "{inviter} ({inviterEmail}) invited you to join {company} on Mixel Remote as {role}.",
    roles: { admin: "Admin", technician: "Technician", member: "Member" },
    roleText: {
      admin: "Admins manage the team, devices, groups and settings.",
      technician: "Technicians connect to and manage the devices they have access to.",
      member: "Members use Mixel Remote for their own devices.",
    },
    button: "Accept invitation",
    validity: "The invitation is valid until {date} and works only with this e-mail address ({email}). Sign in or create an account with it.",
    fallback: "If the button does not work, copy this link into your browser:",
    ignore: "If you did not expect this invitation, you can ignore this e-mail.",
    footer: "Mixel Remote · Mixel IT and Corporate Services GmbH · 8173 Neerach, Switzerland",
    dateLocale: "en-GB",
  },
  de: {
    subject: "{inviter} hat Sie zu {company} in Mixel Remote eingeladen",
    heading: "Einladung zu {company}",
    intro: "{inviter} ({inviterEmail}) hat Sie eingeladen, {company} in Mixel Remote als {role} beizutreten.",
    roles: { admin: "Admin", technician: "Techniker", member: "Mitglied" },
    roleText: {
      admin: "Admins verwalten Team, Geräte, Gruppen und Einstellungen.",
      technician: "Techniker verbinden sich mit den Geräten, auf die sie Zugriff haben, und verwalten sie.",
      member: "Mitglieder nutzen Mixel Remote für ihre eigenen Geräte.",
    },
    button: "Einladung annehmen",
    validity: "Die Einladung ist bis {date} gültig und funktioniert nur mit dieser E-Mail-Adresse ({email}). Melden Sie sich damit an oder erstellen Sie ein Konto.",
    fallback: "Falls die Schaltfläche nicht funktioniert, kopieren Sie diesen Link in Ihren Browser:",
    ignore: "Falls Sie diese Einladung nicht erwartet haben, können Sie diese E-Mail ignorieren.",
    footer: "Mixel Remote · Mixel IT and Corporate Services GmbH · 8173 Neerach, Schweiz",
    dateLocale: "de-CH",
  },
  fr: {
    subject: "{inviter} vous invite à rejoindre {company} sur Mixel Remote",
    heading: "Invitation à rejoindre {company}",
    intro: "{inviter} ({inviterEmail}) vous invite à rejoindre {company} sur Mixel Remote en tant que {role}.",
    roles: { admin: "admin", technician: "technicien", member: "membre" },
    roleText: {
      admin: "Les admins gèrent l’équipe, les appareils, les groupes et les paramètres.",
      technician: "Les techniciens se connectent aux appareils auxquels ils ont accès et les gèrent.",
      member: "Les membres utilisent Mixel Remote pour leurs propres appareils.",
    },
    button: "Accepter l’invitation",
    validity: "L’invitation est valable jusqu’au {date} et ne fonctionne qu’avec cette adresse e-mail ({email}). Connectez-vous ou créez un compte avec celle-ci.",
    fallback: "Si le bouton ne fonctionne pas, copiez ce lien dans votre navigateur :",
    ignore: "Si vous n’attendiez pas cette invitation, vous pouvez ignorer cet e-mail.",
    footer: "Mixel Remote · Mixel IT and Corporate Services GmbH · 8173 Neerach, Suisse",
    dateLocale: "fr-CH",
  },
  it: {
    subject: "{inviter} La invita a unirsi a {company} su Mixel Remote",
    heading: "Invito a unirsi a {company}",
    intro: "{inviter} ({inviterEmail}) La invita a unirsi a {company} su Mixel Remote come {role}.",
    roles: { admin: "admin", technician: "tecnico", member: "membro" },
    roleText: {
      admin: "Gli admin gestiscono il team, i dispositivi, i gruppi e le impostazioni.",
      technician: "I tecnici si collegano ai dispositivi a cui hanno accesso e li gestiscono.",
      member: "I membri usano Mixel Remote per i propri dispositivi.",
    },
    button: "Accetta l’invito",
    validity: "L’invito è valido fino al {date} e funziona solo con questo indirizzo e-mail ({email}). Acceda o crei un account con questo indirizzo.",
    fallback: "Se il pulsante non funziona, copi questo link nel Suo browser:",
    ignore: "Se non si aspettava questo invito, può ignorare questa e-mail.",
    footer: "Mixel Remote · Mixel IT and Corporate Services GmbH · 8173 Neerach, Svizzera",
    dateLocale: "it-CH",
  },
};

export const LOCALES = /** @type {Locale[]} */ (Object.keys(STRINGS));

/** @param {string} value */
export function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
}

/** Single line, no control characters, bounded length (names come from user input). @param {string} value @param {number} max */
function clean(value, max) {
  const flat = String(value ?? "").replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ").replace(/\s+/g, " ").trim();
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** @param {string} template @param {Record<string, string>} values */
function fill(template, values) {
  return template.replace(/\{(\w+)\}/g, (_, key) => values[key] ?? "");
}

/**
 * @param {{ locale: string, company: string, inviterName: string, inviterEmail: string, email: string,
 *   role: string, link: string, expiresAt: Date }} input
 * @returns {{ subject: string, html: string, text: string }}
 */
export function renderInvite(input) {
  const locale = /** @type {Locale} */ (LOCALES.includes(/** @type {Locale} */ (input.locale)) ? input.locale : "en");
  const s = STRINGS[locale];
  const role = /** @type {Role} */ (["admin", "technician", "member"].includes(input.role) ? input.role : "member");
  if (!/^https:\/\/[^\s"'<>]+#mri_[A-Za-z0-9_-]{20,96}$/.test(input.link)) throw new Error("invalid invitation link");
  const plain = {
    company: clean(input.company, 80),
    inviter: clean(input.inviterName, 80),
    inviterEmail: clean(input.inviterEmail, 254),
    email: clean(input.email, 254),
    role: s.roles[role],
    date: new Intl.DateTimeFormat(s.dateLocale, { dateStyle: "long", timeZone: "Europe/Zurich" }).format(input.expiresAt),
  };
  /** @type {Record<string, string>} */
  const html = Object.fromEntries(Object.entries(plain).map(([key, value]) => [key, escapeHtml(value)]));
  html.company = `<strong>${html.company}</strong>`;
  html.role = `<strong>${html.role}</strong>`;
  const subject = fill(s.subject, plain);
  const link = escapeHtml(input.link);
  const text = [
    fill(s.heading, plain),
    "",
    fill(s.intro, plain),
    s.roleText[role],
    "",
    `${s.button}: ${input.link}`,
    "",
    fill(s.validity, plain),
    s.ignore,
    "",
    "--",
    s.footer,
  ].join("\n");
  const body = `<!doctype html>
<html lang="${locale}">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${escapeHtml(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1d2433;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f4f5f7;padding:32px 12px;">
<tr><td align="center">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;border-radius:12px;border:1px solid #e3e6eb;">
<tr><td style="padding:28px 32px 8px 32px;font-size:15px;font-weight:600;letter-spacing:.2px;color:#2b5cd6;">Mixel Remote</td></tr>
<tr><td style="padding:8px 32px 0 32px;"><h1 style="margin:0;font-size:22px;line-height:1.3;color:#1d2433;">${fill(s.heading, html)}</h1></td></tr>
<tr><td style="padding:16px 32px 0 32px;font-size:15px;line-height:1.55;">
<p style="margin:0 0 12px 0;">${fill(s.intro, html)}</p>
<p style="margin:0;color:#4a5263;">${escapeHtml(s.roleText[role])}</p>
</td></tr>
<tr><td style="padding:24px 32px;">
<a href="${link}" style="display:inline-block;background:#2b5cd6;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;padding:12px 22px;border-radius:8px;">${escapeHtml(s.button)}</a>
</td></tr>
<tr><td style="padding:0 32px 8px 32px;font-size:13px;line-height:1.55;color:#4a5263;">
<p style="margin:0 0 12px 0;">${fill(s.validity, html)}</p>
<p style="margin:0 0 4px 0;">${escapeHtml(s.fallback)}</p>
<p style="margin:0 0 12px 0;word-break:break-all;"><a href="${link}" style="color:#2b5cd6;">${link}</a></p>
<p style="margin:0;">${escapeHtml(s.ignore)}</p>
</td></tr>
<tr><td style="padding:20px 32px 28px 32px;font-size:12px;color:#8a91a0;border-top:1px solid #eef0f3;">${escapeHtml(s.footer)}</td></tr>
</table>
</td></tr>
</table>
</body>
</html>`;
  return { subject, html: body, text };
}
