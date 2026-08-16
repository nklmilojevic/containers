import { api, esc, toast, fmtBytes } from './core.js';
import { onAction } from './delegate.js';

// ---------------- Patchers ----------------
async function loadPatchers() {
  const ds = await api('devices');
  const v = document.getElementById('patchView');
  if (!ds.length) {
    v.innerHTML =
      '<div class="empty"><b>No devices connected</b><p class="mut">Connect a device first, then come back to apply patches.</p></div>';
    return;
  }
  let html = `<div class="card" style="border-color:var(--warn)">
    <p style="margin:0"><b>Warning: firmware modification</b> — These patchers modify files on the device filesystem. This is done at your own risk. The authors are not responsible for any bricking, damage, loss of warranty, or other issues. Read each description carefully before applying.</p>
    <p style="margin:4px 0 0;font-size:0.85em;opacity:0.8">All patches are stored on the writable /system partition (jffs2). The read-only /app partition (squashfs) is never modified. Removing a patch just deletes the files from /system and reboots — the device returns to stock firmware.</p></div>`;
  for (const dev of ds) {
    const p = await api('devices/' + dev.id + '/patcher');
    if (!p.supported) {
      html += `<div class="card"><h3>${esc(dev.name)} <span class="chip">${esc(dev.type.toUpperCase())} · #${esc(dev.id)}</span></h3><p class="mut">Patchers are only available for the Linux models (T5, T6, T7, D4H, D4SH, W7H). This device uses a different platform.</p></div>`;
      continue;
    }
    const ip = p.device_ip || '?';
    html += `<div class="card"><h3>${esc(dev.name)} <span class="chip">${esc(dev.type.toUpperCase())} · #${esc(dev.id)}</span></h3>`;
    if (!ip || ip === '?')
      html +=
        '<p class="badge warn">Device IP unknown — wait for a state_report before patching.</p>';
    for (const [pid, pt] of Object.entries(p.patchers || {})) {
      // Real newlines in the description are rendered by `white-space:pre-line`;
      // only the literal two-character sequence "\n" becomes a <br>. Splitting
      // first and escaping each piece keeps that without ever un-escaping.
      const desc = String(pt.description ?? '')
        .split('\\n')
        .map(esc)
        .join('<br>');
      html += `<div class="card" style="margin:8px 0;background:var(--card2)">
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
          <b>${esc(pt.name)}</b>
          ${pt.applied ? '<span class="badge ok">applied</span>' : '<span class="badge">not applied</span>'}
          ${pt.unavailable ? `<span class="badge warn">${esc(pt.unavailable)}</span>` : pt.greyed ? '<span class="badge ok">MQTT connected</span>' : ''}
          <span class="grow"></span>
          <span class="badge" title="Checked on the device before anything is written. The exact requirement is measured at apply time and is usually smaller.">needs ~${esc(fmtBytes(pt.needs_bytes))} free on /system</span>
        </div>
        <p class="sub" style="white-space:pre-line">${desc}</p>
        ${pt.needs_pubkey ? `<div style="margin-bottom:8px"><label class="mut">Public key</label><input type="text" class="ssh-pubkey" data-dev="${esc(dev.id)}" data-patcher="${esc(pid)}" placeholder="ssh-rsa AAAA... or ecdsa-sha2-..." value="${esc(pt.ssh_pubkey || '')}" aria-label="Public key"></div>` : ''}
        <div>
          <button class="act${pt.greyed ? ' ghost' : ''}" ${pt.greyed || !ip || ip === '?' ? 'disabled' : ''} data-action="apply-patch" data-id="${esc(dev.id)}" data-patcher="${esc(pid)}" data-name="${esc(pt.name)}">Apply</button>
          <button class="ghost act" data-action="remove-patch" data-id="${esc(dev.id)}" data-patcher="${esc(pid)}" data-name="${esc(pt.name)}">Remove</button>
        </div>
        <pre id="plog_${esc(dev.id)}_${esc(pid)}" style="display:none;margin-top:8px;font-size:11px;max-height:200px;overflow:auto"></pre>
      </div>`;
    }
    html += '</div>';
  }
  v.innerHTML = html;
}
onAction('apply-patch', el =>
  applyPatch(Number(el.dataset.id), el.dataset.patcher, el.dataset.name),
);
onAction('remove-patch', el =>
  removePatch(Number(el.dataset.id), el.dataset.patcher, el.dataset.name),
);
async function applyPatch(id, patcher, name) {
  if (
    !confirm(
      'Apply "' +
        name +
        '" to this device?\n\nThis modifies the device firmware. Make sure you understand what it does. Proceed?',
    )
  )
    return;
  const lg = document.getElementById('plog_' + id + '_' + patcher);
  if (lg) {
    lg.style.display = 'block';
    lg.textContent = 'Starting...\n';
  }
  toast('Applying ' + name + '...');
  const r = await api('devices/' + id + '/patcher', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      patcher,
      action: 'apply',
      pubkey:
        (document.querySelector(`.ssh-pubkey[data-patcher="${patcher}"][data-dev="${id}"]`) || {})
          .value || '',
    }),
  });
  if (!r.ok) {
    toast('Error: ' + (r.error || 'failed'));
    if (lg) lg.textContent += 'ERROR: ' + (r.error || 'failed') + '\n';
  }
}
async function removePatch(id, patcher, name) {
  if (!confirm('Remove "' + name + '" from this device?\n\nThe device will reboot.')) return;
  const lg = document.getElementById('plog_' + id + '_' + patcher);
  if (lg) {
    lg.style.display = 'block';
    lg.textContent = 'Removing...\n';
  }
  const r = await api('devices/' + id + '/patcher', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ patcher, action: 'remove' }),
  });
  if (!r.ok) {
    toast('Error: ' + (r.error || 'failed'));
  }
}

export { loadPatchers };
