// Makes EVERY model explodable: a real model often arrives as a few big meshes (or one solid scan). This splits each mesh into
// its physically separate pieces (connected triangle islands - bolts, panels, blades, bones...). A single solid scan with no
// separate pieces is cut into spatial sections (k-means on its surface) so it can still be taken apart, and those are named
// as "sections" - honest about what they are. Nothing is invented: the pieces are the model's own geometry.

/** Splits meshes under root in place. Returns { pieces, method: 'model' | 'islands' | 'sections' }. */
let counter = { piece: 0, section: 0 };

export function splitModel(THREE, root, { want = 6, maxPieces = 120 } = {}) {
  counter = { piece: 0, section: 0 };
  const meshes = [];
  root.traverse((o) => { if (o.isMesh && !o.isSkinnedMesh && !o.isInstancedMesh && !Array.isArray(o.material) && o.geometry?.attributes?.position) meshes.push(o); });
  if (!meshes.length) return { pieces: 0, method: 'model' };
  let method = 'model', pieces = meshes.length;
  if (pieces < want) {
    const budget = Math.max(2, Math.floor(maxPieces / meshes.length));
    let made = 0;
    for (const m of meshes) made += islands(THREE, m, budget);
    if (made > meshes.length) { method = 'islands'; pieces = made; }
  }
  if (pieces < want) {                                   // still basically one solid piece: cut it into sections
    const big = [];
    root.traverse((o) => { if (o.isMesh && !o.isSkinnedMesh && !o.isInstancedMesh && !Array.isArray(o.material)) big.push(o); });
    big.sort((a, b) => tris(b) - tris(a));
    const k = Math.min(16, Math.max(want, 8));
    if (big[0] && tris(big[0]) > 400) { pieces += sections(THREE, big[0], k) - 1; method = 'sections'; }
  }
  return { pieces, method };
}

const tris = (m) => (m.geometry.index ? m.geometry.index.count : m.geometry.attributes.position.count) / 3;

function vertexIndex(geo) {
  const idx = geo.index;
  return idx ? (i) => idx.getX(i) : (i) => i;
}

/** Splits one mesh into its connected islands. Returns how many meshes it became. */
function islands(THREE, mesh, budget) {
  const geo = mesh.geometry, pos = geo.attributes.position, n = tris(mesh);
  if (n < 8 || n > 2_500_000) return 1;
  geo.computeBoundingBox();
  const size = geo.boundingBox.getSize(new THREE.Vector3()).length() || 1, q = 1e5 / size;
  // weld vertices that share a position (meshes are often split at UV seams) then union triangles
  const weld = new Int32Array(pos.count), seen = new Map();
  for (let i = 0; i < pos.count; i++) {
    const key = `${Math.round(pos.getX(i) * q)}_${Math.round(pos.getY(i) * q)}_${Math.round(pos.getZ(i) * q)}`;
    let id = seen.get(key);
    if (id === undefined) { id = seen.size; seen.set(key, id); }
    weld[i] = id;
  }
  const parent = new Int32Array(seen.size); for (let i = 0; i < parent.length; i++) parent[i] = i;
  const find = (x) => { while (parent[x] !== x) { parent[x] = parent[parent[x]]; x = parent[x]; } return x; };
  const vi = vertexIndex(geo);
  for (let t = 0; t < n; t++) {
    const a = find(weld[vi(t * 3)]), b = find(weld[vi(t * 3 + 1)]), c = find(weld[vi(t * 3 + 2)]);
    if (a !== b) parent[a] = b;
    const b2 = find(b);
    if (find(c) !== b2) parent[find(c)] = b2;
  }
  const groups = new Map();
  for (let t = 0; t < n; t++) {
    const r = find(weld[vi(t * 3)]);
    let g = groups.get(r);
    if (!g) { g = []; groups.set(r, g); }
    g.push(t);
  }
  if (groups.size < 2) return 1;
  let list = [...groups.values()].sort((a, b) => b.length - a.length);
  // tiny islands (rivets, specks) join the nearest kept piece so the count stays useful
  const keep = Math.min(budget, list.filter((g) => g.length >= Math.max(4, n * 0.002)).length || 1);
  if (keep < 2) return 1;
  const kept = list.slice(0, keep), rest = list.slice(keep);
  if (rest.length) {
    const centers = kept.map((g) => centroid(pos, vi, g));
    for (const g of rest) {
      const c = centroid(pos, vi, g);
      let best = 0, bd = Infinity;
      centers.forEach((k, i) => { const d = (k[0] - c[0]) ** 2 + (k[1] - c[1]) ** 2 + (k[2] - c[2]) ** 2; if (d < bd) { bd = d; best = i; } });
      for (const t of g) kept[best].push(t);
    }
  }
  replace(THREE, mesh, kept, 'piece');
  return kept.length;
}

