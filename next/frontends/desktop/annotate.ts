// Drawing on the remote screen: the technician switches to pen mode and draws on the remote
// picture with a pen, a highlighter or an arrow. Strokes go to the remote computer, which shows
// them over its screen for the person there (view-only sessions too: nothing is clicked or
// typed), and they are drawn here as well. Everything fades a few seconds after drawing stops,
// like on the remote computer. A remote computer whose picture already contains the strokes
// (Linux X11, `drawpic`) shows them in the picture, so finished strokes are left to it here.
(() => {
  const invoke = window.__TAURI__.core.invoke;
  const canvas = document.getElementById('remote-canvas') as HTMLCanvasElement | null;
  const pointerButton = document.getElementById('remote-pointer');
  const surface = canvas?.parentElement;
  if (!canvas || !pointerButton || !surface) return;

  type Tool = 'pen' | 'highlighter' | 'arrow';
  type Point = [number, number];
  type Stroke = {id: number; tool: Tool; color: number; width: number; points: Point[]; done: boolean; finished: number};
  type Message = {kind: 'stroke'; id: number; tool: Tool; color: number; width: number; points: Point[]; done: boolean} | {kind: 'undo'} | {kind: 'clear'};
  type State = {phase: string; session: string | null; sharing: boolean; peer_features: string[]};

  // Same values as core_protocol::annotation.
  const COLORS = ['#E5322D', '#FFD400', '#22B14C', '#1E7BFF', '#FFFFFF'];
  const COLOR_NAMES = ['Red', 'Yellow', 'Green', 'Blue', 'White'];
  const PEN_WIDTHS = [3, 5, 8, 12], HIGHLIGHT_WIDTHS = [14, 20, 28, 36];
  const HIGHLIGHT_ALPHA = 0.35, FADE_AFTER = 5000, FADE = 600, IN_PICTURE_AFTER = 1500;
  const MAX_MESSAGE = 64, MAX_STROKE = 4096, MAX_STROKES = 100, BATCH = 50;

  const make = <K extends keyof HTMLElementTagNameMap>(tag: K, id: string, text = '', className = 'button-quiet'): HTMLElementTagNameMap[K] => {
    const value = document.createElement(tag);
    value.id = id;
    if (className) value.className = className;
    if (text) value.textContent = text;
    return value;
  };
  const toggle = make('button', 'remote-draw', 'Draw: off');
  toggle.type = 'button';
  toggle.hidden = true;
  toggle.setAttribute('aria-pressed', 'false');
  toggle.title = 'Draw on the remote screen. The person at the computer sees it; nothing is clicked.';
  const bar = make('span', 'remote-draw-tools', '', 'draw-tools');
  bar.hidden = true;
  bar.setAttribute('role', 'toolbar');
  bar.setAttribute('aria-label', 'Drawing tools');
  const toolButtons: Record<Tool, HTMLButtonElement> = {pen: make('button', 'remote-draw-pen', 'Pen'), highlighter: make('button', 'remote-draw-highlighter', 'Highlighter'), arrow: make('button', 'remote-draw-arrow', 'Arrow')};
  const colorButtons = COLORS.map((color, index) => {
    const swatch = make('button', `remote-draw-color-${index}`, '', 'button-quiet draw-swatch');
    swatch.setAttribute('aria-label', COLOR_NAMES[index]);
    swatch.title = COLOR_NAMES[index];
    swatch.style.setProperty('--swatch', color);
    return swatch;
  });
  const widthButton = make('button', 'remote-draw-width', 'Size: 2');
  widthButton.title = 'Line width';
  const undoButton = make('button', 'remote-draw-undo', 'Undo');
  const clearButton = make('button', 'remote-draw-clear', 'Clear');
  for (const button of [...Object.values(toolButtons), ...colorButtons, widthButton, undoButton, clearButton]) {
    button.type = 'button';
    bar.append(button);
  }
  pointerButton.after(toggle, bar);
  // The strokes are drawn on a layer over the remote picture (which is redrawn for every frame).
  const layer = make('canvas', 'remote-draw-layer', '', 'draw-layer');
  layer.setAttribute('aria-hidden', 'true');
  layer.hidden = true;
  surface.append(layer);
  const ink = layer.getContext('2d');
  const style = document.createElement('style');
  style.textContent = '.draw-tools{display:inline-flex;align-items:center;flex-wrap:wrap;gap:3px;padding:0 4px;border-left:1px solid var(--line);border-right:1px solid var(--line)}.draw-tools[hidden]{display:none}'
    + '.draw-tools button[aria-pressed=true]{background:var(--brand-soft);color:var(--brand)}'
    + '.draw-swatch{width:27px;padding:0!important}.draw-swatch::before{content:"";width:13px;height:13px;border-radius:50%;background:var(--swatch);box-shadow:0 0 0 1px #c3cad4}'
    + '.draw-swatch[aria-pressed=true]::before{box-shadow:0 0 0 2px var(--panel),0 0 0 4px var(--brand)}'
    + '.draw-layer{position:absolute;left:0;top:0;pointer-events:none;touch-action:none}.draw-layer[hidden]{display:none}.draw-layer[data-drawing=true]{pointer-events:auto;cursor:crosshair}';
  document.head?.appendChild(style);

  let state: State = {phase: 'offline', session: null, sharing: false, peer_features: []};
  let session: string | null = null;
  let drawing = false, tool: Tool = 'pen', color = 0, width = 2;
  let strokes: Stroke[] = [];
  let active: {stroke: Stroke; outbox: Point[]; pointer: number; firstSent: boolean} | null = null;
  let lastChange = 0, nextId = 1, painted = false;
  const queue: {session: string; message: Message}[] = [];
  let sending = false;

  const available = (): boolean => state.phase === 'active' && !state.sharing && !!state.session && state.peer_features.includes('draw');
  const inPicture = (): boolean => state.peer_features.includes('drawpic');
  // Set a label only when its English text changes: the translation replaces the text shown.
  const labels = new WeakMap<HTMLElement, string>();
  const label = (target: HTMLElement, text: string): void => { if (labels.get(target) !== text) { labels.set(target, text); target.textContent = text; } };

  function render(): void {
    const show = available();
    toggle.hidden = !show;
    if (!show) drawing = false;
    bar.hidden = !show || !drawing;
    label(toggle, `Draw: ${drawing ? 'on' : 'off'}`);
    toggle.setAttribute('aria-pressed', String(drawing));
    for (const name of Object.keys(toolButtons) as Tool[]) toolButtons[name].setAttribute('aria-pressed', String(name === tool));
    colorButtons.forEach((button, index) => button.setAttribute('aria-pressed', String(index === color)));
    label(widthButton, `Size: ${width}`);
    layer.dataset.drawing = String(drawing);
    paint();
  }

  // Sends in order, one at a time; a stalled session drops the rest.
  async function send(message: Message): Promise<void> {
    if (!session) return;
    if (queue.length >= 256) queue.length = 0;
    queue.push({session, message});
    if (sending) return;
    sending = true;
    try {
      while (queue.length) {
        const next = queue.shift()!;
        if (next.session !== session) continue;
        try { await invoke<boolean>('remote_draw', next); } catch { /* the session reports its own end */ }
      }
    } finally { sending = false; }
  }

  // The points drawn since the last message, at most 64 to a message.
  function flush(): void {
    if (!active) return;
    const {stroke} = active;
    const points = active.outbox.splice(0);
    if (!points.length && !stroke.done) return;
    do {
      const part = points.splice(0, MAX_MESSAGE);
      void send({kind: 'stroke', id: stroke.id, tool: stroke.tool, color: stroke.color, width: stroke.width, points: part, done: stroke.done && !points.length});
    } while (points.length);
    active.firstSent = true;
    if (stroke.done) active = null;
  }

  function reset(): void {
    strokes = []; active = null; drawing = false; queue.length = 0;
  }

  /** A point of the remote picture (0..65535) under the pointer, and the drawn picture area. */
  function picture(): {left: number; top: number; width: number; height: number} | null {
    if (canvas!.hidden || !canvas!.width || !canvas!.height) return null;
    const bounds = canvas!.getBoundingClientRect();
    if (bounds.width <= 0 || bounds.height <= 0) return null;
    const scale = Math.min(bounds.width / canvas!.width, bounds.height / canvas!.height);
    const width = canvas!.width * scale, height = canvas!.height * scale;
    return {left: bounds.left + (bounds.width - width) / 2, top: bounds.top + (bounds.height - height) / 2, width, height};
  }
  function at(event: {clientX: number; clientY: number}): Point | null {
    const area = picture();
    if (!area) return null;
    const scaled = (value: number): number => Math.round(Math.max(0, Math.min(1, value)) * 65535);
    return [scaled((event.clientX - area.left) / area.width), scaled((event.clientY - area.top) / area.height)];
  }

  const lineWidth = (stroke: Stroke, height: number): number => Math.max(1, ((stroke.tool === 'highlighter' ? HIGHLIGHT_WIDTHS : PEN_WIDTHS)[stroke.width - 1] ?? 3) * height / 1080);

  /** Opacity of the whole drawing now (it fades like on the remote computer); clears it at the end. */
  function opacity(now: number): number {
    if (!strokes.length) return 0;
    if (active && now - lastChange < FADE_AFTER) return 1;
    const since = now - lastChange;
    if (since < FADE_AFTER) return 1;
    if (since < FADE_AFTER + FADE) return 1 - (since - FADE_AFTER) / FADE;
    strokes = []; active = null;
    return 0;
  }

  function paint(): void {
    const now = Date.now();
    const alpha = opacity(now);
    const area = picture();
    const visible = strokes.filter(stroke => !(inPicture() && stroke.done && now - stroke.finished >= IN_PICTURE_AFTER));
    layer.hidden = !area || (!drawing && !visible.length);
    if (!ink || !area || layer.hidden) {
      if (painted && ink) { ink.clearRect(0, 0, layer.width, layer.height); painted = false; }
      return;
    }
    // The layer covers exactly the drawn picture, in device pixels for sharp lines.
    const ratio = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
    const room = surface!.getBoundingClientRect();
    layer.style.left = `${area.left - room.left + (surface!.scrollLeft || 0)}px`;
    layer.style.top = `${area.top - room.top + (surface!.scrollTop || 0)}px`;
    layer.style.width = `${area.width}px`;
    layer.style.height = `${area.height}px`;
    const pixels = [Math.round(area.width * ratio), Math.round(area.height * ratio)];
    if (layer.width !== pixels[0] || layer.height !== pixels[1]) { layer.width = pixels[0]; layer.height = pixels[1]; }
    ink.setTransform(1, 0, 0, 1, 0, 0);
    ink.clearRect(0, 0, layer.width, layer.height);
    painted = visible.length > 0;
    ink.setTransform(ratio, 0, 0, ratio, 0, 0);
    ink.lineCap = 'round';
    ink.lineJoin = 'round';
    const place = ([x, y]: Point): Point => [x * (area.width - 1) / 65535, y * (area.height - 1) / 65535];
    for (const stroke of visible) {
      const line = lineWidth(stroke, area.height);
      ink.globalAlpha = alpha * (stroke.tool === 'highlighter' ? HIGHLIGHT_ALPHA : 1);
      ink.strokeStyle = ink.fillStyle = COLORS[stroke.color] ?? COLORS[0];
      ink.lineWidth = line;
      const points = stroke.tool === 'arrow' ? [stroke.points[0], stroke.points[stroke.points.length - 1]] : stroke.points;
      const [from, to] = [place(points[0]), place(points[points.length - 1])];
      const length = Math.hypot(to[0] - from[0], to[1] - from[1]);
      if (points.length === 1 || length < 1 && stroke.tool === 'arrow') {
        ink.beginPath(); ink.arc(to[0], to[1], line / 2, 0, Math.PI * 2); ink.fill();
        continue;
      }
      ink.beginPath();
      if (stroke.tool !== 'arrow') {
        ink.moveTo(...place(points[0]));
        for (const point of points.slice(1)) ink.lineTo(...place(point));
        ink.stroke();
        continue;
      }
      // Arrow: a straight line from the first to the last point and a head (as on the remote side).
      const [ux, uy] = [(to[0] - from[0]) / length, (to[1] - from[1]) / length];
      const head = Math.min(length, Math.max(line * 3, 12 * area.height / 1080)), half = head * 0.53;
      if (length > head) { ink.moveTo(...from); ink.lineTo(to[0] - ux * head * 0.8, to[1] - uy * head * 0.8); ink.stroke(); }
      const base = [to[0] - ux * head, to[1] - uy * head];
      ink.beginPath(); ink.moveTo(...to); ink.lineTo(base[0] - uy * half, base[1] + ux * half); ink.lineTo(base[0] + uy * half, base[1] - ux * half); ink.closePath(); ink.fill();
    }
    ink.globalAlpha = 1;
  }
  let frameRequested = false;
  function repaint(): void {
    if (frameRequested) return;
    frameRequested = true;
    const run = (): void => { frameRequested = false; paint(); };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run); else run();
  }

  document.addEventListener('mixel-remote-state', event => {
    const detail = (event as CustomEvent<State>).detail;
    state = {phase: detail.phase, session: detail.session ?? null, sharing: !!detail.sharing, peer_features: detail.peer_features ?? []};
    if (state.session !== session || !available()) { if (session || strokes.length) reset(); session = available() ? state.session : null; }
    render();
  });
  toggle.addEventListener('click', () => {
    if (!available()) return;
    drawing = !drawing;
    if (!drawing) finish();
    render();
  });
  for (const name of Object.keys(toolButtons) as Tool[]) toolButtons[name].addEventListener('click', () => { tool = name; render(); });
  colorButtons.forEach((button, index) => button.addEventListener('click', () => { color = index; render(); }));
  widthButton.addEventListener('click', () => { width = width % 4 + 1; render(); });
  undoButton.addEventListener('click', () => {
    if (!session) return;
    // A stroke being drawn is finished first, so the remote computer undoes that one.
    finish();
    strokes.pop();
    lastChange = Date.now();
    void send({kind: 'undo'});
    render();
  });
  clearButton.addEventListener('click', () => {
    if (!session) return;
    strokes = []; active = null;
    void send({kind: 'clear'});
    render();
  });

  function finish(): void {
    if (!active) return;
    active.stroke.done = true;
    active.stroke.finished = lastChange = Date.now();
    flush();
  }
  layer.addEventListener('pointerdown', event => {
    if (!drawing || !session || event.button !== 0) return;
    const point = at(event);
    if (!point) return;
    event.preventDefault();
    finish();
    try { layer.setPointerCapture(event.pointerId); } catch { /* not captured */ }
    const stroke: Stroke = {id: nextId, tool, color, width, points: [point], done: false, finished: 0};
    nextId = nextId % 65535 + 1;
    strokes.push(stroke);
    if (strokes.length > MAX_STROKES) strokes.shift();
    active = {stroke, outbox: [point], pointer: event.pointerId, firstSent: false};
    lastChange = Date.now();
    repaint();
  });
  layer.addEventListener('pointermove', event => {
    if (!active || event.pointerId !== active.pointer) return;
    const point = at(event);
    if (!point) return;
    event.preventDefault();
    const {stroke} = active;
    const last = stroke.points[stroke.points.length - 1];
    if (stroke.tool === 'arrow') {
      // Only the end moves: the remote side draws from the first to the last point.
      if (last[0] === point[0] && last[1] === point[1]) return;
      if (stroke.points.length > 1) stroke.points[1] = point; else stroke.points.push(point);
      active.outbox = active.firstSent ? [point] : [stroke.points[0], point];
    } else {
      // Leave out movements of less than a pixel of the picture.
      const step = 0.75 * 65535 / Math.max(1, canvas.width);
      if (Math.abs(last[0] - point[0]) < step && Math.abs(last[1] - point[1]) < step) return;
      if (stroke.points.length >= MAX_STROKE) return;
      stroke.points.push(point);
      active.outbox.push(point);
    }
    lastChange = Date.now();
    repaint();
  });
  for (const name of ['pointerup', 'pointercancel'] as const) layer.addEventListener(name, event => {
    if (!active || event.pointerId !== active.pointer) return;
    finish();
    try { if (layer.hasPointerCapture(event.pointerId)) layer.releasePointerCapture(event.pointerId); } catch { /* released */ }
    repaint();
  });
  layer.addEventListener('contextmenu', event => { if (drawing) event.preventDefault(); });
  // Batches the points drawn (about 20 messages a second) and keeps the fade going.
  window.setInterval(() => { flush(); if (strokes.length || painted || drawing) paint(); }, BATCH);
  render();
})();
