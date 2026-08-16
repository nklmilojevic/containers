import { BASE, api, esc, toast, fmtBytes } from './core.js';
import { onAction } from './delegate.js';

// ---------------- Capture ----------------
async function loadCapture() {
  const c = await api('capture');
  const v = document.getElementById('capView');
  const what =
    '<p class="sub">Capture records raw device traffic to <code>.jsonl</code> files (one JSON object per line) for protocol analysis — every HTTP request/response and every MQTT frame. Use it to reverse-engineer new payloads or debug a device that won\'t connect.</p>' +
    '<div class="card" style="border-color:var(--warn);margin:0 0 14px"><p style="margin:0"><b>⚠ Every capture is sensitive.</b> These files are a verbatim recording of everything your device said and was told — nothing in them is filtered, because a capture is only useful if it is exact. Expect your <b>Wi-Fi SSID</b>, LAN addresses, the device serial and its signing secret in any of them, and in the <b>proxy</b> ones the full exchanges with PetKit <b>including your account credentials</b>. Read one before you send it anywhere.</p></div>';
  if (!c.enabled) {
    v.innerHTML =
      '<h3>Capture is off</h3>' +
      what +
      '<p class="sub">Turn <b>Traffic capture</b> on in <a data-action="goto-tab" data-tab="setup">Setup → Settings</a> to start recording to <code>' +
      esc(c.dir || '/data/capture') +
      '</code>. It applies immediately — there is no configuration option and no restart.</p>';
    return;
  }
  if (!c.files.length) {
    v.innerHTML =
      '<h3>Capture is on</h3>' +
      what +
      '<p class="mut">No files yet at <code>' +
      esc(c.dir) +
      '</code> — traffic will appear once a device talks to this app.</p>';
    return;
  }
  v.innerHTML =
    '<h3>Capture files</h3>' +
    what +
    '<table><thead><tr><th>File</th><th>Lines</th><th>Size</th><th></th></tr></thead><tbody>' +
    c.files
      .map(
        f => `<tr><td><a data-action="view-capture" data-name="${esc(f.name)}">${esc(f.name)}</a></td><td>${esc(f.lines)}</td><td>${esc(fmtBytes(f.size))}</td>
      <td class="cap-acts"><a href="${esc(BASE + 'api/capture/' + encodeURIComponent(f.name) + '/download')}">download</a>
      <a data-action="delete-capture" data-name="${esc(f.name)}" data-size="${esc(f.size)}" class="danger-link">delete</a></td></tr>`,
      )
      .join('') +
    '</tbody></table>' +
    `<p class="sub" style="margin-top:10px">Nothing prunes these — a capture is deliberate, so it is kept in full until you delete it. Total on disk: <b>${esc(fmtBytes(c.files.reduce((n, f) => n + f.size, 0)))}</b> in <code>${esc(c.dir)}</code>.</p>` +
    '<div id="capBody"></div>';
}
onAction('delete-capture', el => deleteCapture(el.dataset.name, Number(el.dataset.size)));
async function deleteCapture(name, size) {
  // Irreversible and there is no second copy — a capture is usually the only
  // record of whatever it was recorded to investigate.
  if (!confirm(`Delete ${name} (${fmtBytes(size)})?\n\nThis cannot be undone.`)) return;
  const r = await api('capture/' + encodeURIComponent(name), { method: 'DELETE' });
  toast(r.ok ? 'Deleted ' + name : 'Error: ' + (r.error || 'failed'));
  // Clear the viewer if it was showing the file that just went away.
  const body = document.getElementById('capBody');
  if (body && body.textContent.includes(name)) body.innerHTML = '';
  loadCapture();
}
onAction('view-capture', el => viewCapture(el.dataset.name));
async function viewCapture(name) {
  // The name is a directory entry, so it can hold anything a filesystem allows —
  // percent-encode it rather than pasting it straight into the request path.
  const r = await api('capture/' + encodeURIComponent(name) + '?limit=60');
  document.getElementById('capBody').innerHTML =
    '<b>' +
    esc(name) +
    '</b> <span class="mut">(last ' +
    r.records.length +
    ' of ' +
    r.total +
    ')</span><pre>' +
    esc(r.records.map(x => JSON.stringify(x, null, 1)).join('\n')) +
    '</pre>';
}

export { loadCapture };
