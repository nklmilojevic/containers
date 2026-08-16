// ---------------- Event delegation ----------------
// Every container here is re-rendered wholesale through innerHTML, which would
// drop any listener bound to its children. So behaviour is bound ONCE at the
// document root and dispatched on the nearest [data-action]/[data-change]
// ancestor of the event target. The indirection is also the security property:
// a handler is picked from this table by name, so nothing device-supplied ever
// reaches a place the browser would parse as code.
const ACTIONS = {},
  CHANGES = {},
  INPUTS = {},
  TOGGLES = {};
const onAction = (name, fn) => {
  ACTIONS[name] = fn;
};
const onChange = (name, fn) => {
  CHANGES[name] = fn;
};
// A <details> fires `toggle`, which — like `error` below — does NOT bubble, so
// it is caught in the capture phase. `delegate` still works unchanged: on a
// non-bubbling capture-phase event `ev.target` IS the <details>, so a nested
// one is attributed to itself rather than to its parent. Reading `.open` from a
// click on the <summary> would instead need a next-tick hack, because the
// browser has not flipped the attribute yet at that point.
const onToggle = (name, fn) => {
  TOGGLES[name] = fn;
};
// `change` on a range input only fires when the pointer is RELEASED, so a
// slider wired through it appears frozen while you drag. `input` fires on every
// step — including arrow keys — which is what a live control needs.
const onInput = (name, fn) => {
  INPUTS[name] = fn;
};
// The same lookup-by-name, for a fragment of MARKUP rather than for a handler.
// A card whose editor lives in its own module registers its renderer here and
// the panel that hosts it asks for it by name. That is what keeps the
// dependency one-way: `schedules.js` needs the open panel and the cached
// detail that `devices.js` holds, so `devices.js` must not import it back.
const SECTIONS = {};
const onSection = (name, fn) => {
  SECTIONS[name] = fn;
};
const renderSection = (name, ...args) => (SECTIONS[name] ? SECTIONS[name](...args) : '');

function delegate(table, attr) {
  return ev => {
    const el = ev.target && ev.target.closest ? ev.target.closest('[data-' + attr + ']') : null;
    if (!el) return;
    const fn = table[el.dataset[attr]];
    if (fn) fn(el, ev);
  };
}
document.addEventListener('click', delegate(ACTIONS, 'action'));
document.addEventListener('change', delegate(CHANGES, 'change'));
document.addEventListener('input', delegate(INPUTS, 'input'));
document.addEventListener('toggle', delegate(TOGGLES, 'toggle'), true);
// 'error' does not bubble, so a broken <img> can only be caught on the way DOWN.
document.addEventListener(
  'error',
  ev => {
    const t = ev.target;
    if (t && t.classList && t.classList.contains('hide-on-error')) t.style.display = 'none';
  },
  true,
);

export { onAction, onChange, onToggle, onInput, onSection, renderSection };
