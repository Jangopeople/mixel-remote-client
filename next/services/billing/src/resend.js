// Minimal Resend client (https://resend.com/docs/api-reference/emails/send-email), the mail path the
// Mixel Store already uses. Only the send call; no SDK.

export class MailError extends Error {
  /** @param {string} message @param {number} status @param {boolean} retry */
  constructor(message, status, retry) {
    super(message);
    this.status = status;
    this.retry = retry;
  }
}

export class ResendClient {
  /** @param {{ apiKey: string, fetch?: typeof fetch, baseUrl?: string }} options */
  constructor({ apiKey, fetch: fetcher = fetch, baseUrl = "https://api.resend.com" }) {
    this.apiKey = apiKey;
    this.fetch = fetcher;
    this.baseUrl = baseUrl;
  }

  /**
   * @param {{ from: string, to: string, subject: string, html: string, text: string, replyTo?: string | null,
   *   idempotencyKey: string, tags?: { name: string, value: string }[] }} mail
   * @returns {Promise<string>} the Resend message id
   */
  async send(mail) {
    /** @type {Record<string, unknown>} */
    const body = { from: mail.from, to: [mail.to], subject: mail.subject, html: mail.html, text: mail.text };
    if (mail.replyTo) body.reply_to = mail.replyTo;
    if (mail.tags) body.tags = mail.tags;
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}/emails`, {
        method: "POST",
        headers: {
          authorization: `Bearer ${this.apiKey}`,
          "content-type": "application/json",
          "idempotency-key": mail.idempotencyKey,
          "user-agent": "mixel-remote-mail/1",
        },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new MailError("mail service unreachable", 0, true);
    }
    const answer = /** @type {Record<string, unknown>} */ (await response.json().catch(() => ({})));
    if (!response.ok) {
      const name = typeof answer.name === "string" ? answer.name.slice(0, 60) : "error";
      // 429 and 5xx are temporary; a 4xx (invalid address, unverified sender) is not.
      throw new MailError(`resend ${response.status} ${name}`, response.status, response.status === 429 || response.status >= 500);
    }
    if (typeof answer.id !== "string" || !answer.id) throw new MailError("resend answered without an id", response.status, true);
    return answer.id;
  }
}
