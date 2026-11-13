// Entry point: node src/server.js
import { createServer } from "node:http";
import { createApp } from "./app.js";
import { createAuthorize } from "./auth.js";
import { loadConfig } from "./config.js";
import { createDb } from "./db.js";
import { StripeClient } from "./stripe.js";

const config = loadConfig();
const db = createDb(config.databaseUrl);
const app = createApp({
  db,
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
    server.close(() => void db.close().finally(() => process.exit(0)));
    setTimeout(() => process.exit(0), 5000).unref();
  });
}
