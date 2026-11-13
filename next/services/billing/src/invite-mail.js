// Invitation e-mails (SQL 026). The database queues an invitation without a secret; this worker
// claims it, creates the secret, stores only its SHA-256, sends the e-mail through Resend and records
// the result. The secret lives only in memory for the duration of one send and in the e-mail link;
// it is never logged, stored or returned.
//
// Wake-ups: LISTEN remote_saas_invite_mail (NOTIFY from the invite/resend RPCs, no payload) plus a
// sweep every 30 s, so a missed notification or a retry with back-off is picked up anyway.
import { createHash, randomBytes } from "node:crypto";
import { renderInvite } from "./invite-templates.js";
import { MailError } from "./resend.js";

/**
 * @typedef {{ invite_id: string, claim: string, email: string, role: string, locale: string, expires_at: string | Date,
 *   company: string, inviter_name: string, inviter_email: string | null, attempt: number }} ClaimedInvite
 * @typedef {{
 *   claimInvites(max: number): Promise<ClaimedInvite[]>,
 *   inviteSecret(invite: string, claim: string, hash: Buffer): Promise<boolean>,
 *   inviteResult(invite: string, claim: string, messageId: string | null, failure: string | null, giveUp: boolean): Promise<string>,
 * }} MailDb
 * @typedef {{ send(mail: { from: string, to: string, subject: string, html: string, text: string, replyTo?: string | null,
 *   idempotencyKey: string, tags?: { name: string, value: string }[] }): Promise<string> }} Mailer
 */

/** A new invitation secret: "mri_" + 32 random bytes, base64url (47 characters). */
export function newSecret() {
  return `mri_${randomBytes(32).toString("base64url")}`;
}

/** @param {string} secret */
export function secretHash(secret) {
  return createHash("sha256").update(secret, "utf8").digest();
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/** Reserved test domains (RFC 2606/6761): QA invitations there are never mailed (bounces hurt the sender). @param {string} email */
export function isTestAddress(email) {
  const domain = email.split("@").pop()?.toLowerCase() ?? "";
  return /(^|\.)(test|example|invalid|localhost)$/.test(domain) || /^example\.(com|net|org)$/.test(domain);
}

/**
 * @param {{ db: MailDb, mailer: Mailer, from: string, inviteBase: string,
 *   log?: (entry: Record<string, unknown>) => void }} deps
 */
export function createInviteWorker({ db, mailer, from, inviteBase, log = (e) => console.log(JSON.stringify(e)) }) {
  let running = false;
  let again = false;
  const stats = { sent: 0, failed: 0, lastRun: /** @type {string | null} */ (null), lastError: /** @type {string | null} */ (null) };

  /** @param {ClaimedInvite} item */
  async function sendOne(item) {
    if (isTestAddress(item.email)) {
      await db.inviteResult(item.invite_id, item.claim, null, "test address, not mailed", true);
      log({ level: "info", msg: "invite mail", invite: item.invite_id, outcome: "failed", error: "test address, not mailed" });
      return;
    }
    const secret = newSecret();
    const stored = await db.inviteSecret(item.invite_id, item.claim, secretHash(secret));
    if (!stored) {
      log({ level: "info", msg: "invite mail skipped", invite: item.invite_id, reason: "revoked or re-claimed" });
      return;
    }
    let messageId = null;
    /** @type {string | null} */
    let failure = null;
    let giveUp = false;
    try {
      const mail = renderInvite({
        locale: item.locale,
        company: item.company,
        inviterName: item.inviter_name,
        inviterEmail: item.inviter_email ?? "",
        email: item.email,
        role: item.role,
        link: `${inviteBase}${secret}`,
        expiresAt: new Date(item.expires_at),
      });
      messageId = await mailer.send({
        from,
        to: item.email,
        subject: mail.subject,
        html: mail.html,
        text: mail.text,
        replyTo: item.inviter_email && EMAIL.test(item.inviter_email) ? item.inviter_email : null,
        // One claim = one e-mail: a retry of the same request after a timeout is not sent twice.
        idempotencyKey: `mixel-remote-invite-${item.claim}`,
        tags: [{ name: "app", value: "mixel-remote" }, { name: "kind", value: "invite" }],
      });
    } catch (error) {
      failure = error instanceof MailError ? error.message : "could not prepare the e-mail";
      giveUp = error instanceof MailError ? !error.retry : true;
    }
    const outcome = await db.inviteResult(item.invite_id, item.claim, messageId, failure, giveUp);
    if (outcome === "sent") stats.sent += 1;
    else stats.failed += 1;
    log({ level: failure ? "warn" : "info", msg: "invite mail", invite: item.invite_id, outcome, attempt: item.attempt,
      locale: item.locale, message: messageId, ...(failure ? { error: failure } : {}) });
  }

  /** Sends everything that is due; concurrent calls are coalesced. */
  async function runOnce() {
    if (running) {
      again = true;
      return;
    }
    running = true;
    try {
      do {
        again = false;
        for (let round = 0; round < 20; round += 1) {
          const batch = await db.claimInvites(10);
          for (const item of batch) await sendOne(item);
          if (batch.length < 10) break;
        }
      } while (again);
      stats.lastError = null;
    } catch (error) {
      stats.lastError = error instanceof Error ? error.message.slice(0, 200) : "error";
      log({ level: "error", msg: "invite mail run", error: stats.lastError });
    } finally {
      stats.lastRun = new Date().toISOString();
      running = false;
    }
  }

  return { runOnce, stats };
}

/**
 * Wires the worker to LISTEN/NOTIFY and a periodic sweep. `listen` connects a dedicated client and
 * calls `onNotify` per notification; it returns a close function. Reconnects with back-off.
 * @param {{ worker: { runOnce(): Promise<void> }, listen: (onNotify: () => void, onError: (error: Error) => void) => Promise<() => Promise<void>>,
 *   sweepMs?: number, log?: (entry: Record<string, unknown>) => void }} deps
 */
export function startInviteMail({ worker, listen, sweepMs = 30000, log = (e) => console.log(JSON.stringify(e)) }) {
  let stopped = false;
  /** @type {(() => Promise<void>) | null} */
  let close = null;
  let backoff = 1000;
  const kick = () => void worker.runOnce();
  async function connect() {
    if (stopped) return;
    try {
      close = await listen(kick, (error) => {
        log({ level: "warn", msg: "invite mail listener lost", error: error.message.slice(0, 200) });
        const previous = close;
        close = null;
        void previous?.().catch(() => {});
        setTimeout(() => void connect(), backoff).unref();
        backoff = Math.min(backoff * 2, 60000);
      });
      backoff = 1000;
      kick();
    } catch (error) {
      log({ level: "warn", msg: "invite mail listener", error: error instanceof Error ? error.message.slice(0, 200) : "error" });
      setTimeout(() => void connect(), backoff).unref();
      backoff = Math.min(backoff * 2, 60000);
    }
  }
  void connect();
  const timer = setInterval(kick, sweepMs);
  timer.unref();
  return async () => {
    stopped = true;
    clearInterval(timer);
    await close?.().catch(() => {});
  };
}
