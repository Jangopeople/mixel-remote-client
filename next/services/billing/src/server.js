// Entry point: node src/server.js
import { createServer } from "node:http";
import { createApp } from "./app.js";
import { createAuthorize } from "./auth.js";
import { loadConfig } from "./config.js";
import { createDb, listenForInvites } from "./db.js";
import { createInviteWorker, startInviteMail } from "./invite-mail.js";
import { ResendClient } from "./resend.js";
import { StripeClient } from "./stripe.js";

const config = loadConfig();
const db = createDb(config.databaseUrl);
const inviteWorker = config.resendApiKey
  ? createInviteWorker({ db, mailer: new ResendClient({ apiKey: config.resendApiKey }), from: config.mailFrom, inviteBase: config.inviteBase })
  : null;
const stopMail = inviteWorker
  ? startInviteMail({ worker: inviteWorker, listen: (onNotify, onError) => listenForInvites(config.databaseUrl, onNotify, onError) })
  : null;
if (!inviteWorker) console.log(JSON.stringify({ level: "warn", msg: "RESEND_API_KEY not set: invitation e-mails stay queued" }));
const app = createApp({
  db,
  mail: inviteWorker ? inviteWorker.stats : null,
  stripe: new StripeClient({ secretKey: config.stripeSecretKey }),
  authorize: createAuthorize({ postgrestUrl: config.postgrestUrl }),
  config,
});
const server = createServer((req, res) => void app(req, res));
server.requestTimeout = 30000;
server.headersTimeout = 10000;
server.listen(config.port, config.host, () => {
  console.log(JSON.stringify({ level: "info", msg: "listening", port: config.port, live: config.stripeSecretKey.startsWith("sk_live_") }));
});
for (const signal of ["SIGTERM", "SIGINT"]) {
  process.on(signal, () => {
    server.close(() => void Promise.resolve(stopMail?.()).finally(() => void db.close().finally(() => process.exit(0))));
    setTimeout(() => process.exit(0), 5000).unref();
  });
}
