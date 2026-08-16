const BASE = location.pathname.endsWith('/') ? location.pathname : location.pathname + '/';
// Never rejects, and never resolves to something a caller could mistake for
// data: a dead backend, an Ingress error page and a 500 all come back as the
// `{error}` shape the server answers failures with. Most call sites carry no
// `.catch`, so a rejection here leaves a tab showing "Loading…" forever.
const api = async (p, o) => {
  let r;
  try {
    r = await fetch(BASE + 'api/' + p, o);
  } catch (e) {
    return { error: e.message || 'network error' };
  }
  let body;
  try {
    body = await r.json();
  } catch (e) {
    return { error: r.ok ? 'malformed response' : 'HTTP ' + r.status };
  }
  if (r.ok) return body;
  return { error: (body && body.error) || 'HTTP ' + r.status };
};

// Markup escaping for the two entity-decoding contexts this file builds:
// TEXT nodes and DOUBLE-QUOTED attribute values. In both, an escaped string can
// no longer open a tag or close the attribute, so it lands as inert text.
// It is NOT enough for: unquoted attributes, URL schemes (a `javascript:` href
// survives escaping untouched), or anything the browser parses as CODE. That
// last case is why this file has no inline `on*=` handler attributes at all —
// see the delegation table below. Handler arguments travel in data-* attributes
// and are only ever read back as strings or JSON.parse'd, never evaluated.
const ESC_MAP = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
  '/': '&#x2F;',
};
const esc = s => (s == null ? '' : String(s)).replace(/[&<>"'\/]/g, c => ESC_MAP[c]);
const fmtTs = t => (t ? new Date(t * 1000).toLocaleTimeString() : '—');
function relTime(t) {
  if (!t) return 'never';
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 2) return 'just now';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.floor(s / 60) + 'm ago';
  if (s < 86400) return Math.floor(s / 3600) + 'h ago';
  return Math.floor(s / 86400) + 'd ago';
}
function fmtBytes(n) {
  if (n == null) return '—';
  if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

// `navigator.clipboard` exists only in a secure context, and this panel is
// normally plain HTTP on the LAN — the same constraint that keeps Web Bluetooth
// off the Provision tab. So execCommand, deprecated as it is, is the path that
// actually runs here; the modern one is the exception, not the fallback.
function copyText(text) {
  if (!text) return;
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(
      () => toast('Copied'),
      () => toast('Could not copy — select it by hand'),
    );
    return;
  }
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (e) {
    ok = false;
  }
  document.body.removeChild(ta);
  toast(ok ? 'Copied' : 'Could not copy — select it by hand');
}

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._t);
  t._t = setTimeout(() => t.classList.remove('show'), 2600);
}

export { BASE, api, esc, fmtTs, relTime, fmtBytes, copyText, toast };
