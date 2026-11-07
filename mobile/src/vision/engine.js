// AURA Vision engine - runs ON THE PHONE: live object detection (COCO-SSD), smooth tracking, and
// "visual fingerprints" (MobileNet embeddings) to recognise the things you told AURA to remember.
// The libraries are the same open TensorFlow.js ones the PC camera page uses, loaded on first use.
const LIBS = [
  'https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.22.0/dist/tf.min.js',
  'https://cdn.jsdelivr.net/npm/@tensorflow-models/coco-ssd@2.2.3/dist/coco-ssd.min.js',
  'https://cdn.jsdelivr.net/npm/@tensorflow-models/mobilenet@2.1.1/dist/mobilenet.min.js',
];
export const EMBED_MODEL = 'mobilenet_v1_0.50';

let detector = null, embedder = null, loading = null, precise = false, upgrading = null;

function loadScript(src) {
  return new Promise((resolve, reject) => {
    if ([...document.scripts].some((s) => s.src === src)) return resolve();
    const s = document.createElement('script');
    s.src = src; s.async = false; s.crossOrigin = 'anonymous';
    s.onload = resolve; s.onerror = () => reject(new Error('Could not load ' + src.split('/npm/')[1]));
    document.head.append(s);
  });
}

/** Loads everything once. onStage(text, fraction) reports progress. */
export function load(onStage = () => {}) {
  if (loading) return loading;
  loading = (async () => {
    onStage('LOADING NEURAL CORE', 0.1);
    for (const src of LIBS) await loadScript(src);
    const tf = window.tf;
    try { await tf.setBackend('webgl'); } catch (e) { /* falls back to cpu */ }
    await tf.ready();
    onStage('LOADING OBJECT DETECTOR', 0.4);
    detector = await window.cocoSsd.load({ base: 'lite_mobilenet_v2' });
    onStage('LOADING RECOGNITION CORE', 0.75);
    try { embedder = await window.mobilenet.load({ version: 1, alpha: 0.5 }); } catch (e) { embedder = null; console.warn('recognition unavailable', e); }
    onStage('ONLINE', 1);
    return { detector: !!detector, embedder: !!embedder };
  })();
  loading.catch(() => { loading = null; });
  return loading;
}

/** Swaps in the bigger, more accurate detector (COCO-SSD mobilenet_v2) once it's downloaded. The fast
 *  one keeps working meanwhile, so the camera never waits. */
export function upgrade(onDone = () => {}) {
  if (precise || upgrading || !detector) return upgrading;
  upgrading = window.cocoSsd.load({ base: 'mobilenet_v2' }).then((big) => {
    const old = detector; detector = big; precise = true;
    try { old.dispose && old.dispose(); } catch (e) { /* ignore */ }
    onDone(true);
    return true;
  }).catch((e) => { console.warn('precision model failed', e); upgrading = null; onDone(false); return false; });
  return upgrading;
}
export const isPrecise = () => precise;

export const ready = () => !!detector;
export const canRecognise = () => !!embedder;

/** Objects in a video/canvas: [{label, score, box:[x,y,w,h]}] in source pixels. */
export async function detect(source) {
  if (!detector) return [];
  const raw = await detector.detect(source, 25, precise ? 0.4 : 0.45);
  return raw.map((r) => ({ label: r.class, score: r.score, box: r.bbox }));
}

// ---------------------------------------------------------------- fingerprints
const cropCanvas = document.createElement('canvas');
cropCanvas.width = cropCanvas.height = 224;
const cropCtx = cropCanvas.getContext('2d', { willReadFrequently: true });

/** A normalised fingerprint of one region of the source (x, y, w, h in source pixels). */
export function embed(source, [x, y, w, h]) {
  if (!embedder) return null;
  const sw = source.videoWidth || source.width, sh = source.videoHeight || source.height;
  // a little context around the region, squared up
  const side = Math.max(w, h) * 1.12, cx = x + w / 2, cy = y + h / 2;
  const sx = Math.max(0, cx - side / 2), sy = Math.max(0, cy - side / 2);
  const sz = Math.min(side, sw - sx, sh - sy);
  if (sz < 24) return null;
  cropCtx.drawImage(source, sx, sy, sz, sz, 0, 0, 224, 224);
  const tf = window.tf;
  return tf.tidy(() => {
    const t = embedder.infer(cropCanvas, true);
    const v = t.flatten();
    const n = v.norm();
    return Array.from(v.div(n.add(1e-8)).dataSync());
  });
}

/** Best match among remembered items: { item, score } or null. Items carry normalised embeddings. */
export function match(vec, items, threshold = 0.8) {
  if (!vec || !items || !items.length) return null;
  let best = null, second = 0;
  for (const it of items) {
    if (it.model && it.model !== EMBED_MODEL) continue;
    let top = 0;
    for (const e of it.embeddings || []) {
      let dot = 0;
      for (let i = 0; i < vec.length; i++) dot += vec[i] * e[i];
      if (dot > top) top = dot;
    }
    if (!best || top > best.score) { second = best ? best.score : second; best = { item: it, score: top }; }
    else if (top > second) second = top;
  }
  // must be confident AND clearly better than the next thing
  return best && best.score >= threshold && best.score - second > 0.03 ? best : null;
}

