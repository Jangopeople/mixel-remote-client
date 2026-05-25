# UI Tokens & Accessibility — Audit Rules

Load when the PR touches the frontend UI — components, screens, styles, tokens.

Audit against `docs/ui/UI_SPEC.md` (the design contract for this product) + `docs/ui/tokens.css` (the token source of truth) + MIXEL design principles.

---

## Token discipline

### Hardcoded values

🔴 Hardcoded color hex codes / RGB values in components (e.g. `bg-[#C8102E]`, `color: rgb(200, 16, 46)`). Use the brand token (`--brand`).

🔴 Hardcoded sizes / spacing not on the scale (e.g. `p-[17px]` instead of `p-4`, `p-5`, etc.).

🔴 Hardcoded radii not matching `--radius-*` tokens.

🔴 Hardcoded shadow values instead of `--shadow-*` tokens.

🟡 Custom colors added inline that aren't proposed as new tokens. If a color is genuinely needed, propose adding it to `tokens.css` instead.

🟡 Tailwind classes using arbitrary values (`bg-[...]`, `p-[...]`, `text-[...]`) without a token rationale in a comment.

### Token consistency

🟡 New component reproduces styling logic that should come from a shared variant utility (e.g. button variants).

🟡 Status colors (success / warning / error / info) used for non-status purposes (e.g. green just because it "looks nice"). Status colors are semantically reserved.

🟢 Token naming inconsistent with the `tokens.css` convention (semantic over visual: `--bg-surface` not `--gray-50`).

---

## Typography

🟡 Font sizes outside the established type scale (5–6 sizes max per MIXEL Design Principles).

🟡 Multiple font weights used for the same hierarchy level on the same screen.

🟡 Letter-spacing applied to body text (should only be on large headings).

🟢 Mono font (`--font-mono`) used for non-code / non-ID content.

🟢 Heading hierarchy skipped (`h1` → `h3` without `h2`) — bad for accessibility too.

---

## Spacing

🟡 Spacing values not on the 4px scale (4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64).

🟡 Inconsistent vertical rhythm — same hierarchy level uses different gaps across screens.

🟢 Negative margins used to overcome layout instead of fixing the layout structure.

---

## Component reuse

🔴 New custom component when an existing UI Spec component would have worked (e.g. custom button instead of using the standard Button variants).

🟡 Existing shadcn/ui component re-implemented from scratch.

🟡 Component variant created inline instead of added to the variant API.

🟢 Component file > 200 lines without an obvious split.

---

## Layout consistency with UI Spec

🔴 Screen doesn't match `docs/ui/screens/<slug>.tsx` mockup for the default state (excludes legitimate state variants — empty / loading / error / success / edge).

🟡 Navigation pattern deviates from the global pattern set in `UI_SPEC.md` §3 (sidebar / topbar / tabs).

🟡 Spacing / sectioning doesn't match the mockup.

🟢 Minor visual variance from mockup (e.g. icon placement) without justification.

---

## States — empty / loading / error / success / edge

🔴 Screen ships without an empty state for entities that can be empty.

🔴 Operation > 200ms without a loading indicator.

🔴 Operation that can fail without an error state.

🟡 Empty state has no CTA — should always guide user to the next action.

🟡 Empty state copy is "No data." or similar non-helpful — should explain *what's missing* + *why it matters* + *one clear CTA*.

🟡 Error state shows technical message instead of user-facing copy.

🟡 Error state doesn't include a recovery action (retry button, edit form, contact support).

🟡 Loading state uses a spinner where a skeleton matching the final layout would be clearer.

🟢 Skeleton shimmer duration too long / too short (target 1.5s, opacity range 0.4 → 0.8).

---

## Confirmations and destructive actions

🔴 Destructive action (delete, archive, void) without confirmation.

🟡 Confirmation modal without a final action button label matching the action (`Delete` not `OK`).

