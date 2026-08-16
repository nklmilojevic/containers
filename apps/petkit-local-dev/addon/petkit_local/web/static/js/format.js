import { esc } from './core.js';

function fmtVal(v) {
  if (v === null || v === undefined) return '<span class="mut">—</span>';
  if (v === true) return 'on';
  if (v === false) return 'off';
  if (typeof v === 'object') return '<code>' + esc(JSON.stringify(v)) + '</code>';
  return esc(v);
}

// A second count as something a human reads. Uptime arrives as `142750 s`,
// which is a number you have to do arithmetic on before it means anything.
// Home Assistant renders `device_class: duration` for you; the panel has to do
// it itself.
function fmtDuration(sec) {
  const n = Number(sec);
  if (!isFinite(n) || n < 0) return null;
  const d = Math.floor(n / 86400),
    h = Math.floor((n % 86400) / 3600),
    m = Math.floor((n % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m`;
  return `${Math.floor(n)}s`;
}

// One entity's value, rendered the way Home Assistant renders it. The panel used
// to print the raw number while HA applied the discovery value_template, so the
// same entity read `-1` here and `idle` there — and the panel is where anyone
// checks whether a sensor is working.
function fmtEntityValue(e) {
  const v = e.value;
  const unit = e.unit ? ` <span class="mut">${esc(e.unit)}</span>` : '';
  if (v === null || v === undefined) return '<span class="mut">—</span>';
  if (e.options && e.options.length && e.component !== 'event') {
    const vals = e.option_values && e.option_values.length ? e.option_values : e.options;
    const i = vals.findIndex(x => String(x) === String(v));
    // An unmapped value stays visible as its raw self, matching
    // `_enum_sensor_value_template`: blanking it would hide a real state.
    if (i >= 0) return esc(e.options[i]);
  }
  if (e.component === 'binary_sensor') return v === 0 || v === '0' || v === false ? 'off' : 'on';
  // These two carry their own units ("1d 15h", a formatted date), so the raw
  // unit must NOT be appended or they read "1d 15h s".
  if (e.device_class === 'duration' && e.unit === 's') {
    const pretty = fmtDuration(v);
    if (pretty) return esc(pretty);
  }
  if (e.device_class === 'timestamp') return esc(fmtDateTime(v));
  return fmtVal(v) + unit;
}

// A timestamp sensor holds an ISO string; printing it raw next to "2 hours ago"
// values is the kind of thing that makes a table hard to scan.
function fmtDateTime(v) {
  const t = typeof v === 'number' ? v * 1000 : Date.parse(v);
  if (!isFinite(t)) return String(v);
  return new Date(t).toLocaleString();
}

export { fmtEntityValue };
