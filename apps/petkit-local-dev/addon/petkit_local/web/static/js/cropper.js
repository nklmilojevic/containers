import { BASE, esc, toast } from './core.js';
import { onAction, onChange, onInput } from './delegate.js';
import { closeModal } from './media.js';
import { loadPets } from './pets.js';

// The size the NPU is fed. Every mugshot PetKit's own cloud served was
// exactly this, and media/transcode.py::normalize_face_photo returns an upload
// byte-identical when it already is — so cropping to it here means the server
// never re-encodes and the crop the user made is the one the device gets.
const FACE_SIZE = 224;

// What PetKit's own app tells its users. These are the conditions the matcher
// was tuned for; a photo that breaks one costs accuracy on every future visit.
// Every rule here is real and comes from what the NPU actually needs — except
// the last one, which is a passport-photo joke. Nothing detects eyewear; it is
// there because the list had already drifted into sounding like a government
// form by the time it reached "One cat per photo". Leave it, or don't, but
// please do not implement it.
const FACE_RULES = [
  ['ok', 'Front view, both eyes visible, looking at the camera'],
  ['ok', 'The whole head in frame, evenly lit'],
  ['bad', 'No side or three-quarter views'],
  ['bad', 'No night-vision / infrared shots'],
  ['bad', 'No backlighting — the face must not be in shadow'],
  ['bad', 'Nothing covering the face'],
  ['bad', 'One cat per photo'],
  ['bad', 'No glasses, and all facial jewellery must be removed'],
];

// ---- the cropper -----------------------------------------------------------
// A fixed square frame with the photo panning and zooming underneath it, the
// way every avatar cropper works. The alternative — a draggable, resizable box
// over a static photo — needs corner handles, aspect locking and edge clamping,
// and is miserable on a touchscreen.
//
// State lives here rather than in the DOM because the modal is static markup
// (see index.html) while everything that opens it is re-rendered.
//
// The zoom ceiling is a multiple of "cover", not an absolute scale, so it means
// the same thing for a 12MP phone photo and a small crop someone already made.
// 8x lets you pull a face out of a wide shot where the cat is a fifth of the
// frame — the case the old 4x could not reach.
const MAX_ZOOM = 8;
let _CROP = null; // {petId, bitmap, scale, minScale, x, y, dragging, px, py}

function cropEls() {
  return {
    modal: document.getElementById('cropModal'),
    pick: document.getElementById('cropPick'),
    stage: document.getElementById('cropStage'),
    file: document.getElementById('cropFile'),
    canvas: document.getElementById('cropCanvas'),
    preview: document.getElementById('cropPreview'),
    zoom: document.getElementById('cropZoom'),
    rules: document.getElementById('cropRules'),
    title: document.getElementById('cropTitle'),
  };
}

function openCropper(petId, petName) {
  const el = cropEls();
  _CROP = { petId: petId, bitmap: null, scale: 1, minScale: 1, x: 0, y: 0, dragging: false };
  el.title.textContent = petName ? `Add a mugshot of ${petName}` : 'Add a mugshot';
  el.rules.innerHTML = FACE_RULES.map(r => `<li class="${r[0]}">${esc(r[1])}</li>`).join('');
  el.file.value = '';
  el.pick.classList.remove('hidden', 'dragover');
  el.stage.classList.add('hidden');
  el.modal.classList.remove('hidden');
}

function closeCropper() {
  const el = cropEls();
  el.modal.classList.add('hidden');
  // Free the decoded image rather than waiting for GC: a phone photo is tens
  // of megabytes once decoded, and the modal may be reopened many times.
  if (_CROP && _CROP.bitmap && _CROP.bitmap.close) _CROP.bitmap.close();
  _CROP = null;
}