🟡 No undo affordance for high-stakes reversible operations.

🟢 Destructive button not visually distinct (should use error / red treatment).

---

## Forms

🔴 Form validates on every keystroke (annoying) for inputs that aren't password / strength meters.

🟡 No inline validation — only on submit.

🟡 Submit button disabled when there are validation errors (better: enabled, click reveals errors).

🟡 Required fields marked inconsistently (mix of asterisks and "optional" suffix).

🟡 Label position inconsistent (above input is standard).

🟢 Placeholder used as a substitute for label (accessibility issue + form clears when typed).

---

## Modals vs sheets vs full pages

🟡 Multi-step task / form > 5 fields shown in a modal (use a sheet or dedicated page).

🟡 Quick task / single-field interaction on a full page (use a modal or inline).

🟢 Modal max-width inconsistent across the product (standard: 400–600px).

🟢 Sheet width inconsistent (standard: 400–500px on right/bottom).

---

## Tables

🟡 Table on mobile without card transformation (horizontal scrolling raw table on mobile).

🟡 No row hover state.

🟡 Sortable headers without sort indicator.

🟡 Action buttons inconsistent across rows (icons in one column, kebab menu in next).

🟢 Sticky header missing on long tables.

🟢 Pagination position inconsistent.

---

## Accessibility (WCAG 2.1 AA minimum)

### Keyboard

🔴 Interactive element not keyboard-reachable.

🔴 Custom keyboard shortcut hijacks browser / OS combinations.

🟡 Focus order doesn't match visual order on a non-trivial page.

🟡 Modal opens but focus doesn't move into it / doesn't return to trigger on close.

### Focus

🔴 Focus ring removed without a custom visible alternative (`outline: none` without `focus-visible:ring-*`).

🟡 Focus ring uses a color that doesn't contrast against all backgrounds it can sit on.

### ARIA

🔴 Icon-only button without `aria-label`.

🟡 Custom dropdown / combobox without proper ARIA roles (`role="combobox"`, `aria-expanded`, `aria-haspopup`, etc.).

🟡 `aria-live` regions missing for dynamic content (toasts, alerts).

🟡 Empty `alt` attribute on a decorative image without `role="presentation"`.

### Color contrast

🔴 Body text < 4.5:1 contrast against background.

🟡 Large text < 3:1 contrast.

🟡 UI component (button, input border) < 3:1 contrast against adjacent color.

🟡 Color-only information conveyance (red dot for error, green dot for success, no icon or text).

### Motion

🟡 Animation without `prefers-reduced-motion: reduce` respect.

🟢 Animation duration > 400ms for utility transitions (page changes, hover).

---

## Internationalization in UI

🔴 Hardcoded English string in user-facing UI (must be i18n key).

🟡 Button / label layout pixel-tight to English string — German will overflow.

🟡 Date / number / currency rendered with `toLocaleString()` without locale parameter.

🟢 Translation key naming inconsistent with the product's pattern.

---

## Dark mode (if in scope)

🟡 Component hardcodes colors that should adapt via tokens.

🟡 Hover / focus states tested only in light mode.

🟢 Brand color contrast worse in dark mode than light.

---

## Performance for UI

🟡 Large image loaded eagerly above the fold without lazy-load attribute.

🟡 Heavy component imported without code splitting (`React.lazy`).

🟡 Animation runs on the main thread for >50ms blocks.

🟢 Web font loaded without `font-display: swap`.

---

## Reverse-engineering finding patterns (App Doctor pairing)

When auditing existing apps, common UI debt:

- Tokens added late, only used in new components → 🟡 design system inconsistent across screens
- Empty / loading / error states retrofitted as an afterthought → 🟡 inconsistent UX
- Accessibility never audited → 🔴 dozens of focus / contrast / ARIA issues compound
- German strings overflow because layouts were built English-first → 🟡 layout regressions per locale

Flag these for the App Doctor's remediation plan.
