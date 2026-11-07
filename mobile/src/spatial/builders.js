// Turns a scene from the PC (spatial.py) into three.js objects: real model files (glTF/GLB, OBJ, STL, PLY, FBX), AI-built
// structural models (named primitives), molecules (PubChem atoms + bonds), macromolecules (RCSB chains) and reference pictures.
// Every builder returns { root, parts: [{name, group, desc, meshes}], clips, anims, units, kind }.
import { extra, DRACO_PATH } from './lib.js';
import { compile } from './expr.js';
import { splitModel } from './segment.js';

// ---------------------------------------------------------------- materials
const PRESET = {
  metal: { metalness: 0.9, roughness: 0.28 }, plastic: { metalness: 0.05, roughness: 0.42 }, glass: { metalness: 0, roughness: 0.05, opacity: 0.32, clearcoat: 1 },
  rubber: { metalness: 0, roughness: 0.92 }, fabric: { metalness: 0, roughness: 1, sheen: 1, sheenRoughness: 0.8 }, tissue: { metalness: 0, roughness: 0.5, clearcoat: 0.35, clearcoatRoughness: 0.4 },
  bone: { metalness: 0, roughness: 0.72 }, wood: { metalness: 0, roughness: 0.8 }, stone: { metalness: 0, roughness: 0.95 }, organic: { metalness: 0, roughness: 0.6, clearcoat: 0.2 },
  emissive: { metalness: 0, roughness: 0.4, glow: 0.9 }, ceramic: { metalness: 0, roughness: 0.25, clearcoat: 0.6 },
};

function material(THREE, color, kind = 'plastic', opacity = 1) {
  const p = PRESET[kind] || PRESET.plastic;
  const m = new THREE.MeshPhysicalMaterial({ color: new THREE.Color(color), metalness: p.metalness, roughness: p.roughness,
    clearcoat: p.clearcoat || 0, clearcoatRoughness: p.clearcoatRoughness ?? 0.3, sheen: p.sheen || 0, sheenRoughness: p.sheenRoughness || 0.5,
    sheenColor: new THREE.Color(color), side: THREE.DoubleSide });
  const op = Math.min(opacity, p.opacity ?? 1);
  if (op < 1) { m.transparent = true; m.opacity = op; m.depthWrite = false; }
  if (p.glow) { m.emissive = new THREE.Color(color); m.emissiveIntensity = p.glow; }
  return m;
}

// ---------------------------------------------------------------- AI-built structural models
function geometry(L, part) {
  const { THREE } = L, d = part.dims || {}, n = (k, def) => (Number.isFinite(+d[k]) && +d[k] > 0 ? +d[k] : def);
  switch (part.shape) {
    case 'box': return new THREE.BoxGeometry(n('w', 1), n('h', 1), n('d', 1));
    case 'sphere': return new THREE.SphereGeometry(n('r', 1), 48, 32);
    case 'ellipsoid': { const g = new THREE.SphereGeometry(1, 48, 32); g.scale(n('rx', 1), n('ry', 1), n('rz', 1)); return g; }
    case 'cylinder': return new THREE.CylinderGeometry(n('rt', n('r', 1)), n('rb', n('r', 1)), n('h', 1), 48, 1, false);
    case 'cone': return new THREE.ConeGeometry(n('r', 1), n('h', 1), 48);
    case 'torus': return new THREE.TorusGeometry(n('R', 1), n('r', 0.2), 24, 96, (Math.min(360, n('arc', 360)) * Math.PI) / 180);
    case 'capsule': return new THREE.CapsuleGeometry(n('r', 0.5), n('h', 1), 10, 24);
    case 'lathe': {
      const pts = (d.points || []).filter((p) => p.length >= 2).map((p) => new THREE.Vector2(Math.max(0, p[0]), p[1]));
      if (pts.length < 2) return new THREE.SphereGeometry(1, 24, 16);
      return new THREE.LatheGeometry(pts, 64);
    }
    case 'tube': {
      let pts = (d.points || []).filter((p) => p.length >= 3).map((p) => new THREE.Vector3(p[0], p[1], p[2]));
      if (pts.length < 2) return new THREE.SphereGeometry(n('r', 0.3), 16, 12);
      if (pts.length === 2) pts = [pts[0], pts[0].clone().lerp(pts[1], 0.5), pts[1]];
      return new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.min(400, pts.length * 16), n('r', 0.3), 16, false);
    }
    case 'extrude': {
      const pts = (d.shape || []).filter((p) => p.length >= 2);
      if (pts.length < 3) return new THREE.BoxGeometry(1, 1, 1);
      const shape = new THREE.Shape(pts.map((p) => new THREE.Vector2(p[0], p[1])));
      const depth = n('depth', 1);
      const g = new THREE.ExtrudeGeometry(shape, { depth, bevelEnabled: true, bevelThickness: depth * 0.04, bevelSize: depth * 0.04, bevelSegments: 2, curveSegments: 24 });
      g.translate(0, 0, -depth / 2);
      return g;
    }
    case 'parametric': return null;          // built async (needs the add-on), see below
    default: return new THREE.BoxGeometry(1, 1, 1);
  }
}

