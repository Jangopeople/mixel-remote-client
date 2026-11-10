(() => {
  type Message = {id: number; mine: boolean; text: string};
  const panel = document.getElementById('remote-chat')!;
  const empty = document.getElementById('remote-chat-empty')!;
  const log = document.getElementById('remote-chat-log')!;
  const form = document.getElementById('remote-chat-form') as HTMLFormElement;
  const text = document.getElementById('remote-chat-text') as HTMLInputElement;
  const send = document.getElementById('remote-chat-send') as HTMLButtonElement;
  const error = document.getElementById('remote-chat-error')!;
  const invoke = window.__TAURI__.core.invoke;
  let session = '', available = false, polling = false, sending = false;
  let generation = 0, rendered: Message[] = [];

  function setAvailable(value: boolean): void {
    if (available === value) return;
    available = value;
    panel.hidden = !value;
    send.disabled = !value || sending;
    document.dispatchEvent(new CustomEvent('mixel-chat-availability', {detail:{available:value}}));
  }

  function show(messages: Message[]): void {
    let overlap = Math.min(messages.length, rendered.length);
    while (overlap > 0 && !rendered.slice(-overlap).every((old, index) =>
      old.id === messages[index].id)) overlap--;
    for (let index = 0; index < rendered.length - overlap; index++) log.firstElementChild?.remove();
    for (const message of messages.slice(overlap)) {
      const row = document.createElement('li');
      const speaker = document.createElement('strong');
      speaker.textContent = message.mine ? 'You' : 'Session partner';
      const body = document.createElement('p');
      body.textContent = message.text;
      row.append(speaker, body);
      log.appendChild(row);
    }
    log.hidden = messages.length === 0;
    empty.hidden = messages.length > 0;
    if (overlap < messages.length) log.scrollTop = log.scrollHeight;
    const unread = messages.slice(overlap).filter(message => !message.mine).length;
    if (unread) document.dispatchEvent(new CustomEvent('mixel-chat-unread', {detail:{count:unread}}));
    rendered = messages;
  }

  async function refresh(): Promise<void> {
    if (!session || polling) return;
    const current = generation;
    polling = true;
    try {
      const messages = await invoke<Message[] | null>('remote_chat_messages', {session});
      if (current !== generation) return;
      show(messages ?? []);
      setAvailable(messages !== null);
    } catch {
      if (current === generation) { show([]); setAvailable(false); }
    } finally {
      if (current === generation) polling = false;
    }
  }

  document.addEventListener('mixel-remote-state', ((event: CustomEvent<{phase:string;session?:string|null}>) => {
    const next = event.detail.phase === 'active' ? event.detail.session ?? '' : '';
    if (next === session) return;
    session = next;
    generation++;
    polling = sending = false;
    text.value = error.textContent = '';
    show([]);
    setAvailable(false);
    void refresh();
  }) as EventListener);

  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!session || !available || sending) return;
    const message = text.value.trim();
    if (!message) return;
    if (new TextEncoder().encode(message).length > 2048) {
      error.textContent = 'This message exceeds 2,048 bytes.';
      return;
    }
    const current = generation;
    sending = send.disabled = true;
    error.textContent = '';
    try {
      await invoke('remote_chat_send', {session, text:message});
      if (current === generation && text.value.trim() === message) text.value = '';
    } catch (problem) {
      if (current === generation) error.textContent = String(problem);
    } finally {
      if (current === generation) {
        sending = false;
        send.disabled = !available;
        void refresh();
      }
    }
  });
  window.setInterval(() => { void refresh(); }, 500);
})();
