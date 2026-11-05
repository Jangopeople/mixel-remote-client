(() => {
  const invoke = window.__TAURI__.core.invoke;
  const connect = document.querySelector<HTMLButtonElement>('#connect')!;
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
    localFailure = String(error); clear(); status.className = 'error'; status.textContent = String(error);
    await invoke('close_local_session').catch(() => {});
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
  connect.addEventListener('click', async () => {
    connect.disabled = true; localFailure = ''; status.className = ''; clear(); runningRequest = 0;
    try { await invoke('request_local_session'); }
    catch (error) { await fail(error); }
  });
  disconnect.addEventListener('click', async () => {
    clear(); runningRequest = 0;
    try { await invoke('close_local_session'); }
    catch (error) { status.textContent = String(error); }
  });
  async function refresh(): Promise<void> {
    try {
      const state = await invoke<{request: number; phase: string; message: string; received: number}>('local_session_status');
      status.textContent = localFailure ? `Viewer error · ${localFailure} · ${state.phase}` : `${state.phase} · ${state.message}`;
      connect.disabled = !supported || ['pending', 'negotiating', 'active'].includes(state.phase);
      disconnect.disabled = !['pending', 'negotiating', 'active'].includes(state.phase);
      if (state.phase === 'active' && runningRequest !== state.request) void receive(state.request).catch(error => fail(error));
      else if (!['active', 'negotiating'].includes(state.phase) && decoder) clear();
    } catch (error) { await fail(error); }
    setTimeout(() => { void refresh(); }, 200);
  }
  void (async () => {
    try {
      if (typeof VideoDecoder === 'undefined' || !(await VideoDecoder.isConfigSupported(config)).supported) throw Error('H264 WebCodecs decoder unavailable in this webview');
      supported = true; await refresh();
    } catch (error) { connect.disabled = true; status.className = 'error'; status.textContent = String(error); }
  })();
})();