async function parametric(L, part) {
  const ParametricGeometry = await extra('parametric');
  const d = part.dims || {};
  const fx = compile(d.x || 'u'), fy = compile(d.y || 'v'), fz = compile(d.z || '0');
  const [u0, u1] = d.u && d.u.length === 2 ? d.u : [0, Math.PI * 2], [v0, v1] = d.v && d.v.length === 2 ? d.v : [0, Math.PI * 2];
  return new ParametricGeometry((a, b, target) => {
    const u = u0 + a * (u1 - u0), v = v0 + b * (v1 - v0);
    target.set(fx(u, v, 0), fy(u, v, 0), fz(u, v, 0));
  }, 120, 120);
}

export async function buildProcedural(L, spec) {
  const { THREE } = L, root = new THREE.Group(), parts = [];
  const D = Math.PI / 180;
  for (const p of spec.parts || []) {
    let geo = geometry(L, p);
    if (!geo && p.shape === 'parametric') {
      try { geo = await parametric(L, p); } catch (e) { geo = new THREE.TorusKnotGeometry(1, 0.3, 160, 24); }
    }
    const mat = material(THREE, p.color, p.material, p.opacity ?? 1);
    const place = (name, pos, rot) => {
      const mesh = new THREE.Mesh(geo, mat.clone());
      mesh.name = name;
      mesh.position.set(...(pos || [0, 0, 0]));
      mesh.rotation.set((rot?.[0] || 0) * D, (rot?.[1] || 0) * D, (rot?.[2] || 0) * D);
      root.add(mesh);
      parts.push({ name, group: p.group, desc: p.desc, meshes: [mesh], shape: p.shape });
    };
    place(p.name, p.pos, p.rot);
    for (const c of p.copies || []) place(c.name, c.pos, c.rot || p.rot);
  }
  return { root, parts, clips: [], anims: spec.animations || [], units: 'cm', kind: 'procedural' };
}

// ---------------------------------------------------------------- molecules (PubChem)
const CPK = { H: '#f2f2f2', C: '#3b3b3b', N: '#3050f8', O: '#ff0d0d', F: '#90e050', Cl: '#1ff01f', Br: '#a62929', I: '#940094', S: '#ffff30', P: '#ff8000',
  Na: '#ab5cf2', K: '#8f40d4', Ca: '#3dff00', Mg: '#8aff00', Fe: '#e06633', Zn: '#7d80b0', Cu: '#c88033', B: '#ffb5b5', Si: '#f0c8a0', Se: '#ffa100' };
const ELEMENT = { H: 'Hydrogen', C: 'Carbon', N: 'Nitrogen', O: 'Oxygen', F: 'Fluorine', Cl: 'Chlorine', Br: 'Bromine', I: 'Iodine', S: 'Sulfur', P: 'Phosphorus',
  Fe: 'Iron', Zn: 'Zinc', Mg: 'Magnesium', Ca: 'Calcium', Na: 'Sodium', K: 'Potassium', Cu: 'Copper', B: 'Boron', Si: 'Silicon', Se: 'Selenium' };
