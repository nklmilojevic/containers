import { esc } from './core.js';
import { onAction } from './delegate.js';

// ---------------- Inline help ----------------
// A `?` next to a label, opening a small popover. Explanations that used to sit
// under a heading as a paragraph live in here instead, so the page reads as
// controls rather than as prose.
//
// The text travels in a data-* attribute rather than `title=`, for three
// reasons: the native tooltip never appears on touch, its delay and wrapping
// are the OS's to decide, and it cannot hold markup. It is escaped on the way
// in and assigned with textContent on the way out, so it is inert either way.
//
// The popover is a single element parented to <body>, NOT a sibling of the
// button: every container in this file is re-rendered wholesale through
// innerHTML, which would blow away an inline one mid-read. That does mean the
// anchor can vanish underneath it, hence the isConnected check while open.
const help = text =>
  `<button class="help" type="button" data-action="help" data-help="${esc(text)}" aria-label="What is this?">?</button>`;

let HELP_ANCHOR = null;
let HELP_WATCH = 0;

function closeHelp() {
  const pop = document.getElementById('helpPop');
  if (pop) pop.remove();
  if (HELP_ANCHOR) HELP_ANCHOR.setAttribute('aria-expanded', 'false');
  HELP_ANCHOR = null;
  clearInterval(HELP_WATCH);
  HELP_WATCH = 0;
}

// Registered down here, not beside `help()` above: `onAction` is a `const` in
// the delegation table below, so calling it earlier hits the temporal dead zone
// and takes the whole script — every tab — down with it.
onAction('help', (el, ev) => {
  ev.preventDefault();
  ev.stopPropagation();
  const same = HELP_ANCHOR === el;
  closeHelp();
  if (same) return; // second click on the same `?` closes it

  const pop = document.createElement('div');
  pop.id = 'helpPop';
  pop.setAttribute('role', 'tooltip');
  pop.textContent = el.dataset.help || '';
  document.body.appendChild(pop);

  // Placed after appending so the width is known. Clamped to the viewport so a
  // `?` near the right edge does not push the popover off-screen — the panel
  // has controls hard against that edge.
  const r = el.getBoundingClientRect();
  const w = pop.offsetWidth;
  const left = Math.max(8, Math.min(r.left + window.scrollX, window.innerWidth - w - 8));
  pop.style.left = left + 'px';
  pop.style.top = r.bottom + window.scrollY + 6 + 'px';

  el.setAttribute('aria-expanded', 'true');
  HELP_ANCHOR = el;
  HELP_WATCH = setInterval(() => {
    if (!el.isConnected) closeHelp();
  }, 500);
});

document.addEventListener('click', ev => {
  if (HELP_ANCHOR && !ev.target.closest('#helpPop') && !ev.target.closest('[data-action="help"]'))
    closeHelp();
});
document.addEventListener('keydown', ev => {
  if (ev.key === 'Escape') closeHelp();
});
window.addEventListener('scroll', closeHelp, true);
window.addEventListener('resize', closeHelp);

export { help };
