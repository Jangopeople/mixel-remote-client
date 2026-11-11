// AI assistant for the session view. Kept apart from remote.ts: it only listens to the session
// state and reads the picture the technician already sees.
(() => {
  function element<T extends HTMLElement>(id: string): T { const value = document.getElementById(id); if (!value) throw Error(`Missing assistant control ${id}`); return value as T; }
  const invoke = window.__TAURI__.core.invoke;
  const canvas = element<HTMLCanvasElement>('remote-canvas');
  type State = {phase: string; session: string | null; sharing: boolean; peer?: number | null};
  let state: State | null = null;
  // AI assistant (the company's own server): explain the screen, suggest steps, write the note.
  const aiButton = element<HTMLButtonElement>('remote-ai'), aiPanel = element<HTMLDivElement>('ai-panel'), aiQuestion = element<HTMLInputElement>('ai-question');
  const aiAsk = element<HTMLButtonElement>('ai-ask'), aiNote = element<HTMLButtonElement>('ai-note'), aiAnswer = element<HTMLDivElement>('ai-answer'), aiCopy = element<HTMLButtonElement>('ai-copy');
  const aiAddress = element<HTMLInputElement>('ai-address'), aiModel = element<HTMLSelectElement>('ai-model'), aiTest = element<HTMLButtonElement>('ai-test'), aiStatus = element<HTMLParagraphElement>('ai-status');
  const aiKey = 'mixel.remote.ai.v1';
  let ai: {address: string; model: string} | null = null, aiBusy = false, aiOpen = false;
  let aiLog: string[] = [], aiSession: string | null = null, aiStarted = 0, aiPeer: number | null = null;
  function aiLanguage(): string {
    const code = (document.documentElement.lang || 'en').slice(0, 2);
    return ({de: 'German', fr: 'French', it: 'Italian'} as Record<string, string>)[code] ?? 'English';
  }
  async function aiConnect(address: string, model: string, announce: boolean): Promise<void> {
    try {
      const server = await invoke<{address: string; models: string[]; model: string}>('ai_connect', {address, model});
      ai = {address: server.address, model: server.model};
      try { localStorage.setItem(aiKey, JSON.stringify(ai)); } catch { /* not kept */ }
      aiAddress.value = server.address;
      aiModel.replaceChildren();
      for (const name of server.models) { const option = document.createElement('option'); option.value = option.textContent = name; aiModel.appendChild(option); }
      aiModel.value = server.model;
      if (announce) aiStatus.textContent = 'Connected to your AI server.';
    } catch (value) { ai = null; if (announce) aiStatus.textContent = String(value); }
    renderAi();
  }
  aiTest.addEventListener('click', () => { aiStatus.textContent = 'Connecting…'; void aiConnect(aiAddress.value.trim(), aiModel.value, true); });
  aiModel.addEventListener('change', () => { if (ai) void aiConnect(ai.address, aiModel.value, false); });
  { let saved = {address: '', model: ''}; try { saved = {...saved, ...JSON.parse(localStorage.getItem(aiKey) ?? '{}')}; } catch { /* default */ } void aiConnect(saved.address, saved.model, false); }
  function screenshot(): string | null {
    if (canvas.hidden || canvas.width < 16) return null;
    const scale = Math.min(1, 1280 / canvas.width), copy = document.createElement('canvas');
    copy.width = Math.round(canvas.width * scale); copy.height = Math.round(canvas.height * scale);
    const target = copy.getContext('2d'); if (!target) return null;
    target.drawImage(canvas, 0, 0, copy.width, copy.height);
    return copy.toDataURL('image/jpeg', 0.72).split(',')[1] ?? null;
  }
  async function aiRun(kind: 'ask' | 'note'): Promise<void> {
    if (!ai || aiBusy) return;
    aiBusy = true; aiAsk.disabled = aiNote.disabled = true; aiCopy.hidden = true;
    aiAnswer.textContent = kind === 'ask' ? 'Looking at the screen…' : 'Writing the note…';
    const typed = aiQuestion.value.trim();
    try {
      let answer: string;
      if (kind === 'ask') {
        const question = typed || 'Explain briefly what is shown on this screen, name any error or warning you can read, and suggest the next two or three steps for the technician.';
        answer = await invoke<string>('ai_ask', {address: ai.address, model: ai.model, image: screenshot(),
          system: `You are an IT support assistant helping a technician during a remote support session. The image is the remote computer's screen (a Mixel Remote window on it only means the session is running; ignore it). Be concrete and brief. No Markdown. Quote on-screen text exactly when you rely on it; say so if you cannot read something. Answer in ${aiLanguage()}.`,
          prompt: question});
        aiLog.push(`Question: ${typed || '(explain the screen)'}\nAssistant: ${answer}`);
        if (aiLog.length > 12) aiLog.shift();
      } else {
        const minutes = Math.max(1, Math.round((Date.now() - aiStarted) / 60000));
        answer = await invoke<string>('ai_ask', {address: ai.address, model: ai.model, image: screenshot(),
          system: `You write short, factual service-desk notes for an IT provider. Use only the facts given and what the final screenshot shows; never invent work. Format: one summary line, then three short sections for what was done, what is still open, and the time spent, with the section labels in the answer language. Write "–" for an empty section. No Markdown. Answer in ${aiLanguage()}.`,
          prompt: `Remote support session with computer ID ${aiPeer ?? 'unknown'}, about ${minutes} minutes so far.\nTechnician's keywords: ${typed || '(none)'}\nAssistant exchanges during the session:\n${aiLog.join('\n---\n') || '(none)'}\nWrite the ticket note.`});
      }
      // Plain text for display: drop Markdown emphasis and heading marks.
      answer = answer.replace(/\*\*(.+?)\*\*/g, '$1').replace(/^#{1,4}\s*/gm, '').replace(/^\s*\*\s+/gm, '• ');
      aiAnswer.textContent = answer; aiCopy.hidden = false;
    } catch (value) { aiAnswer.textContent = String(value); }
    finally { aiBusy = false; aiAsk.disabled = aiNote.disabled = false; }
  }
  aiButton.addEventListener('click', () => { aiOpen = !aiOpen; renderAi(); if (aiOpen) aiQuestion.focus(); });
  aiAsk.addEventListener('click', () => { void aiRun('ask'); });
  aiNote.addEventListener('click', () => { void aiRun('note'); });
  aiQuestion.addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); void aiRun('ask'); } });
  aiCopy.addEventListener('click', () => { void navigator.clipboard?.writeText(aiAnswer.textContent ?? '').catch(() => {}); });
  function renderAi(): void {
    const viewing = !!state && state.phase === 'active' && !state.sharing && !!state.session;
    if (viewing && state && state.session !== aiSession) { aiSession = state.session; aiStarted = Date.now(); aiPeer = state.peer ?? null; aiLog = []; aiAnswer.textContent = ''; aiCopy.hidden = true; }
    aiButton.hidden = !viewing || !ai;
    aiButton.setAttribute('aria-expanded', String(aiOpen && !aiButton.hidden));
    aiPanel.hidden = !(aiOpen && viewing && ai);
  }
  document.addEventListener('mixel-remote-state', event => { state = (event as CustomEvent<State>).detail; renderAi(); });
  renderAi();
})();
