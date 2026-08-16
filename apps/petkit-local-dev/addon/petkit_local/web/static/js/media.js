import { BASE, esc } from './core.js';
import { onAction } from './delegate.js';

// Encode each path SEGMENT: encodeURI() leaves '#' (and '?') alone, and the
// device folder name contains one ("Purobot Max Pro 2 (T5 #10000001)") — the
// browser would treat everything from '#' on as a fragment and request a
// truncated path, so every thumbnail 404'd and silently hid itself.
function mediaPath(rel) {
  return rel.split('/').map(encodeURIComponent).join('/');
}
function mediaUrl(rel) {
  return rel ? BASE + 'api/media/' + mediaPath(rel) : '';
}
function thumbUrl(rel) {
  return rel ? BASE + 'api/media/thumb/' + mediaPath(rel) : '';
}

function _has(m) {
  return (
    m &&
    (m.playback_url ||
      m.highlight_url ||
      m.preview_url ||
      (m.waste && m.waste.length) ||
      (m.health && m.health.length) ||
      m.snapshot_url ||
      m.video_pending)
  );
}

// Thumbnails for one media slot-set. Aspect ratio is preserved (object-fit:
// contain in CSS) — the device's fisheye is square and must not be cropped.
// The primary tile autoplays a muted looping <video>, GIF-like and with NO
// re-encoding (the browser just plays the existing mp4), lazily (only while
// on screen; see observeLazyVideos). Source preference:
//   1. the CLIP (highlight/dynamicVideo) — a complete single file, so it's a
//      real preview, never a fragment;
//   2. else the timelapse (cloudDouble) ONCE it's stitched;
//   3. else a still;
//   4. else, if a recording is still assembling, a "processing" placeholder
//      so the user knows the video is on its way.
// Every path here is DEVICE-DERIVED (folder names come from the device's own
// reported name), so the lists ride along as JSON in a data- attribute: the
// browser hands that back as an inert string and JSON.parse can only ever
// produce data. They are never interpolated into anything executable.
function mediaThumbs(m) {
  if (!m) return '';
  let html = '';
  const autoplay = m.highlight_url || m.preview_url; // clip preferred over timelapse
  // The PLAYER offers Timelapse ⇄ Playback (the short clip is already the
  // autoplaying thumbnail, so it'd be redundant there). The clip is only a
  // last-resort fallback when neither chunked video exists.
  const opts = [];
  if (m.preview_url) opts.push(['Timelapse', m.preview_url]);
  if (m.playback_url) opts.push(['Playback', m.playback_url]);
  if (!opts.length && m.highlight_url) opts.push(['Clip', m.highlight_url]);
  const poster = thumbUrl(m.snapshot_url || m.highlight_url || m.playback_url);
  if (autoplay) {
    const open = opts.length
      ? ` data-action="open-video" data-media="${esc(JSON.stringify(opts))}" data-pending="${m.video_pending ? 1 : 0}"`
      : '';
    html += `<div class="tl-thumb"${open}>
      <video class="lazyvid" muted loop playsinline preload="none" data-src="${esc(mediaUrl(autoplay))}" ${poster ? `poster="${esc(poster)}"` : ''}></video>
      <span class="tl-play">▶</span>${m.video_pending ? '<span class="tl-badge proc">assembling…</span>' : ''}</div>`;
  } else if (m.video_pending) {
    // A recording is expected but its chunks haven't been joined yet. Show the
    // still if we have one (badged), else a clear placeholder — never a raw
    // fragment. The card auto-refreshes when the stitcher finishes (WS 'media').
    html += m.snapshot_url
      ? `<div class="tl-thumb" data-action="open-gallery" data-media="${esc(JSON.stringify([m.snapshot_url]))}" data-title="Snapshot">
           <img class="hide-on-error" src="${esc(thumbUrl(m.snapshot_url))}">
           <span class="tl-cap">▶ video processing…</span></div>`
      : `<div class="tl-thumb tl-ph"><div class="tl-ph-in"><span class="tl-spin"></span>Video processing…</div></div>`;
  } else if (m.snapshot_url) {
    html += `<div class="tl-thumb" data-action="open-gallery" data-media="${esc(JSON.stringify([m.snapshot_url]))}" data-title="Snapshot">
      <img class="hide-on-error" src="${esc(thumbUrl(m.snapshot_url))}"></div>`;
  }
  if (m.waste && m.waste.length) {
    html += `<div class="tl-thumb small" data-action="open-gallery" data-media="${esc(JSON.stringify(m.waste))}" data-title="Check waste">
      <img class="hide-on-error" src="${esc(thumbUrl(m.waste[0]))}"><span class="tl-badge">${esc(m.waste.length)}</span>
      <span class="tl-cap">Check waste</span></div>`;
  }
  if (m.health && m.health.length) {
    html += `<div class="tl-thumb small" data-action="open-gallery" data-media="${esc(JSON.stringify(m.health))}" data-title="Health">
      <img class="hide-on-error" src="${esc(thumbUrl(m.health[0]))}"><span class="tl-badge">${esc(m.health.length)}</span>
      <span class="tl-cap">Health</span></div>`;
  }
  return html;
}
onAction('open-video', el => openVideo(JSON.parse(el.dataset.media), el.dataset.pending === '1'));
onAction('open-gallery', el => openGallery(JSON.parse(el.dataset.media), el.dataset.title));