const RADIUS = { H: 0.25, C: 0.38, N: 0.36, O: 0.35, S: 0.48, P: 0.47 };

function atomsMesh(THREE, list, radius, color) {
  const geo = new THREE.SphereGeometry(1, 28, 20);
  const mesh = new THREE.InstancedMesh(geo, material(THREE, color, 'ceramic'), list.length);
  const m = new THREE.Matrix4();
  list.forEach((a, i) => { m.makeScale(radius, radius, radius); m.setPosition(a[1], a[2], a[3]); mesh.setMatrixAt(i, m); });
  mesh.instanceMatrix.needsUpdate = true;
  return mesh;
}

function bondsMesh(THREE, atoms, bonds, radius) {
  const segs = [];
  const up = new THREE.Vector3(0, 1, 0);
  for (const [i, j, order] of bonds) {
    const a = atoms[i], b = atoms[j];
    if (!a || !b) continue;
    const pa = new THREE.Vector3(a[1], a[2], a[3]), pb = new THREE.Vector3(b[1], b[2], b[3]);
    const dir = pb.clone().sub(pa);
    let side = dir.clone().cross(Math.abs(dir.clone().normalize().dot(up)) > 0.9 ? new THREE.Vector3(1, 0, 0) : up).normalize().multiplyScalar(0.12);
    const k = Math.max(1, Math.min(3, order || 1));
    for (let s = 0; s < k; s++) {
      const off = side.clone().multiplyScalar(s - (k - 1) / 2);
      segs.push([pa.clone().add(off), pb.clone().add(off), k > 1 ? radius * 0.6 : radius]);
    }
  }
  const geo = new THREE.CylinderGeometry(1, 1, 1, 12);
  const mesh = new THREE.InstancedMesh(geo, material(THREE, '#b8c7d1', 'ceramic'), Math.max(1, segs.length));
  const m = new THREE.Matrix4(), q = new THREE.Quaternion();
  segs.forEach(([a, b, r], i) => {
    const dir = b.clone().sub(a), len = dir.length();
    q.setFromUnitVectors(up, dir.normalize());
    m.compose(a.clone().add(b).multiplyScalar(0.5), q, new THREE.Vector3(r, len, r));
    mesh.setMatrixAt(i, m);
  });
  mesh.count = segs.length;
  mesh.instanceMatrix.needsUpdate = true;
  return mesh;
}

export function buildMolecule(L, spec) {
  const { THREE } = L, root = new THREE.Group(), parts = [];
  const byEl = {};
  spec.atoms.forEach((a) => { (byEl[a[0]] = byEl[a[0]] || []).push(a); });
  for (const [el, list] of Object.entries(byEl)) {
    const mesh = atomsMesh(THREE, list, RADIUS[el] || 0.42, CPK[el] || '#ff1493');
    mesh.name = `${ELEMENT[el] || el} atoms`;
    root.add(mesh);
    parts.push({ name: `${ELEMENT[el] || el} atoms (${list.length})`, group: 'Atoms', desc: `${list.length} ${ELEMENT[el] || el} atom${list.length > 1 ? 's' : ''} (${el})`, meshes: [mesh] });
  }
  if (spec.bonds?.length) {
    const b = bondsMesh(THREE, spec.atoms, spec.bonds, 0.11);
    b.name = 'Bonds';
    root.add(b);
    parts.push({ name: `Bonds (${spec.bonds.length})`, group: 'Bonds', desc: 'Covalent bonds (double / triple bonds drawn as parallel sticks)', meshes: [b] });
  }
  return { root, parts, clips: [], anims: [], units: 'Å', kind: 'molecule' };
}