function centroid(pos, vi, triList) {
  const c = [0, 0, 0], step = Math.max(1, Math.floor(triList.length / 200));
  let k = 0;
  for (let i = 0; i < triList.length; i += step) { const v = vi(triList[i] * 3); c[0] += pos.getX(v); c[1] += pos.getY(v); c[2] += pos.getZ(v); k++; }
  return c.map((x) => x / k);
}

/** Cuts one solid mesh into k spatial sections (k-means on triangle centres). */
function sections(THREE, mesh, k) {
  const geo = mesh.geometry, pos = geo.attributes.position, n = tris(mesh), vi = vertexIndex(geo);
  const cx = new Float32Array(n), cy = new Float32Array(n), cz = new Float32Array(n);
  for (let t = 0; t < n; t++) {
    const a = vi(t * 3), b = vi(t * 3 + 1), c = vi(t * 3 + 2);
    cx[t] = (pos.getX(a) + pos.getX(b) + pos.getX(c)) / 3; cy[t] = (pos.getY(a) + pos.getY(b) + pos.getY(c)) / 3; cz[t] = (pos.getZ(a) + pos.getZ(b) + pos.getZ(c)) / 3;
  }
  const C = [];
  for (let i = 0; i < k; i++) { const t = Math.floor(((i + 0.5) / k) * n); C.push([cx[t], cy[t], cz[t]]); }
  const asg = new Int32Array(n);
  for (let it = 0; it < 8; it++) {
    const sum = C.map(() => [0, 0, 0, 0]);
    for (let t = 0; t < n; t++) {
      let best = 0, bd = Infinity;
      for (let i = 0; i < k; i++) { const d = (C[i][0] - cx[t]) ** 2 + (C[i][1] - cy[t]) ** 2 + (C[i][2] - cz[t]) ** 2; if (d < bd) { bd = d; best = i; } }
      asg[t] = best; const s = sum[best]; s[0] += cx[t]; s[1] += cy[t]; s[2] += cz[t]; s[3]++;
    }
    sum.forEach((s, i) => { if (s[3]) C[i] = [s[0] / s[3], s[1] / s[3], s[2] / s[3]]; });
  }
  const groups = C.map(() => []);
  for (let t = 0; t < n; t++) groups[asg[t]].push(t);
  const kept = groups.filter((g) => g.length);
  if (kept.length < 2) return 1;
  replace(THREE, mesh, kept, 'section');
  return kept.length;
}

/** Builds one new mesh per triangle group (same material + transform) and swaps them in for the original. */
function replace(THREE, mesh, groupsOfTris, word) {
  const geo = mesh.geometry, vi = vertexIndex(geo), attrs = Object.entries(geo.attributes);
  const base = /^(object|mesh|node|geo|geometry|defaultmaterial|polysurface|g|m)?[\W_]*\d*$/i.test(mesh.name || '') ? '' : (mesh.name || '').replace(/[_]+/g, ' ').trim();
  groupsOfTris.forEach((g, gi) => {
    const out = new THREE.BufferGeometry();
    for (const [name, attr] of attrs) {
      const size = attr.itemSize, arr = new Float32Array(g.length * 3 * size);
      let o = 0;
      for (const t of g) for (let v = 0; v < 3; v++) { const src = vi(t * 3 + v); for (let c = 0; c < size; c++) arr[o++] = attr.getComponent(src, c); }
      out.setAttribute(name, new THREE.BufferAttribute(arr, size, attr.normalized));
    }
    if (!out.attributes.normal) out.computeVertexNormals();
    const m = new THREE.Mesh(out, mesh.material);
    m.name = `${base ? base + ' · ' : ''}${word === 'section' ? 'Section' : 'Piece'} ${++counter[word === 'section' ? 'section' : 'piece']}`;
    m.position.copy(mesh.position); m.quaternion.copy(mesh.quaternion); m.scale.copy(mesh.scale);
    m.userData.split = word;
    mesh.parent.add(m);
  });
  mesh.parent.remove(mesh);
  geo.dispose();
}
