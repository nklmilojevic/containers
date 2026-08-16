"""The panel's JavaScript must at least *evaluate* without throwing.

`node --check` parses; it does not run, so it cannot see a temporal-dead-zone
error. That distinction is not academic: registering a handler above the
`const onAction = ...` that defines it threw `Cannot access 'onAction' before
initialization` at load, which aborts the whole script — every tab, not just
the broken feature — while the file parsed perfectly and every Python test
passed. The panel was simply blank.

So this imports `static/js/main.js` against a DOM stub just big enough to reach
the end of its bootstrap. It is deliberately NOT a DOM test: nothing here
asserts what the page renders. It answers one question — does the panel survive
being loaded — because that is the failure that takes everything with it.

Importing the real entry point rather than reading one file also makes this the
check on the module graph: a typo in a relative path is `ERR_MODULE_NOT_FOUND`
here, and a name a module forgot to export is `SyntaxError`, both of which are
just as total as the dead-zone error above.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parent.parent / "petkit_local" / "web" / "static" / "js"
MAIN_JS = JS_DIR / "main.js"
PROVISION_JS = JS_DIR / "provision.js"

#: Everything the panel touches at load: the delegation listeners, the nav
#: wiring, `localStorage`, and the first `fetch` its bootstrap fires.
DOM_STUB = """
const noop = () => {};
const el = () => new Proxy({
  style: {setProperty: noop, removeProperty: noop}, dataset: {},
  classList: {add: noop, remove: noop, toggle: noop, contains: () => false},
  children: [], appendChild: noop, remove: noop, removeChild: noop,
  addEventListener: noop, setAttribute: noop, getAttribute: () => null,
  querySelector: () => null, querySelectorAll: () => [],
  closest: () => null, getBoundingClientRect: () => ({left: 0, bottom: 0}),
  offsetWidth: 0, textContent: '', innerHTML: '', value: '', checked: false,
  focus: noop, click: noop, isConnected: true,
}, {get: (t, k) => (k in t ? t[k] : noop), set: () => true});

globalThis.document = {
  addEventListener: noop, createElement: el, body: el(),
  getElementById: () => el(), querySelector: () => el(), querySelectorAll: () => [],
  documentElement: el(),
};
globalThis.window = {
  addEventListener: noop, innerWidth: 1280, scrollX: 0, scrollY: 0,
  matchMedia: () => ({matches: false, addEventListener: noop}),
};
globalThis.location = {pathname: '/', href: 'http://x/'};
globalThis.localStorage = {getItem: () => null, setItem: noop, removeItem: noop};
globalThis.fetch = () => Promise.resolve({json: () => Promise.resolve({}), ok: true});
// Node 21 turned `navigator` into a read-only accessor on globalThis, so a
// plain assignment throws `Cannot set property navigator` and takes this whole
// harness down before the panel is even imported. That is a difference between
// Node versions, not between environments: it passed on a local Node 20 and
// failed on CI's Node 22. defineProperty covers both, and the catch covers a
// future version that makes it non-configurable too — the panel only reads
// `navigator.clipboard`, and undefined is the right answer for it here.
try {
  Object.defineProperty(globalThis, 'navigator', {
    value: {...globalThis.navigator, language: 'en-GB', userAgent: 'node'},
    configurable: true,
    writable: true,
  });
} catch {
  /* whatever Node already provides is good enough */
}
globalThis.EventSource = class { constructor() {} addEventListener() {} close() {} };
globalThis.WebSocket = class { constructor() {} addEventListener() {} send() {} close() {} };
globalThis.requestAnimationFrame = noop;
globalThis.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.Intl = Intl;

process.on('unhandledRejection', () => {});   // network calls we stubbed away
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_panel_script_survives_being_loaded(tmp_path):
    harness = tmp_path / "harness.mjs"
    # A DYNAMIC import, because a static one is hoisted above the stub and the
    # first module to evaluate would find no `document`.
    harness.write_text(DOM_STUB + f"await import({MAIN_JS.as_uri()!r});\n" + "console.log('LOADED');\n")
    r = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert "LOADED" in r.stdout, (
        "the panel threw while loading, which blanks every tab:\n" f"{r.stderr.strip()[:2000]}"
    )


#: Run against the real `provision.js` export. This used to be concatenated
#: onto app.js's source, because a file evaluated as one function body keeps
#: its declarations to itself; the module exports them by name instead.
PROVISION_ASSERTIONS = """
const cases = [
  ['http, chrome-with-bluetooth-hidden', false, false],
  ['http, browser really has none',      false, true ],
];
for (const [label, hasBt, secure] of cases) {
  const {card, tooltip} = provisionWarning(hasBt, secure);
  if (secure === false && !/secure page/.test(card))
    throw new Error(label + ': insecure page did not produce the HTTPS warning: ' + card);
}
const chromeOnHttps = provisionWarning(false, true);
if (!/Chrome\\/Edge/.test(chromeOnHttps.card))
  throw new Error('a secure page with no Web Bluetooth must name the browser');
const ok = provisionWarning(true, true);
if (ok.card !== '' || ok.tooltip !== '')
  throw new Error('a working setup must warn about nothing: ' + JSON.stringify(ok));
console.log('PROVISION_OK');
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_a_plain_http_page_is_told_it_needs_https_not_a_different_browser(tmp_path):
    """Two users on Chrome over HTTP were told their browser was unsupported.

    Web Bluetooth is secure-context-only, so `navigator.bluetooth` is undefined
    on plain HTTP no matter which browser is running. The old code tested for it
    before testing the context, so the one case that is both the most common and
    the most fixable — HA served over HTTP — reported the one thing the user
    could not act on.
    """
    harness = tmp_path / "provision.mjs"
    harness.write_text(
        DOM_STUB
        + f"const {{provisionWarning}} = await import({PROVISION_JS.as_uri()!r});\n"
        + PROVISION_ASSERTIONS
    )
    r = subprocess.run(["node", str(harness)], capture_output=True, text=True, timeout=60)
    assert "PROVISION_OK" in r.stdout, r.stderr.strip()[:2000]