/** Turns a structure so its longest dimension stands upright (a DNA helix along its axis, a long protein vertical). */
function stand(THREE, root, pts) {
  if (pts.length < 3) return;
  const c = [0, 0, 0];
  pts.forEach((p) => { c[0] += p[0]; c[1] += p[1]; c[2] += p[2]; });
  c.forEach((_, i) => { c[i] /= pts.length; });
  const m = [[0, 0, 0], [0, 0, 0], [0, 0, 0]];
  for (const p of pts) { const d = [p[0] - c[0], p[1] - c[1], p[2] - c[2]]; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) m[i][j] += d[i] * d[j]; }
  let v = [1, 0.5, 0.25];
  for (let k = 0; k < 40; k++) {                        // power iteration: the main axis of the points
    const w = [0, 1, 2].map((i) => m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2]);
    const n = Math.hypot(...w) || 1; v = w.map((x) => x / n);
  }
  root.quaternion.setFromUnitVectors(new THREE.Vector3(...v), new THREE.Vector3(0, 1, 0));
}

// ---------------------------------------------------------------- proteins / DNA / RNA (RCSB PDB)
const CHAIN_COLORS = ['#3ad6ff', '#a78bfa', '#ffb547', '#4ef0a3', '#ff5a6a', '#60a5fa', '#f472b6', '#facc15', '#34d399', '#c084fc'];

export function buildMacromolecule(L, spec) {
  const { THREE } = L, root = new THREE.Group(), parts = [];
  const baseRungs = {};
  spec.chains.forEach((c, i) => {
    const color = CHAIN_COLORS[i % CHAIN_COLORS.length];
    const pts = c.trace.map((p) => new THREE.Vector3(p[0], p[1], p[2]));
    const nucleic = c.kind !== 'protein';
    const tube = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), Math.min(4000, pts.length * 6), nucleic ? 0.9 : 0.6, 10, false),
      material(THREE, color, 'ceramic'));
    const meshes = [tube];
    if (c.bases?.length) {                              // DNA / RNA: every base as a rung, coloured by type (parts per base type below)
      for (const b of c.bases) (baseRungs[b[6]] = baseRungs[b[6]] || []).push(b);
    } else if (c.atoms?.length) {                              // small structures: every atom as a fine sphere cloud around the backbone
      const m = atomsMesh(THREE, c.atoms, nucleic ? 0.42 : 0.3, color);
      m.material.opacity = 0.55; m.material.transparent = true; m.material.depthWrite = false;
      root.add(m); meshes.push(m);
    }
    root.add(tube);
    const label = { protein: 'protein', dna: 'DNA strand', rna: 'RNA strand' }[c.kind] || c.kind;
    parts.push({ name: `Chain ${c.id} (${label})`, group: nucleic ? 'Nucleic acid' : 'Protein chains', desc: `${label}, ${c.trace.length} ${nucleic ? 'nucleotides' : 'residues'} (backbone trace)`, meshes });
  });
  const BASE = { A: ['Adenine (A)', '#ff5a6a'], T: ['Thymine (T)', '#60a5fa'], G: ['Guanine (G)', '#4ef0a3'], C: ['Cytosine (C)', '#ffb547'], U: ['Uracil (U)', '#a78bfa'], I: ['Inosine (I)', '#cccccc'] };
  const up = new THREE.Vector3(0, 1, 0), m4 = new THREE.Matrix4(), q = new THREE.Quaternion();
  for (const [k, list] of Object.entries(baseRungs)) {
    const [label, color] = BASE[k] || [`Base ${k}`, '#cccccc'];
    const mesh = new THREE.InstancedMesh(new THREE.CylinderGeometry(0.55, 0.55, 1, 10), material(THREE, color, 'ceramic'), list.length);
    list.forEach((b, i) => {
      const a = new THREE.Vector3(b[0], b[1], b[2]), m = new THREE.Vector3(b[3], b[4], b[5]);
      const end = a.clone().lerp(m, 1.35), dir = end.clone().sub(a), len = dir.length();
      q.setFromUnitVectors(up, dir.normalize());
      m4.compose(a.clone().add(end).multiplyScalar(0.5), q, new THREE.Vector3(1, len, 1));
      mesh.setMatrixAt(i, m4);
    });
    mesh.instanceMatrix.needsUpdate = true;
    root.add(mesh);
    parts.push({ name: `${label} bases (${list.length})`, group: 'Bases', desc: `${list.length} ${label.split(' ')[0].toLowerCase()} nucleotide bases (sugar to base centre)`, meshes: [mesh] });
  }
  for (const lig of spec.ligands || []) {
    if (!lig.atoms.length) continue;
    const m = atomsMesh(THREE, lig.atoms, 0.45, '#ffe066');
    root.add(m);
    parts.push({ name: `Ligand ${lig.id}`, group: 'Ligands', desc: `Bound molecule ${lig.name}`, meshes: [m] });
  }
  stand(THREE, root, spec.chains.flatMap((c) => c.trace));
  return { root, parts, clips: [], anims: [], units: 'Å', kind: 'macromolecule' };
}

