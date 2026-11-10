(() => {
  const invoke = window.__TAURI__.core.invoke;
  const connect = document.querySelector<HTMLButtonElement>('#connect')!;
  const keyboard = document.querySelector<HTMLButtonElement>('#connect-keyboard')!;
  const sendText = document.querySelector<HTMLButtonElement>('#send-text')!;
  const testKeys = document.querySelector<HTMLButtonElement>('#test-keys')!;
  const remoteText = document.querySelector<HTMLInputElement>('#remote-text')!;
  const inputStatus = document.querySelector<HTMLParagraphElement>('#input-status')!;
  let activeRequest = 0, sending = false, inputMessageRequest = 0;
  const disconnect = document.querySelector<HTMLButtonElement>('#disconnect')!;
  const status = document.querySelector<HTMLParagraphElement>('#viewer-status')!;
  const metrics = document.querySelector<HTMLParagraphElement>('#viewer-metrics')!;
  const canvas = document.querySelector<HTMLCanvasElement>('#remote-video')!;
  const context = canvas.getContext('2d')!;
  let decoder: VideoDecoder | undefined, generation = 0, runningRequest = 0, supported = false;
  let localFailure = '';
  const config: VideoDecoderConfig = { codec: 'avc1.42E01F', optimizeForLatency: true, hardwareAcceleration: 'prefer-hardware' };
  function clear(): void {
    generation++;
    if (decoder && decoder.state !== 'closed') decoder.close();
    decoder = undefined;
    context.clearRect(0, 0, canvas.width, canvas.height);
    metrics.textContent = 'No retained remote video';
  }
  async function fail(error: unknown): Promise<void> {
    const request = activeRequest;
    localFailure = String(error); clear(); status.className = 'error'; status.textContent = String(error);
    inputStatus.textContent = 'No active keyboard session';
    if (request) await invoke('close_local_session', { request }).catch(() => {});
  }
  async function receive(request: number): Promise<void> {
    runningRequest = request;
    clear(); const current = generation;
    let sequence = 0, rendered = 0;
    decoder = new VideoDecoder({
      output(frame) {
        try {
          if (current !== generation) return;
          if (frame.displayWidth !== 640 || frame.displayHeight !== 420) throw Error('Unexpected decoded frame dimensions');
          canvas.width = frame.displayWidth; canvas.height = frame.displayHeight;
          context.drawImage(frame, 0, 0);
          const decodedSequence = Math.round(frame.timestamp / 33333);
          const pendingFrames = sequence - decodedSequence;
          metrics.textContent = `Received and decoded frames: ${++rendered} · RTP frame ${sequence} · decoded frame ${decodedSequence} · decoder pending ${pendingFrames} · ${frame.displayWidth} × ${frame.displayHeight}`;
        } catch (error) { if (current === generation) void fail(error); } finally { frame.close(); }
      },
      error(error) { if (current === generation) void fail(error); },
    });
    decoder.configure(config);
    try {
      while (current === generation) {
        const packet = await invoke<ArrayBuffer | number[] | Uint8Array>('next_local_video', { request });
        if (current !== generation) break;
        const bytes = packet instanceof ArrayBuffer ? new Uint8Array(packet) : new Uint8Array(packet);
        if (!bytes.length) { await new Promise(resolve => setTimeout(resolve, 20)); continue; }
        if (bytes.length <= 17) throw Error('Truncated authenticated video frame');
        const header = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
        const next = Number(header.getBigUint64(0, true));
        const timestamp = Number(header.getBigUint64(9, true));
        if (!Number.isSafeInteger(next) || next !== sequence + 1 || !Number.isSafeInteger(timestamp) || bytes[8] > 1) throw Error('Invalid video sequence');
        if (!decoder || decoder.state !== 'configured' || decoder.decodeQueueSize > 4) throw Error('Viewer decoder cannot keep up; stopping without dropping predictive frames');
        if (sequence === 0 && bytes[8] !== 1) throw Error('First received frame is not a keyframe');
        sequence = next;
        decoder.decode(new EncodedVideoChunk({ type: bytes[8] ? 'key' : 'delta', timestamp, data: bytes.subarray(17) }));
      }
    } catch (error) { if (current === generation) await fail(error); }
  }
  async function requestConnection(control: boolean): Promise<void> {
    connect.disabled = true; localFailure = ''; status.className = ''; clear(); runningRequest = 0;
    sending = false; activeRequest = 0;
    inputStatus.textContent = control ? 'Waiting for host keyboard consent' : 'View-only request; keyboard control is disabled';
    const current = generation;
    try {
      const state = await invoke<{request: number}>('request_local_session', { control });
      if (current === generation) activeRequest = state.request;
    }
    catch (error) { if (current === generation) await fail(error); }
  }
  connect.addEventListener('click', () => { void requestConnection(false); });
  keyboard.addEventListener('click', () => { void requestConnection(true); });
  disconnect.addEventListener('click', async () => {
    const request = activeRequest;
    clear(); runningRequest = 0;
    inputStatus.textContent = 'No active keyboard session';
    try { if (request) await invoke('close_local_session', { request }); }
    catch (error) { status.textContent = String(error); }
  });
  type Event = {kind: 'text'; text: string} | {kind: 'key'; usage: number; down: boolean};
  async function sendEvents(events: Event[]): Promise<void> {
    if (sending) return;
    sending = true;
    const request = activeRequest;
    const current = generation;
    sendText.disabled = testKeys.disabled = true;
    try {
      for (const event of events) {
        if (current !== generation || request !== activeRequest) return;
        await invoke('send_local_input', { request, event });
      }
      if (current === generation && request === activeRequest) {
        inputMessageRequest = request;
        inputStatus.textContent = `Host authenticated and submitted ${events.length} native input event(s)`;
      }
    } catch (error) {
      if (current === generation && request === activeRequest) {
        inputMessageRequest = request;
        inputStatus.textContent = `Input failed: ${error}`;
        await invoke('close_local_session', { request }).catch(() => {});
      }
    } finally { if (current === generation) sending = false; }
  }
  sendText.addEventListener('click', () => { void sendEvents([{kind:'text', text:remoteText.value}]); });
  testKeys.addEventListener('click', () => {
    const key = (usage: number, down: boolean): Event => ({kind:'key',usage,down});
    void sendEvents([key(0xe1,true),key(4,true),key(4,false),key(0xe1,false),key(5,true),key(5,false),key(0x2a,true),key(0x2a,false),key(6,true),key(6,false)]);
  });
  async function refresh(): Promise<void> {
    const current = generation;
    try {
      const state = await invoke<{request: number; phase: string; message: string; received: number; control: boolean; injected: number; input_access: boolean}>('local_session_status');
      if (current !== generation) { setTimeout(() => { void refresh(); }, 200); return; }
      status.textContent = localFailure ? `Viewer error · ${localFailure} · ${state.phase}` : `${state.phase} · ${state.message}`;
      activeRequest = state.request;
      if (state.phase !== 'active') inputStatus.textContent = 'No active keyboard session';
      else if (!state.control) inputStatus.textContent = 'View-only session; keyboard control is disabled';
      else if (!state.input_access) inputStatus.textContent = 'macOS Accessibility event-post permission is required for keyboard control; view-only remains available.';
      else if (inputMessageRequest !== state.request) inputStatus.textContent = 'Keyboard control is active for this fixture session';
      sendText.disabled = testKeys.disabled = sending || state.phase !== 'active' || !state.control || !state.input_access;
      keyboard.disabled = connect.disabled = !supported || ['pending', 'negotiating', 'active'].includes(state.phase);
      disconnect.disabled = !['pending', 'negotiating', 'active'].includes(state.phase);
      if (state.phase === 'active' && runningRequest !== state.request) void receive(state.request).catch(error => fail(error));
      else if (!['active', 'negotiating'].includes(state.phase) && decoder) clear();
    } catch (error) { if (current === generation) await fail(error); }
    setTimeout(() => { void refresh(); }, 200);
  }
  void (async () => {
    try {
      if (typeof VideoDecoder === 'undefined' || !(await VideoDecoder.isConfigSupported(config)).supported) throw Error('H264 WebCodecs decoder unavailable in this webview');
      supported = true; await refresh();
    } catch (error) { connect.disabled = true; status.className = 'error'; status.textContent = String(error); }
  })();
})();