// Lazily autoplay the timelapse-preview <video> thumbnails: load + play only
// while on screen, pause + unload when scrolled away, so a long day of events
// doesn't fetch every clip at once.
let _vidObserver = null;
function observeLazyVideos() {
  if (!('IntersectionObserver' in window)) {
    // Fallback: just load + play them all.
    document.querySelectorAll('video.lazyvid').forEach(v => {
      if (!v.src) {
        v.src = v.dataset.src;
      }
      v.play().catch(() => {});
    });
    return;
  }
  if (!_vidObserver) {
    _vidObserver = new IntersectionObserver(
      entries => {
        for (const en of entries) {
          const v = en.target;
          if (en.isIntersecting) {
            if (!v.src) {
              v.src = v.dataset.src;
            }
            v.play().catch(() => {});
          } else {
            v.pause();
          }
        }
      },
      { rootMargin: '100px' },
    );
  }
  document.querySelectorAll('video.lazyvid').forEach(v => _vidObserver.observe(v));
}

// State for whatever the modal currently shows. The source-switch and gallery
// buttons carry only an INDEX into this, so no path is ever put in markup.
let _VID = null; // {opts: [[label, relPath], …], pending: bool}
let _GAL = null; // {list: [relPath, …], title, idx}

function closeModal() {
  document.getElementById('mediaModal').classList.add('hidden');
  document.getElementById('modalBody').innerHTML = '';
  _VID = null;
  _GAL = null;
}
onAction('close-modal', () => closeModal());
onAction('modal-backdrop', (el, ev) => {
  if (ev.target === el) closeModal();
});

// opts: array of [label, relPath] — usually [['Timelapse', …], ['Playback', …]].
function openVideo(opts, playbackPending) {
  if (!opts || !opts.length) return;
  _VID = { opts, pending: !!playbackPending };
  renderVideo(opts[0][1]);
  document.getElementById('mediaModal').classList.remove('hidden');
}
function renderVideo(rel) {
  const multi = _VID.opts.length > 1;
  document.getElementById('modalBody').innerHTML =
    (multi
      ? `<div class="row" style="margin-bottom:8px">${_VID.opts
          .map(
            ([label], i) =>
              `<button class="ghost act" data-action="vid-source" data-i="${i}">${esc(label)}</button>`,
          )
          .join('')}</div>`
      : // Playback is chunked and still being assembled — say so rather than
        // implying the shown clip is the whole recording.
        _VID.pending
        ? `<div class="mut" style="margin-bottom:8px;font-size:12px">▶ Full playback still processing…</div>`
        : '') +
    `<video controls autoplay style="width:100%;max-height:70vh" src="${esc(mediaUrl(rel))}"></video>`;
}
onAction('vid-source', el => {
  if (_VID) renderVideo(_VID.opts[Number(el.dataset.i)][1]);
});

function openGallery(list, title) {
  if (!list || !list.length) return;
  _GAL = { list, title: title || '', idx: 0 };
  renderGallery();
  document.getElementById('mediaModal').classList.remove('hidden');
}
// The TRUE "n of m" is shown here, computed from the actual group — the
// filename deliberately carries only a bare index (the total isn't known
// at upload time). See media/layout.build_media_path.
function renderGallery() {
  const g = _GAL;
  document.getElementById('modalBody').innerHTML =
    `<div class="row" style="margin-bottom:8px;align-items:center;gap:10px">
      <b>${esc(g.title)}</b><span class="grow"></span>
      <button class="ghost act" data-action="gal-step" data-n="-1" ${g.list.length < 2 ? 'disabled' : ''}>◂</button>
      <span class="mut" style="font-variant-numeric:tabular-nums">${g.idx + 1} of ${g.list.length}</span>
      <button class="ghost act" data-action="gal-step" data-n="1" ${g.list.length < 2 ? 'disabled' : ''}>▸</button></div>
      <img src="${esc(mediaUrl(g.list[g.idx]))}" style="width:100%;max-height:70vh;object-fit:contain;background:#000;border-radius:10px">`;
}
onAction('gal-step', el => {
  if (!_GAL) return;
  _GAL.idx = (_GAL.idx + Number(el.dataset.n) + _GAL.list.length) % _GAL.list.length;
  renderGallery();
});

export { mediaThumbs, observeLazyVideos, closeModal, _has };