// ---------------------------------------------------------------- real model files
function loadWith(loader, url, onProgress) {
  return new Promise((resolve, reject) => loader.load(url, resolve, (e) => e.total && onProgress?.(e.loaded / e.total), reject));
}

export async function buildModel(L, scene, onProgress) {
  const { THREE } = L, url = scene.url, fmt = scene.format;
  let root, clips = [];
  if (fmt === 'glb' || fmt === 'gltf') {
    const [GLTFLoader, DRACOLoader, MeshoptDecoder] = await Promise.all([extra('gltf'), extra('draco'), extra('meshopt').catch(() => null)]);
    const loader = new GLTFLoader();
    const draco = new DRACOLoader(); draco.setDecoderPath(DRACO_PATH); loader.setDRACOLoader(draco);
    if (MeshoptDecoder) loader.setMeshoptDecoder(MeshoptDecoder);
    const g = await loadWith(loader, url, onProgress);
    draco.dispose();
    root = g.scene || g.scenes?.[0]; clips = g.animations || [];
  } else if (fmt === 'stl' || fmt === 'ply') {
    const Loader = await extra(fmt);
    const geo = await loadWith(new Loader(), url, onProgress);
    if (!geo.attributes.normal) geo.computeVertexNormals();
    const hasColor = !!geo.attributes.color;
    root = new THREE.Group();
    root.add(new THREE.Mesh(geo, new THREE.MeshPhysicalMaterial({ color: hasColor ? 0xffffff : 0xb9c7d2, vertexColors: hasColor, metalness: 0.1, roughness: 0.55, side: THREE.DoubleSide })));
  } else if (fmt === 'obj') {
    const OBJLoader = await extra('obj');
    root = await loadWith(new OBJLoader(), url, onProgress);
  } else if (fmt === 'fbx') {
    const FBXLoader = await extra('fbx');
    root = await loadWith(new FBXLoader(), url, onProgress);
    clips = root.animations || [];
  } else {
    throw new Error(`Can't open ${fmt} files.`);
  }
  // every model can be taken apart: few named parts -> split into its real separate pieces (or sections of a solid scan)
  let parts = partsOf(THREE, root), split = null;
  if (parts.length < 12) { split = splitModel(THREE, root, { want: 12 }); parts = partsOf(THREE, root); }
  return { root, parts, clips, anims: [], units: 'model units', kind: 'model', split };
}

const JUNK = /^(object|mesh|node|group|polysurface|defaultmaterial|material|mat|geo|geometry|sketchfab|sketchfab_model|root|rootnode|scene|gltf|model|obj|default|untitled|cube|sphere|cylinder|plane|primitive|body|part|shape|surface|solid|layer|null|empty|instance|merged|combined|g|m|_)?[\W_]*\d*(\.\d+)?$/i;
// "engineBlockSep001_lambert2" -> "Engine Block Sep" (camelCase split, material / poly-count / number suffixes dropped)
const clean = (s) => {
  let t = String(s || '').replace(/[-_ ]\d+(\.\d+)?k$/i, '').replace(/[_.]+/g, ' ')
    .replace(/\s*(lambert|phong|blinn|standard(surface)?|material|mat|mtl|surface|shader|sg)\s*\d*$/i, '')
    .replace(/([a-z])([A-Z])/g, '$1 $2').replace(/([A-Za-z])(\d)/g, '$1 $2').replace(/\s+\d+$/, '').replace(/\s+/g, ' ').trim();
  return t.replace(/\b[a-z]/g, (c) => c.toUpperCase());
};
const GENERIC = /(game ?object|instance|clone|materialstandar|default|untitled|unnamed|polysurface|object_?\d|mesh_?\d|node_?\d|^group\d*$|^scene|root ?node|sketchfab|\.(fbx|obj|max|blend|3ds)$|^[a-z]{1,2}\d+$|[0-9a-f]{10,}|^[\W\d_]+$)/i;
const meaningful = (s) => !!s && !JUNK.test(String(s).trim()) && !GENERIC.test(String(s).replace(/_+/g, ' ')) && /[a-z]{3,}/i.test(s) && !/^(fbx|gltf|obj)/i.test(s);

