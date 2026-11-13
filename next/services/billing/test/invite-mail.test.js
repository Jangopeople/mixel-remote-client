import assert from "node:assert/strict";
import { test } from "node:test";
import { createInviteWorker, isTestAddress, newSecret, secretHash, startInviteMail } from "../src/invite-mail.js";
import { LOCALES, escapeHtml, renderInvite } from "../src/invite-templates.js";
import { MailError, ResendClient } from "../src/resend.js";

const LINK = `https://remote.mixel.ch/invite#${"mri_" + "A".repeat(43)}`;
const BASE = {
  company: "Muster <AG>", inviterName: "Anna \"Admin\" Muster", inviterEmail: "anna@muster.ch",
  email: "tech@muster.ch", role: "technician", link: LINK, expiresAt: new Date("2026-11-20T10:00:00Z"),
};

test("the invitation e-mail exists in four languages with company, inviter, role, link and date", () => {
  const expected = {
    en: ["invited you to", "Technician", "Accept invitation", "20 November 2026"],
    de: ["hat Sie zu", "Techniker", "Einladung annehmen", "20. November 2026"],
    fr: ["vous invite", "technicien", "Accepter l’invitation", "20 novembre 2026"],
    it: ["La invita", "tecnico", "Accetta l’invito", "20 novembre 2026"],
  };
  for (const locale of LOCALES) {
    const mail = renderInvite({ ...BASE, locale });
    const [phrase, role, button, date] = expected[locale];
    assert.ok(mail.subject.includes(phrase) && mail.subject.includes("Muster <AG>"), `${locale} subject`);
    for (const part of [role, button, date, LINK]) {
      assert.ok(mail.text.includes(part), `${locale} text has ${part}`);
      assert.ok(mail.html.includes(escapeHtml(part)), `${locale} html has ${part}`);
    }
    assert.ok(mail.html.includes(`lang="${locale}"`));
    assert.ok(mail.html.includes("Muster &lt;AG&gt;") && !mail.html.includes("Muster <AG>"), `${locale} company escaped`);
    assert.ok(mail.html.includes("Anna &quot;Admin&quot; Muster"), `${locale} inviter escaped`);
    assert.ok(mail.text.includes("tech@muster.ch"), `${locale} invited address named`);
  }
  assert.ok(renderInvite({ ...BASE, locale: "xx" }).subject.includes("invited you to"), "unknown language: English");
  assert.ok(renderInvite({ ...BASE, locale: "de", role: "admin" }).text.includes("Admins verwalten"));
});

test("names cannot inject lines or markup, and only a real invitation link is accepted", () => {
  const mail = renderInvite({ ...BASE, locale: "en", company: "Evil\r\nBcc: x@y.z <script>", inviterName: "x".repeat(300) });
  assert.ok(!mail.subject.includes("\n") && !mail.subject.includes("\r"));
  assert.ok(!mail.html.includes("<script>"));
  assert.ok(mail.subject.length < 200);
  assert.throws(() => renderInvite({ ...BASE, locale: "en", link: "javascript:alert(1)#mri_" + "a".repeat(40) }));
  assert.throws(() => renderInvite({ ...BASE, locale: "en", link: "https://remote.mixel.ch/invite#mri_\"><img" }));
});

