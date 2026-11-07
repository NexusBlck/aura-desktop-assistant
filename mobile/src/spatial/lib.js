// three.js + its add-ons, loaded from the CDN the first time the spatial view opens (never part of the app pack).
// jsDelivr's "+esm" builds all import the same three.js module URL, so the core and the add-ons share one instance.
const V = '0.170.0';
const CDN = `https://cdn.jsdelivr.net/npm/three@${V}`;
const ADDON = (p) => `${CDN}/examples/jsm/${p}/+esm`;
export const DRACO_PATH = `${CDN}/examples/jsm/libs/draco/gltf/`;

let core = null;
const extras = {};

/** three.js core + what every scene needs. */
export function loadThree() {
  if (core) return core;
  const urls = [`${CDN}/+esm`, ADDON('controls/OrbitControls.js'), ADDON('environments/RoomEnvironment.js'), ADDON('renderers/CSS2DRenderer.js')];
  core = Promise.all(urls.map((u) => import(u))).then(([THREE, oc, env, css2d]) => ({
    THREE, OrbitControls: oc.OrbitControls, RoomEnvironment: env.RoomEnvironment, CSS2DRenderer: css2d.CSS2DRenderer, CSS2DObject: css2d.CSS2DObject,
  }));
  core.catch(() => { core = null; });
  return core;
}

const EXTRA = {
  gltf: ['loaders/GLTFLoader.js', 'GLTFLoader'], draco: ['loaders/DRACOLoader.js', 'DRACOLoader'], meshopt: ['libs/meshopt_decoder.module.js', 'MeshoptDecoder'],
  obj: ['loaders/OBJLoader.js', 'OBJLoader'], mtl: ['loaders/MTLLoader.js', 'MTLLoader'], stl: ['loaders/STLLoader.js', 'STLLoader'],
  ply: ['loaders/PLYLoader.js', 'PLYLoader'], fbx: ['loaders/FBXLoader.js', 'FBXLoader'], parametric: ['geometries/ParametricGeometry.js', 'ParametricGeometry'],
};

/** One add-on (a loader / geometry), loaded only when a scene needs it. */
export function extra(name) {
  if (!extras[name]) {
    const [path, sym] = EXTRA[name];
    const url = ADDON(path);
    extras[name] = import(url).then((m) => m[sym]);
    extras[name].catch(() => { delete extras[name]; });
  }
  return extras[name];
}
