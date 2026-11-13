// Environment of the billing service (/opt/mixel-remote-billing/.env on mixel-vps, mode 0600).

/** @param {string} name @returns {string} */
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set`);
  return value;
}

export function loadConfig() {
  return {
    port: Number(process.env.PORT ?? 8080),
    host: process.env.HOST ?? "0.0.0.0",
    stripeSecretKey: required("STRIPE_SECRET_KEY"),
    webhookSecret: required("STRIPE_WEBHOOK_SECRET"),
    portalConfiguration: process.env.STRIPE_PORTAL_CONFIGURATION || null,
    databaseUrl: required("DATABASE_URL"),
    postgrestUrl: process.env.POSTGREST_URL ?? "http://ism-postgrest:3000",
    publicBaseUrl: process.env.PUBLIC_BASE_URL ?? "https://remote.mixel.ch",
    corsOrigins: (process.env.CORS_ORIGINS ?? "https://remote.mixel.ch,tauri://localhost,http://tauri.localhost")
      .split(",").map((s) => s.trim()).filter(Boolean),
    invoiceFooter: process.env.INVOICE_FOOTER
      ?? "Preise inkl. 8.1 % MWST. Mixel IT and Corporate Services GmbH, Rüterspüelstrasse 17, 8173 Neerach, CHE-184.355.981 MWST. Fragen: info@mixel.ch",
    rateLimitPerMinute: Number(process.env.RATE_LIMIT_PER_MINUTE ?? 20),
    // Invitation e-mails (SQL 026) through Resend, the Mixel Store's mail path. Without a key the
    // service runs and invitations stay queued.
    resendApiKey: process.env.RESEND_API_KEY || null,
    mailFrom: process.env.MAIL_FROM ?? "Mixel Remote <remote@mixel.ch>",
    inviteBase: process.env.INVITE_BASE_URL ?? "https://remote.mixel.ch/invite#",
  };
}