// ---------------------------------------------------------------- tracking
/** Keeps stable ids for objects between frames and smooths their boxes. */
export class Tracker {
  constructor() { this.tracks = []; this.next = 1; }
  update(dets, now = performance.now()) {
    const iou = (a, b) => {
      const x1 = Math.max(a[0], b[0]), y1 = Math.max(a[1], b[1]);
      const x2 = Math.min(a[0] + a[2], b[0] + b[2]), y2 = Math.min(a[1] + a[3], b[1] + b[3]);
      const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
      return inter / (a[2] * a[3] + b[2] * b[3] - inter + 1e-6);
    };
    const used = new Set();
    for (const d of dets) {
      let best = null, bestIou = 0.25;
      for (const t of this.tracks) {
        if (used.has(t) || t.label !== d.label) continue;
        const v = iou(t.box, d.box);
        if (v > bestIou) { best = t; bestIou = v; }
      }
      if (best) {
        best.box = best.box.map((v, i) => v + (d.box[i] - v) * 0.55);
        best.score = best.score * 0.6 + d.score * 0.4; best.seen = now; best.hits++;
        best.trail.push([best.box[0] + best.box[2] / 2, best.box[1] + best.box[3] / 2]);
        if (best.trail.length > 14) best.trail.shift();
        used.add(best);
      } else {
        const t = { id: this.next++, label: d.label, score: d.score, box: d.box.slice(), seen: now, born: now, hits: 1, mem: null,
          trail: [[d.box[0] + d.box[2] / 2, d.box[1] + d.box[3] / 2]] };
        this.tracks.push(t); used.add(t);
      }
    }
    this.tracks = this.tracks.filter((t) => now - t.seen < 700);
    // only show things seen in at least 2 frames - steadier and far fewer false alarms
    return this.tracks.filter((t) => t.hits >= 2);
  }
}

// COCO-SSD knows these - used for FIND suggestions
export const CLASSES = ['person', 'bicycle', 'car', 'motorcycle', 'bus', 'truck', 'traffic light', 'stop sign', 'bench', 'bird', 'cat', 'dog',
  'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'sports ball', 'bottle', 'wine glass', 'cup', 'fork', 'knife', 'spoon', 'bowl', 'banana',
  'apple', 'sandwich', 'orange', 'pizza', 'donut', 'cake', 'chair', 'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop',
  'mouse', 'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'sink', 'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear',
  'hair drier', 'toothbrush'];

// ---------------------------------------------------------------- rough distance
// Typical real heights (m). Distance ≈ real height × focal length (px) / height in the image (px).
const HEIGHTS = { person: 1.7, bicycle: 1.05, car: 1.5, motorcycle: 1.15, bus: 3.1, truck: 3.0, chair: 0.9, couch: 0.85, bed: 0.6,
  'dining table': 0.75, tv: 0.6, laptop: 0.24, 'cell phone': 0.15, cup: 0.1, bottle: 0.24, book: 0.23, dog: 0.55, cat: 0.28,
  'potted plant': 0.5, refrigerator: 1.75, toilet: 0.75, oven: 0.9, microwave: 0.3, clock: 0.3, vase: 0.3, backpack: 0.48,
  suitcase: 0.65, 'traffic light': 1.0, 'stop sign': 0.75, bench: 0.85, umbrella: 0.9, 'teddy bear': 0.3, keyboard: 0.03 };
/** Metres, or null. Assumes an iPhone main camera (~63° across the long side of the frame). */
export function distance(label, box, frameW, frameH) {
  const real = HEIGHTS[label];
  if (!real || label === 'keyboard' || box[3] < 12) return null;
  const f = (Math.max(frameW, frameH) / 2) / Math.tan((63 / 2) * Math.PI / 180);
  const d = (real * f) / box[3];
  return d > 0.2 && d < 60 ? d : null;
}

/** How different two tiny grey thumbnails are (0..1) - used to notice the scene changing. */
const tiny = document.createElement('canvas'); tiny.width = 32; tiny.height = 24;
const tinyCtx = tiny.getContext('2d', { willReadFrequently: true });
export function sceneSignature(source) {
  try {
    tinyCtx.drawImage(source, 0, 0, 32, 24);
    const d = tinyCtx.getImageData(0, 0, 32, 24).data, out = new Uint8Array(768);
    for (let i = 0; i < 768; i++) out[i] = (d[i * 4] + d[i * 4 + 1] + d[i * 4 + 2]) / 3;
    return out;
  } catch (e) { return null; }
}
export function sceneDiff(a, b) {
  if (!a || !b) return 1;
  let s = 0; for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / (a.length * 255);
}