test("secrets are 47 characters of base64url and hashed with SHA-256", () => {
  const a = newSecret();
  assert.match(a, /^mri_[A-Za-z0-9_-]{43}$/);
  assert.notEqual(a, newSecret());
  assert.equal(secretHash("abc").toString("hex"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  assert.equal(secretHash(a).length, 32);
});

/** A fake database that behaves like SQL 026 for one or more queued invitations.
 * @param {import("../src/invite-mail.js").ClaimedInvite[]} items */
function fakeDb(items) {
  const calls = { secret: /** @type {any[]} */ ([]), result: /** @type {any[]} */ ([]) };
  let queue = [...items];
  return {
    calls,
    db: {
      claimInvites: async (/** @type {number} */ max) => { const batch = queue.slice(0, max); queue = queue.slice(max); return batch; },
      inviteSecret: async (/** @type {string} */ invite, /** @type {string} */ claim, /** @type {Buffer} */ hash) => {
        calls.secret.push({ invite, claim, hash });
        return !invite.startsWith("revoked");
      },
      inviteResult: async (/** @type {string} */ invite, /** @type {string} */ claim, /** @type {string | null} */ messageId,
        /** @type {string | null} */ failure, /** @type {boolean} */ giveUp) => {
        calls.result.push({ invite, claim, messageId, failure, giveUp });
        return failure ? (giveUp ? "failed" : "pending") : "sent";
      },
    },
  };
}
/** @param {string} id @param {Record<string, unknown>} overrides @returns {import("../src/invite-mail.js").ClaimedInvite} */
const item = (id, overrides = {}) => ({
  invite_id: id, claim: `claim-${id}`, email: "tech@muster.ch", role: "technician", locale: "de",
  expires_at: "2026-11-20T10:00:00Z", company: "Muster AG", inviter_name: "Anna Muster", inviter_email: "anna@muster.ch",
  attempt: 1, ...overrides,
});

test("the worker sends the link with the secret whose hash it stored, and logs no secret", async () => {
  const { db, calls } = fakeDb([item("i1"), item("revoked-2")]);
  /** @type {any[]} */ const sent = [];
  /** @type {any[]} */ const logs = [];
  const worker = createInviteWorker({
    db, from: "Mixel Remote <remote@mixel.ch>", inviteBase: "https://remote.mixel.ch/invite#",
    mailer: { send: async (mail) => { sent.push(mail); return "re_123"; } },
    log: (entry) => logs.push(entry),
  });
  await worker.runOnce();
  assert.equal(sent.length, 1, "nothing is sent for a revoked invitation");
  const mail = sent[0];
  const secret = /#(mri_[A-Za-z0-9_-]{43})/.exec(mail.text)?.[1];
  assert.ok(secret, "link in the text");
  assert.ok(mail.html.includes(secret));
  assert.deepEqual(calls.secret[0].hash, secretHash(secret), "the stored hash belongs to the mailed secret");
  assert.equal(mail.to, "tech@muster.ch");
  assert.equal(mail.replyTo, "anna@muster.ch");
  assert.equal(mail.idempotencyKey, "mixel-remote-invite-claim-i1");
  assert.ok(mail.subject.startsWith("Anna Muster hat Sie zu Muster AG"));
  assert.deepEqual(calls.result, [{ invite: "i1", claim: "claim-i1", messageId: "re_123", failure: null, giveUp: false }]);
  const everything = JSON.stringify({ logs, calls: calls.result });
  assert.ok(!everything.includes(secret), "the secret is not in logs or results");
  assert.equal(worker.stats.sent, 1);
});

test("temporary send errors are retried later, refused addresses give up", async () => {
  const { db, calls } = fakeDb([item("a"), item("b"), item("c")]);
  const errors = [new MailError("resend 503 internal_server_error", 503, true), new MailError("resend 422 validation_error", 422, false)];
  const worker = createInviteWorker({
    db, from: "x <x@mixel.ch>", inviteBase: "https://remote.mixel.ch/invite#",
    mailer: { send: async () => { const e = errors.shift(); if (e) throw e; return "re_ok"; } },
    log: () => {},
  });
  await worker.runOnce();
  assert.deepEqual(calls.result.map((r) => [r.invite, r.failure, r.giveUp]), [
    ["a", "resend 503 internal_server_error", false],
    ["b", "resend 422 validation_error", true],
    ["c", null, false],
  ]);
  assert.equal(worker.stats.failed, 2);
});

test("a database error ends the run without crashing and is reported in the stats", async () => {
  const worker = createInviteWorker({
    db: { claimInvites: async () => { throw new Error("connection refused"); }, inviteSecret: async () => true, inviteResult: async () => "sent" },
    mailer: { send: async () => "x" }, from: "x <x@mixel.ch>", inviteBase: "https://remote.mixel.ch/invite#", log: () => {},
  });
  await worker.runOnce();
  assert.equal(worker.stats.lastError, "connection refused");
});

test("a notification and the sweep both start a run; a lost listener reconnects", async () => {
  let runs = 0;
  let listens = 0;
  /** @type {{ notify: () => void, lose: (e: Error) => void }} */
  const hooks = { notify: () => {}, lose: () => {} };
  const stop = startInviteMail({
    worker: { runOnce: async () => { runs += 1; } },
    listen: async (onNotify, onError) => { listens += 1; hooks.notify = onNotify; hooks.lose = onError; return async () => {}; },
    sweepMs: 20,
    log: () => {},
  });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(listens, 1);
  const before = runs;
  hooks.notify();
  assert.equal(runs, before + 1);
  await new Promise((r) => setTimeout(r, 50));
  assert.ok(runs >= before + 2, "sweep ran");
  hooks.lose(new Error("gone"));
  await new Promise((r) => setTimeout(r, 1100));
  assert.equal(listens, 2, "reconnected");
  await stop();
});

test("the Resend client sends one request with the idempotency key and maps errors", async () => {
  /** @type {any[]} */ const requests = [];
  const ok = new ResendClient({ apiKey: "re_test", fetch: async (url, init) => {
    requests.push({ url, init });
    return new Response(JSON.stringify({ id: "4ef9a417-02e9-4d39-ad75-9611e0fcc33c" }), { status: 200 });
  } });
  const id = await ok.send({ from: "a <a@mixel.ch>", to: "b@example.test", subject: "s", html: "<p>h</p>", text: "t",
    replyTo: "c@example.test", idempotencyKey: "k1", tags: [{ name: "app", value: "mixel-remote" }] });
  assert.equal(id, "4ef9a417-02e9-4d39-ad75-9611e0fcc33c");
  assert.equal(requests[0].url, "https://api.resend.com/emails");
  const headers = requests[0].init.headers;
  assert.equal(headers.authorization, "Bearer re_test");
  assert.equal(headers["idempotency-key"], "k1");
  const body = JSON.parse(requests[0].init.body);
  assert.deepEqual(body.to, ["b@example.test"]);
  assert.equal(body.reply_to, "c@example.test");
  const refused = new ResendClient({ apiKey: "k", fetch: async () => new Response(JSON.stringify({ name: "validation_error" }), { status: 422 }) });
  await assert.rejects(refused.send({ from: "a", to: "b", subject: "s", html: "h", text: "t", idempotencyKey: "k" }),
    (e) => e instanceof MailError && !e.retry && e.message === "resend 422 validation_error");
  const busy = new ResendClient({ apiKey: "k", fetch: async () => new Response("{}", { status: 429 }) });
  await assert.rejects(busy.send({ from: "a", to: "b", subject: "s", html: "h", text: "t", idempotencyKey: "k" }), (e) => e instanceof MailError && e.retry);
  const down = new ResendClient({ apiKey: "k", fetch: async () => { throw new TypeError("fetch failed"); } });
  await assert.rejects(down.send({ from: "a", to: "b", subject: "s", html: "h", text: "t", idempotencyKey: "k" }), (e) => e instanceof MailError && e.retry);
});

test("reserved test domains are never mailed; Resend's test inbox is", async () => {
  for (const address of ["a@example.test", "b@qa.example", "c@example.com", "d@x.invalid", "e@localhost"]) assert.ok(isTestAddress(address), address);
  for (const address of ["delivered@resend.dev", "x@mixel.ch", "y@example.ch", "z@testing.com"]) assert.ok(!isTestAddress(address), address);
  const { db, calls } = fakeDb([item("t", { email: "qa-owner@example.test" })]);
  let sends = 0;
  const worker = createInviteWorker({ db, mailer: { send: async () => { sends += 1; return "x"; } }, from: "x <x@mixel.ch>",
    inviteBase: "https://remote.mixel.ch/invite#", log: () => {} });
  await worker.runOnce();
  assert.equal(sends, 0);
  assert.equal(calls.secret.length, 0, "no secret for a test address");
  assert.deepEqual(calls.result.map((r) => [r.failure, r.giveUp]), [["test address, not mailed", true]]);
});
