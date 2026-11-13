// One in-app dialog for confirmations and small forms (rename, move, invite, deploy link…).
// Others call window.mixelDialog.open(…); `run` does the work while the dialog shows it is busy.
// An error keeps the dialog open with the message; a returned result node replaces the fields
// (for something to copy), otherwise the dialog closes.
(() => {
  type Field = {id: string; kind: 'text' | 'email' | 'number' | 'select'; label: string; value?: string; options?: {value: string; label: string; disabled?: boolean}[]; min?: number; max?: number; maxLength?: number; required?: boolean; hint?: string};
  type Options = {title: string; text?: string; fields?: Field[]; confirm: string; danger?: boolean; run: (values: Record<string, string>) => Promise<HTMLElement | void>};
  function element<T extends HTMLElement>(id: string): T { const node = document.getElementById(id); if (!node) throw Error(`Missing dialog control ${id}`); return node as T; }
  const root = element<HTMLDivElement>('manage-dialog'), form = element<HTMLFormElement>('manage-dialog-form');
  const title = element<HTMLElement>('manage-dialog-title'), text = element<HTMLElement>('manage-dialog-text'), fields = element<HTMLDivElement>('manage-dialog-fields');
  const error = element<HTMLElement>('manage-dialog-error'), cancel = element<HTMLButtonElement>('manage-dialog-cancel'), confirm = element<HTMLButtonElement>('manage-dialog-confirm');
  let current: Options | null = null, inputs: Record<string, HTMLInputElement | HTMLSelectElement> = {}, busy = false, done = false, opener: HTMLElement | null = null;
  function close(): void {
    if (busy) return;
    root.hidden = true; current = null; inputs = {}; fields.replaceChildren(); error.textContent = '';
    if (opener && typeof opener.focus === 'function') opener.focus();
    opener = null;
  }
  function open(options: Options): void {
    current = options; done = false; busy = false; inputs = {};
    opener = (document as Document & {activeElement?: HTMLElement | null}).activeElement ?? null;
    title.textContent = options.title; text.textContent = options.text ?? ''; text.hidden = !options.text;
    error.textContent = ''; fields.replaceChildren();
    for (const field of options.fields ?? []) {
      const label = document.createElement('label'); label.className = 'stacked-label';
      const caption = document.createElement('span'); caption.textContent = field.label;
      let input: HTMLInputElement | HTMLSelectElement;
      if (field.kind === 'select') {
        const select = document.createElement('select');
        for (const item of field.options ?? []) { const option = document.createElement('option'); option.value = item.value; option.textContent = item.label; option.disabled = !!item.disabled; select.appendChild(option); }
        select.value = field.value ?? field.options?.find(item => !item.disabled)?.value ?? '';
        input = select;
      } else {
        const box = document.createElement('input'); box.type = field.kind; box.value = field.value ?? ''; box.autocomplete = 'off';
        if (field.maxLength) box.maxLength = field.maxLength;
        if (field.min !== undefined) box.min = String(field.min);
        if (field.max !== undefined) box.max = String(field.max);
        box.required = field.required !== false;
        input = box;
      }
      input.dataset.field = field.id; inputs[field.id] = input;
      label.append(caption, input);
      if (field.hint) { const hint = document.createElement('small'); hint.className = 'field-hint'; hint.textContent = field.hint; label.append(hint); }
      fields.append(label);
    }
    confirm.textContent = options.confirm; confirm.className = options.danger ? 'button-danger' : 'button-primary';
    confirm.disabled = cancel.disabled = false; cancel.hidden = false; cancel.textContent = 'Cancel';
    root.hidden = false;
    const first = Object.values(inputs)[0] ?? confirm;
    if (typeof first.focus === 'function') first.focus();
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!current || busy) return;
    if (done) { close(); return; }
    const values: Record<string, string> = {};
    for (const [id, input] of Object.entries(inputs)) values[id] = input.value.trim();
    const missing = Object.values(inputs).find(input => (input as HTMLInputElement).required === true && !input.value.trim());
    if (missing) { error.textContent = 'Fill in every field.'; return; }
    const options = current;
    busy = true; confirm.disabled = cancel.disabled = true; error.textContent = '';
    const label = confirm.textContent; confirm.textContent = 'Please wait…';
    try {
      const result = await options.run(values);
      if (current !== options) return;
      busy = false;
      if (result) {
        done = true; fields.replaceChildren(result); text.hidden = true;
        confirm.textContent = 'Done'; confirm.className = 'button-primary'; confirm.disabled = false; cancel.hidden = true;
      } else close();
    } catch (failure) {
      if (current !== options) return;
      busy = false; confirm.disabled = cancel.disabled = false; confirm.textContent = label;
      error.textContent = String(failure).replace(/^Error: /, '');
    }
  });
  cancel.addEventListener('click', close);
  root.addEventListener('keydown', event => { if ((event as KeyboardEvent).key === 'Escape') close(); });
  (window as unknown as {mixelDialog: {open: typeof open; close: typeof close}}).mixelDialog = {open, close};
  // Texts the page translator cannot reach (choices inside a select): the same table, looked up here.
  (window as unknown as {mixelText: (value: string) => string}).mixelText = (value: string): string => {
    const all = (window as unknown as {MIXEL_I18N?: Record<string, {texts: Record<string, string>}>}).MIXEL_I18N;
    return all?.[document.documentElement?.lang ?? 'en']?.texts[value] ?? value;
  };
})();