// EXIF orientation is honoured by createImageBitmap, which matters because a
// phone portrait shot is stored landscape plus a rotation tag — decoded naively
// the cat comes out sideways. The <img> fallback covers browsers without the
// option; there the tag is applied by the image decoder anyway.
async function decodeImage(file) {
  if (window.createImageBitmap) {
    try {
      return await createImageBitmap(file, { imageOrientation: 'from-image' });
    } catch (e) {
      /* fall through to the <img> path */
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    await new Promise((res, rej) => {
      img.addEventListener('load', res);
      img.addEventListener('error', () => rej(new Error('could not read that image')));
      img.src = url;
    });
    return img;
  } finally {
    URL.revokeObjectURL(url);
  }
}

async function cropLoad(file) {
  if (!file) return;
  const el = cropEls();
  let bitmap;
  try {
    bitmap = await decodeImage(file);
  } catch (e) {
    return toast('Could not read that image');
  }
  const size = el.canvas.width;
  const w = bitmap.width || bitmap.naturalWidth;
  const h = bitmap.height || bitmap.naturalHeight;
  // "Cover": the smallest scale at which the photo still fills the frame, so
  // the crop can never contain empty space.
  _CROP.bitmap = bitmap;
  _CROP.minScale = Math.max(size / w, size / h);
  _CROP.scale = _CROP.minScale;
  _CROP.x = (size - w * _CROP.scale) / 2;
  _CROP.y = (size - h * _CROP.scale) / 2;
  el.zoom.value = '100';
  el.pick.classList.add('hidden');
  el.stage.classList.remove('hidden');
  cropDraw();
}

// Keep the photo covering the frame on every pan and zoom. Without this a drag
// exposes the canvas background and the exported JPEG has a blank edge.
function cropClamp() {
  const size = cropEls().canvas.width;
  const w = (_CROP.bitmap.width || _CROP.bitmap.naturalWidth) * _CROP.scale;
  const h = (_CROP.bitmap.height || _CROP.bitmap.naturalHeight) * _CROP.scale;
  _CROP.x = Math.min(0, Math.max(size - w, _CROP.x));
  _CROP.y = Math.min(0, Math.max(size - h, _CROP.y));
}

function cropDraw() {
  if (!_CROP || !_CROP.bitmap) return;
  const el = cropEls();
  cropClamp();
  const w = (_CROP.bitmap.width || _CROP.bitmap.naturalWidth) * _CROP.scale;
  const h = (_CROP.bitmap.height || _CROP.bitmap.naturalHeight) * _CROP.scale;

  const ctx = el.canvas.getContext('2d');
  ctx.clearRect(0, 0, el.canvas.width, el.canvas.height);
  ctx.drawImage(_CROP.bitmap, _CROP.x, _CROP.y, w, h);

  // The preview is the same transform at the size the device will receive, so
  // what it shows is literally the bytes that get uploaded.
  const k = FACE_SIZE / el.canvas.width;
  const pctx = el.preview.getContext('2d');
  pctx.clearRect(0, 0, FACE_SIZE, FACE_SIZE);
  pctx.drawImage(_CROP.bitmap, _CROP.x * k, _CROP.y * k, w * k, h * k);
}

// Zoom about a point (the cursor for a wheel, the centre for the slider) so the
// thing being looked at stays put instead of sliding away.
function cropZoomTo(scale, ax, ay) {
  const el = cropEls();
  const next = Math.max(_CROP.minScale, Math.min(_CROP.minScale * MAX_ZOOM, scale));
  const ratio = next / _CROP.scale;
  _CROP.x = ax - (ax - _CROP.x) * ratio;
  _CROP.y = ay - (ay - _CROP.y) * ratio;
  _CROP.scale = next;
  el.zoom.value = String(Math.round((next / _CROP.minScale) * 100));
  cropDraw();
}

async function cropSave() {
  if (!_CROP || !_CROP.bitmap) return;
  const petId = _CROP.petId;
  const blob = await new Promise(res => cropEls().preview.toBlob(res, 'image/jpeg', 0.92));
  if (!blob) return toast('Could not encode the crop');
  closeCropper();

  let r;
  try {
    const res = await fetch(BASE + 'api/pets/' + encodeURIComponent(petId) + '/faces', {
      method: 'POST',
      body: blob,
    });
    // A 413 answers text/plain, so .json() rejects — surface it rather than
    // letting the promise die silently, which is what once made this button
    // appear to do nothing at all.
    r = await res
      .json()
      .catch(() => ({ error: res.status === 413 ? 'photo too large' : 'HTTP ' + res.status }));
  } catch (e) {
    r = { error: String((e && e.message) || e) };
  }
  toast(r.face ? 'Photo added' : 'Error: ' + (r.error || 'failed'));
  loadPets();
}

// `crop-open` is emitted by the Pets tab's tiles, and it is registered HERE
// rather than beside them: this module already calls `loadPets` on a save, so
// reaching the other way for `openCropper` would close the loop.
onAction('crop-open', el => openCropper(el.dataset.id, el.dataset.name));
onAction('crop-close', () => closeCropper());
onAction('crop-backdrop', (el, ev) => {
  if (ev.target === el) closeCropper();
});
onAction('crop-browse', () => cropEls().file.click());
onAction('crop-back', () => {
  cropEls().stage.classList.add('hidden');
  cropEls().pick.classList.remove('hidden');
});
onAction('crop-reset', () => {
  const size = cropEls().canvas.width;
  cropZoomTo(_CROP.minScale, size / 2, size / 2);
});
onAction('crop-save', () => cropSave());
onChange('crop-file', el => cropLoad(el.files && el.files[0]));
onInput('crop-zoom', el => {
  const size = cropEls().canvas.width;
  cropZoomTo(_CROP.minScale * (Number(el.value) / 100), size / 2, size / 2);
});

// Drag and wheel are bound once, on the document and the canvas respectively —
// not per render. The modal is static markup, so these survive every re-render
// of the tab behind it, and a pointer that leaves the canvas mid-drag is still
// tracked (which is why move/up live on the document).
(function bindCropGestures() {
  const canvas = document.getElementById('cropCanvas');
  if (!canvas) return;
  canvas.addEventListener('pointerdown', ev => {
    if (!_CROP || !_CROP.bitmap) return;
    _CROP.dragging = true;
    _CROP.px = ev.clientX;
    _CROP.py = ev.clientY;
    canvas.setPointerCapture(ev.pointerId);
  });
  document.addEventListener('pointermove', ev => {
    if (!_CROP || !_CROP.dragging) return;
    // The canvas is drawn at its backing-store size but laid out by CSS, so a
    // pointer pixel is not a canvas pixel unless we scale by the ratio.
    const rect = canvas.getBoundingClientRect();
    const k = canvas.width / rect.width;
    _CROP.x += (ev.clientX - _CROP.px) * k;
    _CROP.y += (ev.clientY - _CROP.py) * k;
    _CROP.px = ev.clientX;
    _CROP.py = ev.clientY;
    cropDraw();
  });
  document.addEventListener('pointerup', () => {
    if (_CROP) _CROP.dragging = false;
  });
  canvas.addEventListener(
    'wheel',
    ev => {
      if (!_CROP || !_CROP.bitmap) return;
      ev.preventDefault();
      const rect = canvas.getBoundingClientRect();
      const k = canvas.width / rect.width;
      cropZoomTo(
        _CROP.scale * (ev.deltaY < 0 ? 1.12 : 1 / 1.12),
        (ev.clientX - rect.left) * k,
        (ev.clientY - rect.top) * k,
      );
    },
    { passive: false },
  );
  // Dropping a file straight onto the picker, rather than only via the dialog.
  const pick = document.getElementById('cropPick');
  ['dragenter', 'dragover'].forEach(t =>
    pick.addEventListener(t, ev => {
      ev.preventDefault();
      pick.classList.add('dragover');
    }),
  );
  ['dragleave', 'drop'].forEach(t =>
    pick.addEventListener(t, () => pick.classList.remove('dragover')),
  );
  pick.addEventListener('drop', ev => {
    ev.preventDefault();
    cropLoad(ev.dataTransfer && ev.dataTransfer.files && ev.dataTransfer.files[0]);
  });
})();

// The file's only keyboard handler. Both dialogs get it, rather than leaving
// one closable with Escape and the other not.
document.addEventListener('keydown', ev => {
  if (ev.key !== 'Escape') return;
  if (!document.getElementById('cropModal').classList.contains('hidden')) return closeCropper();
  if (!document.getElementById('mediaModal').classList.contains('hidden')) closeModal();
});