/** Groups a real model's meshes into named parts: each mesh belongs to its closest meaningfully named ancestor (or its material name). */
export function partsOf(THREE, root) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh || o.isSkinnedMesh) meshes.push(o); });
  const by = new Map();
  let named = 0;
  for (const m of meshes) {
    if (m.userData.split) {                                // a piece made by segment.js: its own part, its own name
      let key = m.name, k = 2;
      while (by.has(key)) key = `${m.name} (${k++})`;
      m.name = key;
      by.set(key, [m]);
      named++;
      continue;
    }
    let o = m, name = null;
    while (o && o !== root) { if (meaningful(o.name)) { name = clean(o.name); break; } o = o.parent; }
    if (!name) {
      const mat = Array.isArray(m.material) ? m.material[0] : m.material;
      if (mat && meaningful(mat.name)) name = clean(mat.name);
    }
    if (name) named++;
    name = name || 'Main body';
    if (!by.has(name)) by.set(name, []);
    by.get(name).push(m);
  }
  let list = [...by.entries()].map(([name, ms]) => {
    const split = ms[0].userData.split;
    return { name, meshes: ms, group: split ? (name.includes(' · ') ? name.split(' · ')[0] : (split === 'section' ? 'Sections' : 'Pieces')) : '',
      desc: split === 'section' ? 'A section of a solid scan (cut by AURA so it can be taken apart)' : split ? 'A separate piece of this model' : '' };
  });
  // generic names lumped many separate meshes together: every mesh is its own piece (the AI scan can name them later)
  if (list.length < 12 && meshes.length >= 12) {
    let k = 0;
    list = list.flatMap((p) => (p.meshes.length === 1 ? [p] : p.meshes.map((m) => ({ name: p.name === 'Main body' ? `Piece ${++k}` : `${p.name} ${++k}`, meshes: [m], group: p.name === 'Main body' ? 'Pieces' : p.name, desc: 'A separate piece of this model' }))));
  }
  if (list.length > 400) {                               // far too many tiny pieces: keep the 399 biggest, merge the rest
    const box = new THREE.Box3(), size = new THREE.Vector3();
    list.forEach((p) => { box.makeEmpty(); p.meshes.forEach((m) => box.expandByObject(m)); p.vol = box.getSize(size).x * size.y * size.z; });
    list.sort((a, b) => b.vol - a.vol);
    const rest = list.slice(399);
    list = list.slice(0, 399).concat([{ name: 'Other small parts', meshes: rest.flatMap((p) => p.meshes), group: '', desc: '' }]);
  }
  list.named = named;
  return list;
}

// ---------------------------------------------------------------- a reference picture (level 6)
export function buildImage(L, scene) {
  const { THREE } = L;
  return new Promise((resolve, reject) => {
    new THREE.TextureLoader().load(scene.url, (tex) => {
      tex.colorSpace = THREE.SRGBColorSpace;
      const a = tex.image.width / tex.image.height;
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(a >= 1 ? 2 : 2 * a, a >= 1 ? 2 / a : 2), new THREE.MeshBasicMaterial({ map: tex, side: THREE.DoubleSide, toneMapped: false }));
      mesh.name = 'Picture';
      const root = new THREE.Group(); root.add(mesh);
      resolve({ root, parts: [{ name: 'Picture', meshes: [mesh], desc: scene.caption || '' }], clips: [], anims: [], units: '', kind: 'image', flat: true });
    }, undefined, () => reject(new Error("Couldn't load the picture.")));
  });
}
