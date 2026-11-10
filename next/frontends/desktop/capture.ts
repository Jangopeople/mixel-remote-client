(() => {
  function element<T extends HTMLElement>(id: string): T {
    const node = document.getElementById(id);
    if (!node) throw Error(`Missing UI element ${id}`);
    return node as T;
  }
  const invoke = window.__TAURI__.core.invoke;
  const start = element<HTMLButtonElement>('start');
  const stop = element<HTMLButtonElement>('stop');
  const status = element<HTMLParagraphElement>('status');
  const metrics = element<HTMLParagraphElement>('metrics');
  const checks = element<HTMLParagraphElement>('checks');
  const canvas = element<HTMLCanvasElement>('preview');
  const context = canvas.getContext('2d');
  if (!context) throw Error('Canvas unavailable');
  let generation = 0;
  const target = element<HTMLSelectElement>('capture-target');
  const confirmDisplay = element<HTMLInputElement>('confirm-full-display');
  const disclosure = element<HTMLParagraphElement>('capture-disclosure');
  let targets: {id: string; name: string; full_display: boolean}[] = [];
  function updateCaptureChoice(): void {
    const selected = targets.find(item => item.id === target.value);
    confirmDisplay.parentElement!.hidden = !selected?.full_display;
    disclosure.textContent = selected?.full_display ? 'The entire selected display, including other windows, will be captured after you confirm. Local preview does not start a remote customer connection.' : selected ? "Capture is limited to this app's fixture window; no customer connection is started." : 'No native capture source is available in this build.';
    start.disabled = !selected || (!!selected.full_display && !confirmDisplay.checked) || !stop.disabled;
  }
  target.addEventListener('change', () => { confirmDisplay.checked = false; updateCaptureChoice(); });
  confirmDisplay.addEventListener('change', updateCaptureChoice);
  void invoke<typeof targets>('capture_targets').then(items => {
    targets = items.filter(item => item.id !== 'qa-relay-fixture');
    target.replaceChildren();
    for (const item of targets) { const option = document.createElement('option'); option.value = item.id; option.textContent = item.name; target.appendChild(option); }
    updateCaptureChoice();
  }).catch(error => { status.textContent = String(error); start.disabled = true; });


  function clearPreview(message: string): void {
    context!.clearRect(0, 0, canvas.width, canvas.height);
    metrics.textContent = 'No retained preview';
    checks.textContent = message;
  }

  start.addEventListener('click', async () => {
    const selected = targets.find(item => item.id === target.value);
    if (!selected || (selected.full_display && !confirmDisplay.checked)) return;
    const confirmed = confirmDisplay.checked;
    confirmDisplay.checked = false;
    target.disabled = confirmDisplay.disabled = true;
    start.disabled = true;
    const current = ++generation;
    clearPreview('Waiting for native frames');
    try {
      status.className = '';
      status.textContent = 'Starting screen capture…';
      const message = await invoke<string>('start_capture', {target: target.value, confirmFullDisplay: confirmed});
      if (generation !== current) return;
      status.textContent = message;
      stop.disabled = false;
      let after = 0, frames = 0, lastSequence = 0;
      const first = performance.now();
      while (generation === current) {
        const packet = await invoke<ArrayBuffer | number[] | Uint8Array>('next_frame', { after });
        if (generation !== current) break;
        const buffer = packet instanceof ArrayBuffer ? packet : new Uint8Array(packet).buffer;
        if (buffer.byteLength === 0) { await new Promise(resolve => setTimeout(resolve, 20)); continue; }
        if (buffer.byteLength < 20) throw Error('Truncated native frame');
        const view = new DataView(buffer);
        const width = view.getUint32(0, true), height = view.getUint32(4, true);
        const stride = view.getUint32(8, true), sequence = Number(view.getBigUint64(12, true));
        if (width < 1 || height < 1 || width > 8192 || height > 8192 || stride < width * 4 ||
            buffer.byteLength !== 20 + stride * height || !Number.isSafeInteger(sequence) || sequence <= after) {
          throw Error('Invalid native frame layout or sequence');
        }
        const bgra = new Uint8Array(buffer, 20), rgba = new Uint8ClampedArray(width * height * 4);
        for (let y = 0; y < height; y++) {
          for (let x = 0; x < width; x++) {
            const source = y * stride + x * 4, destination = (y * width + x) * 4;
            rgba[destination] = bgra[source + 2];
            rgba[destination + 1] = bgra[source + 1];
            rgba[destination + 2] = bgra[source];
            rgba[destination + 3] = 255;
          }
        }
        canvas.width = width;
        canvas.height = height;
        context.putImageData(new ImageData(rgba, width, height), 0, 0);
        bgra.fill(0); rgba.fill(0);
        after = sequence;
        frames++;
        metrics.textContent = `Native frames: ${frames} · sequence ${sequence} · ${width} × ${height} · ${(frames * 1000 / (performance.now() - first)).toFixed(1)} frames/s preview`;
        if (frames >= 3 && sequence > lastSequence) {
          checks.textContent = 'Live native screen frames received and rendered; frame delivery is advancing';
        }
        lastSequence = sequence;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    } catch (error) {
      if (generation === current) {
        clearPreview('Capture ended · preview and success status cleared');
        status.className = 'error';
        status.textContent = `Capture error: ${error}`;
        await invoke<string>('stop_capture').catch(() => {});
      }
    } finally {
      if (generation === current) {
        stop.disabled = true;
        target.disabled = confirmDisplay.disabled = false;
        updateCaptureChoice();
      }
    }
  });

  stop.addEventListener('click', async () => {
    const current = ++generation;
    start.disabled = true;
    stop.disabled = true;
    clearPreview('Stopping capture · preview hidden');
    status.className = '';
    status.textContent = 'Stopping capture…';
    try {
      const message = await invoke<string>('stop_capture');
      if (generation === current) {
        status.textContent = message;
        checks.textContent = 'Stopped · native stream closed, preview and frame slot cleared';
      }
    } catch (error) {
      if (generation === current) {
        status.className = 'error';
        status.textContent = `Stop failed: ${error}`;
        checks.textContent = 'Cleanup failed · preview remains hidden';
      }
    } finally {
      if (generation === current) { target.disabled = confirmDisplay.disabled = false; updateCaptureChoice(); }
    }
  });
})();
