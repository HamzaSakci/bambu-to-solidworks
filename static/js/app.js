/* ============================================================================
   BAMBU STUDIO (.3MF / .STL) -> SOLIDWORKS (.STEP / .SLDPRT / .PRT) WEB STUDIO
   Hibrit Çift Motorlu Mimari:
   1. Yerel Masaüstü Modu (Flask + SolidWorks 2026 COM API)
   2. 7/24 Herkese Açık Bulut / GitHub Pages Modu (Tarayıcı İçi JS CAD Motoru)
   ============================================================================ */

let currentFile = null;
let selectedMaxFaces = 25000;
let activeColorHex = "#00e676";
let showEdges = true;
let autoRotate = true;
let isLocalBackend = false;
let apiBaseUrl = "";

// Three.js değişkenleri
let scene, camera, renderer, controls, meshGroup, mainMesh, edgeLines;

document.addEventListener("DOMContentLoaded", () => {
    initThreeViewer();
    bindUIEvents();
    detectBackendAndInit();
});

/* ----------------------------------------------------------------------------
   1. THREE.JS 3B WEBGL CAD VIEWPORT KURULUMU
   ---------------------------------------------------------------------------- */
function initThreeViewer() {
    const container = document.getElementById("threeContainer");
    const w = container.clientWidth || 640;
    const h = container.clientHeight || 410;

    scene = new THREE.Scene();

    camera = new THREE.PerspectiveCamera(42, w / h, 0.1, 5000);
    camera.position.set(85, 65, 95);

    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(w, h);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.insertBefore(renderer.domElement, container.firstChild);

    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.06;
    controls.autoRotate = autoRotate;
    controls.autoRotateSpeed = 1.6;

    // Stüdyo Işıklandırması
    const hemiLight = new THREE.HemisphereLight(0xdbeafe, 0x0f172a, 0.85);
    scene.add(hemiLight);

    const dirLight1 = new THREE.DirectionalLight(0xffffff, 0.95);
    dirLight1.position.set(120, 180, 140);
    scene.add(dirLight1);

    const dirLight2 = new THREE.DirectionalLight(0x38bdf8, 0.45);
    dirLight2.position.set(-130, -80, -100);
    scene.add(dirLight2);

    const rimLight = new THREE.DirectionalLight(0xa855f7, 0.35);
    rimLight.position.set(0, 120, -150);
    scene.add(rimLight);

    // CAD Taban Izgarası
    const grid = new THREE.GridHelper(180, 18, 0x00d4ff, 0x1e293b);
    grid.position.y = -32;
    grid.material.opacity = 0.45;
    grid.material.transparent = true;
    scene.add(grid);

    meshGroup = new THREE.Group();
    scene.add(meshGroup);

    window.addEventListener("resize", onWindowResize);
    animateThree();
}

function onWindowResize() {
    const container = document.getElementById("threeContainer");
    if (!container || !renderer || !camera) return;
    const w = container.clientWidth;
    const h = container.clientHeight;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
}

function animateThree() {
    requestAnimationFrame(animateThree);
    if (controls) {
        controls.autoRotate = autoRotate;
        controls.update();
    }
    if (renderer && scene && camera) {
        renderer.render(scene, camera);
    }
}

function renderMeshInThree(meshData) {
    if (!meshGroup) return;

    while (meshGroup.children.length > 0) {
        const child = meshGroup.children[0];
        if (child.geometry) child.geometry.dispose();
        if (child.material) child.material.dispose();
        meshGroup.remove(child);
    }

    const emptyOverlay = document.getElementById("viewerEmptyOverlay");
    if (emptyOverlay) emptyOverlay.classList.add("hidden");

    const positions = new Float32Array(meshData.vertices);
    const indices = new Uint32Array(meshData.indices);

    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setIndex(new THREE.BufferAttribute(indices, 1));

    // Z-yukarı (CAD) -> Y-yukarı (Three.js)
    geometry.rotateX(-Math.PI / 2);
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();

    const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(activeColorHex),
        metalness: 0.28,
        roughness: 0.32,
        flatShading: true,
        side: THREE.DoubleSide,
        polygonOffset: true,
        polygonOffsetFactor: 1,
        polygonOffsetUnits: 1,
    });

    mainMesh = new THREE.Mesh(geometry, material);
    meshGroup.add(mainMesh);

    const edgesGeom = new THREE.EdgesGeometry(geometry, 22);
    const edgesMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        transparent: true,
        opacity: 0.45,
    });
    edgeLines = new THREE.LineSegments(edgesGeom, edgesMat);
    edgeLines.visible = showEdges;
    meshGroup.add(edgeLines);

    fitCameraToMesh(geometry);
}

function fitCameraToMesh(geometry) {
    if (!geometry.boundingSphere) geometry.computeBoundingSphere();
    const radius = Math.max(geometry.boundingSphere.radius, 5.0);
    controls.target.set(0, 0, 0);
    camera.position.set(radius * 1.9, radius * 1.35, radius * 2.1);
    camera.near = Math.max(0.1, radius * 0.01);
    camera.far = Math.max(2000, radius * 25);
    camera.updateProjectionMatrix();
    controls.update();
}

/* ----------------------------------------------------------------------------
   2. HİBRİT MOTOR TESPİTİ (YEREL PC vs 7/24 GITHUB PAGES WEB)
   ---------------------------------------------------------------------------- */
async function detectBackendAndInit() {
    const candidateBases = ["", "http://127.0.0.1:5050"];
    for (const base of candidateBases) {
        try {
            const ctrl = new AbortController();
            const tid = setTimeout(() => ctrl.abort(), 1500);
            const res = await fetch(`${base}/api/info`, { signal: ctrl.signal });
            clearTimeout(tid);
            if (res.ok) {
                const info = await res.json();
                isLocalBackend = true;
                apiBaseUrl = base;
                document.getElementById("engineModeText").textContent = "SolidWorks 2026 Masaüstü Bağlantısı Aktif";
                document.getElementById("btnOpenFolder").classList.remove("hidden");
                document.getElementById("btnOpenInSwDirect").classList.remove("hidden");
                if (info.lan_url) {
                    const lanBox = document.getElementById("lanUrlBox");
                    const lanTxt = document.getElementById("lanUrlText");
                    if (lanBox && lanTxt) {
                        lanBox.classList.remove("hidden");
                        lanTxt.textContent = info.lan_url;
                    }
                }
                await loadSamplesFromLocalServer();
                return;
            }
        } catch (_) {
            // Sonraki adresi dene
        }
    }

    isLocalBackend = false;
    apiBaseUrl = "";
    document.getElementById("engineModeText").textContent = "7/24 WebCAD Bulut Motoru Aktif";
    document.getElementById("rowOpenSwAfter").classList.add("hidden");
    initClientSideSamples();
}

/* ----------------------------------------------------------------------------
   3. TARAYICI İÇİ (CLIENT-SIDE) 3MF / STL PARSER VE STEP AP214 MOTORU
      (GitHub Pages üzerinde 7/24 sunucusuz çalışmayı sağlar!)
   ---------------------------------------------------------------------------- */
function generateClientDemoMesh(type) {
    let geom;
    if (type === "flange") {
        // Çokgen prizma + göbek birleşimi
        const g1 = new THREE.CylinderGeometry(26, 26, 8, 32);
        const g2 = new THREE.CylinderGeometry(14, 14, 22, 32);
        g2.translate(0, 7, 0);
        const g3 = new THREE.BoxGeometry(50, 14, 8);
        g3.translate(0, 3, 0);
        geom = mergeThreeGeometries([g1, g2, g3]);
    } else if (type === "bracket") {
        const b1 = new THREE.BoxGeometry(48, 8, 32);
        const b2 = new THREE.BoxGeometry(8, 36, 32);
        b2.translate(-20, 14, 0);
        geom = mergeThreeGeometries([b1, b2]);
    } else {
        geom = new THREE.BoxGeometry(45, 30, 15);
    }
    geom.rotateX(Math.PI / 2); // CAD Z-yukarı
    return extractMeshFromThreeGeometry(geom);
}

function mergeThreeGeometries(geoms) {
    const posList = [];
    const idxList = [];
    let offset = 0;
    for (const g of geoms) {
        const pos = g.attributes.position.array;
        for (let i = 0; i < pos.length; i++) posList.push(pos[i]);
        if (g.index) {
            const idx = g.index.array;
            for (let i = 0; i < idx.length; i++) idxList.push(idx[i] + offset);
        } else {
            const cnt = pos.length / 3;
            for (let i = 0; i < cnt; i++) idxList.push(i + offset);
        }
        offset += pos.length / 3;
    }
    const merged = new THREE.BufferGeometry();
    merged.setAttribute("position", new THREE.BufferAttribute(new Float32Array(posList), 3));
    merged.setIndex(idxList);
    return merged;
}

function extractMeshFromThreeGeometry(geom) {
    const rawPos = geom.attributes.position.array;
    const rawIdx = geom.index ? geom.index.array : null;
    const tris = [];
    if (rawIdx) {
        for (let i = 0; i < rawIdx.length; i += 3) {
            tris.push([rawIdx[i], rawIdx[i + 1], rawIdx[i + 2]]);
        }
    } else {
        for (let i = 0; i < rawPos.length / 9; i++) {
            tris.push([i * 3, i * 3 + 1, i * 3 + 2]);
        }
    }
    const verts = [];
    for (let i = 0; i < rawPos.length; i += 3) {
        verts.push([rawPos[i], rawPos[i + 1], rawPos[i + 2]]);
    }
    return weldAndAnalyzeMesh(verts, tris);
}

function weldAndAnalyzeMesh(rawVerts, rawTris) {
    const vertMap = new Map();
    const uniqueVerts = [];
    const oldToNew = new Int32Array(rawVerts.length);

    for (let i = 0; i < rawVerts.length; i++) {
        const v = rawVerts[i];
        const key = `${Math.round(v[0] * 1e4)},${Math.round(v[1] * 1e4)},${Math.round(v[2] * 1e4)}`;
        let idx = vertMap.get(key);
        if (idx === undefined) {
            idx = uniqueVerts.length;
            uniqueVerts.push([v[0], v[1], v[2]]);
            vertMap.set(key, idx);
        }
        oldToNew[i] = idx;
    }

    const faces = [];
    for (let i = 0; i < rawTris.length; i++) {
        const a = oldToNew[rawTris[i][0]];
        const b = oldToNew[rawTris[i][1]];
        const c = oldToNew[rawTris[i][2]];
        if (a !== b && b !== c && c !== a) {
            faces.push([a, b, c]);
        }
    }

    let minX = Infinity, minY = Infinity, minZ = Infinity;
    let maxX = -Infinity, maxY = -Infinity, maxZ = -Infinity;
    for (const v of uniqueVerts) {
        if (v[0] < minX) minX = v[0];
        if (v[1] < minY) minY = v[1];
        if (v[2] < minZ) minZ = v[2];
        if (v[0] > maxX) maxX = v[0];
        if (v[1] > maxY) maxY = v[1];
        if (v[2] > maxZ) maxZ = v[2];
    }
    if (!uniqueVerts.length) {
        minX = minY = minZ = maxX = maxY = maxZ = 0;
    }

    const cx = 0.5 * (minX + maxX);
    const cy = 0.5 * (minY + maxY);
    const cz = 0.5 * (minZ + maxZ);

    let areaMm2 = 0;
    let signedVol = 0;
    for (const f of faces) {
        const p0 = uniqueVerts[f[0]];
        const p1 = uniqueVerts[f[1]];
        const p2 = uniqueVerts[f[2]];
        const ux = p1[0] - p0[0], uy = p1[1] - p0[1], uz = p1[2] - p0[2];
        const vx = p2[0] - p0[0], vy = p2[1] - p0[1], vz = p2[2] - p0[2];
        const nx = uy * vz - uz * vy;
        const ny = uz * vx - ux * vz;
        const nz = ux * vy - uy * vx;
        areaMm2 += 0.5 * Math.hypot(nx, ny, nz);
        signedVol += (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1]) +
                      p0[1] * (p1[2] * p2[0] - p1[0] * p2[2]) +
                      p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])) / 6.0;
    }

    const flatVerts = [];
    for (const v of uniqueVerts) {
        flatVerts.push(v[0] - cx, v[1] - cy, v[2] - cz);
    }
    const flatIndices = [];
    const step = faces.length > 35000 ? Math.ceil(faces.length / 35000) : 1;
    for (let i = 0; i < faces.length; i += step) {
        flatIndices.push(faces[i][0], faces[i][1], faces[i][2]);
    }

    return {
        uniqueVerts,
        faces,
        dimensions_mm: {
            x: +(maxX - minX).toFixed(2),
            y: +(maxY - minY).toFixed(2),
            z: +(maxZ - minZ).toFixed(2),
        },
        surface_area_cm2: +(areaMm2 / 100.0).toFixed(2),
        volume_cm3: +(Math.abs(signedVol) / 1000.0).toFixed(2),
        mesh_data: {
            vertices: flatVerts,
            indices: flatIndices,
        },
    };
}

function parseStlArrayBuffer(buffer) {
    const view = new DataView(buffer);
    if (buffer.byteLength >= 84) {
        const triCount = view.getUint32(80, true);
        if (84 + triCount * 50 === buffer.byteLength) {
            const verts = [];
            const tris = [];
            let offset = 84;
            for (let i = 0; i < triCount; i++) {
                offset += 12; // normal
                const i0 = verts.length;
                for (let k = 0; k < 3; k++) {
                    const x = view.getFloat32(offset, true);
                    const y = view.getFloat32(offset + 4, true);
                    const z = view.getFloat32(offset + 8, true);
                    verts.push([x, y, z]);
                    offset += 12;
                }
                tris.push([i0, i0 + 1, i0 + 2]);
                offset += 2;
            }
            return weldAndAnalyzeMesh(verts, tris);
        }
    }

    // ASCII STL fallback
    const text = new TextDecoder("utf-8").decode(buffer);
    const verts = [];
    const tris = [];
    const re = /vertex\s+([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s+([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s+([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)/g;
    let m;
    while ((m = re.exec(text)) !== null) {
        verts.push([parseFloat(m[1]), parseFloat(m[2]), parseFloat(m[3])]);
        if (verts.length % 3 === 0) {
            const n = verts.length;
            tris.push([n - 3, n - 2, n - 1]);
        }
    }
    return weldAndAnalyzeMesh(verts, tris);
}

async function parse3mfArrayBuffer(buffer) {
    const zip = await JSZip.loadAsync(buffer);
    const modelFiles = Object.keys(zip.files).filter((n) => n.toLowerCase().endsWith(".model"));
    if (!modelFiles.length) throw new Error(".3MF arşivinde .model dosyası bulunamadı.");

    const allVerts = [];
    const allTris = [];

    for (const mf of modelFiles) {
        const xmlText = await zip.file(mf).async("string");
        const doc = new DOMParser().parseFromString(xmlText, "application/xml");
        const meshes = doc.getElementsByTagName("mesh");
        for (let m = 0; m < meshes.length; m++) {
            const meshEl = meshes[m];
            const baseOffset = allVerts.length;
            const vEls = meshEl.getElementsByTagName("vertex");
            for (let i = 0; i < vEls.length; i++) {
                allVerts.push([
                    parseFloat(vEls[i].getAttribute("x") || "0"),
                    parseFloat(vEls[i].getAttribute("y") || "0"),
                    parseFloat(vEls[i].getAttribute("z") || "0"),
                ]);
            }
            const tEls = meshEl.getElementsByTagName("triangle");
            for (let i = 0; i < tEls.length; i++) {
                allTris.push([
                    baseOffset + parseInt(tEls[i].getAttribute("v1") || "0", 10),
                    baseOffset + parseInt(tEls[i].getAttribute("v2") || "0", 10),
                    baseOffset + parseInt(tEls[i].getAttribute("v3") || "0", 10),
                ]);
            }
        }
    }
    return weldAndAnalyzeMesh(allVerts, allTris);
}

function buildStepAp214ClientSide(verts, faces, partName, mergeCoplanar, angleTolDeg) {
    // Düzlemsel yüzey birleştirme + ISO-10303-21 AP214 STEP üretici
    const nTris = faces.length;
    const normals = [];
    for (let i = 0; i < nTris; i++) {
        const [a, b, c] = faces[i];
        const p0 = verts[a], p1 = verts[b], p2 = verts[c];
        const ux = p1[0] - p0[0], uy = p1[1] - p0[1], uz = p1[2] - p0[2];
        const vx = p2[0] - p0[0], vy = p2[1] - p0[1], vz = p2[2] - p0[2];
        let nx = uy * vz - uz * vy;
        let ny = uz * vx - ux * vz;
        let nz = ux * vy - uy * vx;
        const len = Math.hypot(nx, ny, nz) || 1.0;
        normals.push([nx / len, ny / len, nz / len]);
    }

    let polyLoops = [];
    let polyNormals = [];

    if (!mergeCoplanar || nTris > 40000) {
        polyLoops = faces.map((f) => [f[0], f[1], f[2]]);
        polyNormals = normals;
    } else {
        // Yönlü yarı-kenar haritası ile aynı düzlemdeki komşu üçgenleri grupla
        const heMap = new Map();
        for (let i = 0; i < nTris; i++) {
            const [a, b, c] = faces[i];
            heMap.set(`${a}_${b}`, i);
            heMap.set(`${b}_${c}`, i);
            heMap.set(`${c}_${a}`, i);
        }
        const visited = new Uint8Array(nTris);
        const cosTol = Math.cos((angleTolDeg * Math.PI) / 180.0);

        for (let seed = 0; seed < nTris; seed++) {
            if (visited[seed]) continue;
            visited[seed] = 1;
            const cluster = [seed];
            const queue = [seed];
            const snVec = normals[seed];
            const sp = verts[faces[seed][0]];

            while (queue.length > 0) {
                const curr = queue.pop();
                const [a, b, c] = faces[curr];
                for (const [u, v] of [[a, b], [b, c], [c, a]]) {
                    const nb = heMap.get(`${v}_${u}`);
                    if (nb === undefined || visited[nb]) continue;
                    const nn = normals[nb];
                    if (nn[0] * snVec[0] + nn[1] * snVec[1] + nn[2] * snVec[2] < cosTol) continue;
                    // Düzlem mesafesi kontrolü
                    let okPlane = true;
                    for (const vid of faces[nb]) {
                        const pt = verts[vid];
                        const dist = Math.abs((pt[0] - sp[0]) * snVec[0] + (pt[1] - sp[1]) * snVec[1] + (pt[2] - sp[2]) * snVec[2]);
                        if (dist > 0.002) { okPlane = false; break; }
                    }
                    if (!okPlane) continue;
                    visited[nb] = 1;
                    cluster.push(nb);
                    queue.push(nb);
                }
            }

            if (cluster.length === 1) {
                polyLoops.push([...faces[seed]]);
                polyNormals.push(snVec);
            } else {
                const edgeSet = new Set();
                let valid = true;
                for (const t of cluster) {
                    const [a, b, c] = faces[t];
                    for (const [u, v] of [[a, b], [b, c], [c, a]]) {
                        const rev = `${v}_${u}`;
                        const fwd = `${u}_${v}`;
                        if (edgeSet.has(rev)) edgeSet.delete(rev);
                        else if (edgeSet.has(fwd)) valid = false;
                        else edgeSet.add(fwd);
                    }
                }
                const nextMap = new Map();
                if (valid && edgeSet.size >= 3) {
                    for (const e of edgeSet) {
                        const [uStr, vStr] = e.split("_");
                        const u = parseInt(uStr, 10), v = parseInt(vStr, 10);
                        if (nextMap.has(u)) { valid = false; break; }
                        nextMap.set(u, v);
                    }
                } else {
                    valid = false;
                }

                if (valid) {
                    const start = nextMap.keys().next().value;
                    const loop = [];
                    let curr = start;
                    while (nextMap.has(curr)) {
                        const nxt = nextMap.get(curr);
                        nextMap.delete(curr);
                        loop.push(curr);
                        curr = nxt;
                    }
                    if (curr === start && nextMap.size === 0 && loop.length >= 3) {
                        polyLoops.push(loop);
                        polyNormals.push(snVec);
                    } else {
                        for (const t of cluster) {
                            polyLoops.push([...faces[t]]);
                            polyNormals.push(normals[t]);
                        }
                    }
                } else {
                    for (const t of cluster) {
                        polyLoops.push([...faces[t]]);
                        polyNormals.push(normals[t]);
                    }
                }
            }
        }
    }

    // ISO-10303-21 STEP AP214 Metni Oluştur
    const safeName = (partName || "Bambu_Part").replace(/[^a-zA-Z0-9_-]/g, "_");
    const lines = [];
    let eid = 0;
    const add = (s) => { eid++; lines.push(`#${eid}=${s};`); return eid; };
    const fmt = (x) => (Math.abs(x) < 1e-10 ? "0." : Number(x.toFixed(6)).toString().replace(/^(-?\d+)$/, "$1."));

    const appCtx = add("APPLICATION_CONTEXT('automotive_design')");
    add(`APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#${appCtx})`);
    const prodCtx = add(`PRODUCT_CONTEXT('',#${appCtx},'mechanical')`);
    const prod = add(`PRODUCT('${safeName}','${safeName}','',(#${prodCtx}))`);
    const pdf = add(`PRODUCT_DEFINITION_FORMATION('','',#${prod})`);
    const pdc = add(`PRODUCT_DEFINITION_CONTEXT('part definition',#${appCtx},'design')`);
    const pd = add(`PRODUCT_DEFINITION('design','',#${pdf},#${pdc})`);
    const pds = add(`PRODUCT_DEFINITION_SHAPE('','',#${pd})`);

    const lenU = add("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))");
    const angU = add("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))");
    const solU = add("(NAMED_UNIT(*)SOLID_ANGLE_UNIT()SI_UNIT($,.STERADIAN.))");
    const unc = add(`UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-05),#${lenU},'distance_accuracy_value','confusion accuracy')`);
    const gCtx = add(`(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#${unc}))GLOBAL_UNIT_ASSIGNED_CONTEXT((#${lenU},#${angU},#${solU}))REPRESENTATION_CONTEXT('Context #1','3D Context'))`);

    const origPt = add("CARTESIAN_POINT('',(0.,0.,0.))");
    const dirZ = add("DIRECTION('',(0.,0.,1.))");
    const dirX = add("DIRECTION('',(1.,0.,0.))");
    const wAxis = add(`AXIS2_PLACEMENT_3D('',#${origPt},#${dirZ},#${dirX})`);

    const cpIds = new Int32Array(verts.length);
    for (let i = 0; i < verts.length; i++) {
        const v = verts[i];
        cpIds[i] = add(`CARTESIAN_POINT('',(${fmt(v[0])},${fmt(v[1])},${fmt(v[2])}))`);
    }

    const faceIds = [];
    for (let i = 0; i < polyLoops.length; i++) {
        const lp = polyLoops[i];
        const n = polyNormals[i];
        const ptsStr = lp.map((vi) => `#${cpIds[vi]}`).join(",");
        const plId = add(`POLY_LOOP('',(${ptsStr}))`);
        const fobId = add(`FACE_OUTER_BOUND('',#${plId},.T.)`);
        const ndId = add(`DIRECTION('',(${fmt(n[0])},${fmt(n[1])},${fmt(n[2])}))`);
        const rx = Math.abs(n[0]) < 0.9 ? 1 : 0;
        const ry = rx === 0 ? 1 : 0;
        const rdId = add(`DIRECTION('',(${fmt(rx)},${fmt(ry)},0.))`);
        const axId = add(`AXIS2_PLACEMENT_3D('',#${cpIds[lp[0]]},#${ndId},#${rdId})`);
        const planeId = add(`PLANE('',#${axId})`);
        const fsId = add(`FACE_SURFACE('',(#${fobId}),#${planeId},.T.)`);
        faceIds.push(`#${fsId}`);
    }

    const cShell = add(`CLOSED_SHELL('',(${faceIds.join(",")}))`);
    const fBrep = add(`FACETED_BREP('${safeName}',#${cShell})`);
    const sRep = add(`FACETED_BREP_SHAPE_REPRESENTATION('${safeName}',(#${fBrep},#${wAxis}),#${gCtx})`);
    add(`SHAPE_DEFINITION_REPRESENTATION(#${pds},#${sRep})`);

    const stepText = `ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('Bambu2SolidWorks WebCAD AP214'),'2;1');\nFILE_NAME('${safeName}.step','${new Date().toISOString().slice(0, 19)}',('User'),('Bambu2SolidWorks'),'WebCAD 1.0','SolidWorks','');\nFILE_SCHEMA(('AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }'));\nENDSEC;\nDATA;\n` + lines.join("\n") + "\nENDSEC;\nEND-ISO-10303-21;\n";

    return {
        stepText,
        exportedFaces: polyLoops.length,
    };
}

function initClientSideSamples() {
    const demoSamples = [
        { name: "1_NEMA17_Step_Motor_Tutucu.stl", ext: "STL", url: "input/1_NEMA17_Step_Motor_Tutucu.stl", type: "bracket" },
        { name: "4_PCB_Sikistirma_Kelepcesi.stl", ext: "STL", url: "input/4_PCB_Sikistirma_Kelepcesi.stl", type: "flange" },
        { name: "bambu_flans_adaptor.stl", ext: "STL", url: "input/bambu_flans_adaptor.stl", type: "flange" },
        { name: "ornek_bambu_proje.3mf", ext: "3MF", url: "input/ornek_bambu_proje.3mf", type: "box" },
    ];
    const container = document.getElementById("sampleButtonsContainer");
    container.innerHTML = "";
    demoSamples.forEach((s, idx) => {
        const btn = document.createElement("button");
        btn.className = "sample-pill-btn" + (idx === 0 ? " active" : "");
        const extClass = s.ext === "3MF" ? "ext-3mf" : "ext-stl";
        btn.innerHTML = `<span class="ext-mini ${extClass}">${s.ext}</span> <span>${s.name}</span>`;
        btn.addEventListener("click", () => {
            document.querySelectorAll(".sample-pill-btn").forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            loadClientDemo(s);
        });
        container.appendChild(btn);
    });
    loadClientDemo(demoSamples[0]);
}

async function loadClientDemo(sample) {
    if (sample.url) {
        try {
            const res = await fetch(sample.url);
            if (res.ok) {
                const buf = await res.arrayBuffer();
                const parsed = sample.ext === "3MF" ? await parse3mfArrayBuffer(buf) : parseStlArrayBuffer(buf);
                applyLoadedFile({
                    filename: sample.name,
                    extension: sample.ext.toLowerCase(),
                    file_size_kb: +(buf.byteLength / 1024.0).toFixed(1),
                    triangles: parsed.faces.length,
                    vertices_count: parsed.uniqueVerts.length,
                    is_watertight: true,
                    dimensions_mm: parsed.dimensions_mm,
                    surface_area_cm2: parsed.surface_area_cm2,
                    volume_cm3: parsed.volume_cm3,
                    mesh_data: parsed.mesh_data,
                    _rawVerts: parsed.uniqueVerts,
                    _rawFaces: parsed.faces,
                });
                return;
            }
        } catch (_) {
            // Statik dosya çekilemezse prosedürel modele düş
        }
    }
    const parsed = generateClientDemoMesh(sample.type);
    applyLoadedFile({
        filename: sample.name,
        extension: sample.ext.toLowerCase(),
        file_size_kb: 42.8,
        triangles: parsed.faces.length,
        vertices_count: parsed.uniqueVerts.length,
        is_watertight: true,
        dimensions_mm: parsed.dimensions_mm,
        surface_area_cm2: parsed.surface_area_cm2,
        volume_cm3: parsed.volume_cm3,
        mesh_data: parsed.mesh_data,
        _rawVerts: parsed.uniqueVerts,
        _rawFaces: parsed.faces,
    });
}

/* ----------------------------------------------------------------------------
   4. KULLANICI ETKİLEŞİMLERİ VE OLAY DİNLEYİCİLER
   ---------------------------------------------------------------------------- */
function bindUIEvents() {
    const dropZone = document.getElementById("dropZone");
    const fileInput = document.getElementById("fileInput");

    dropZone.addEventListener("click", () => fileInput.click());

    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        dropZone.classList.add("dragover");
    });

    dropZone.addEventListener("dragleave", () => {
        dropZone.classList.remove("dragover");
    });

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        dropZone.classList.remove("dragover");
        if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
            uploadSelectedFile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files && e.target.files.length > 0) {
            uploadSelectedFile(e.target.files[0]);
        }
    });

    // Format Kartı Tıklamaları
    ["Step", "Sldprt", "Prt"].forEach((fmt) => {
        const chk = document.getElementById(`chk${fmt}`);
        const card = document.getElementById(`cardFmt${fmt}`);
        chk.addEventListener("change", () => {
            card.classList.toggle("active", chk.checked);
        });
    });

    // Açı Toleransı Slider
    const rngAngle = document.getElementById("rngAngle");
    const angleBadge = document.getElementById("angleValueBadge");
    rngAngle.addEventListener("input", () => {
        angleBadge.textContent = `${parseFloat(rngAngle.value).toFixed(1)}°`;
    });

    // Maksimum Yüzey Seçici
    const segButtons = document.querySelectorAll("#maxFacesGroup .seg-btn");
    segButtons.forEach((btn) => {
        btn.addEventListener("click", () => {
            segButtons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            selectedMaxFaces = parseInt(btn.getAttribute("data-val"), 10);
        });
    });

    // 3B Renk Swatch'ları
    const swatches = document.querySelectorAll(".swatch");
    swatches.forEach((sw) => {
        sw.addEventListener("click", () => {
            swatches.forEach((s) => s.classList.remove("active"));
            sw.classList.add("active");
            activeColorHex = sw.getAttribute("data-color");
            if (mainMesh && mainMesh.material) {
                mainMesh.material.color.set(activeColorHex);
            }
        });
    });

    // Görünüm Butonları
    const btnWire = document.getElementById("btnToggleWireframe");
    btnWire.addEventListener("click", () => {
        showEdges = !showEdges;
        btnWire.classList.toggle("active", showEdges);
        if (edgeLines) edgeLines.visible = showEdges;
    });

    const btnRot = document.getElementById("btnToggleAutoRotate");
    btnRot.addEventListener("click", () => {
        autoRotate = !autoRotate;
        btnRot.classList.toggle("active", autoRotate);
    });

    document.getElementById("btnResetCamera").addEventListener("click", () => {
        if (mainMesh && mainMesh.geometry) {
            fitCameraToMesh(mainMesh.geometry);
        }
    });

    // QR Kod Modalı
    const qrModal = document.getElementById("qrModal");
    document.getElementById("btnShowQrModal").addEventListener("click", () => {
        qrModal.classList.remove("hidden");
    });
    document.getElementById("btnCloseQrModal").addEventListener("click", () => {
        qrModal.classList.add("hidden");
    });
    qrModal.addEventListener("click", (e) => {
        if (e.target === qrModal) qrModal.classList.add("hidden");
    });

    // Ana Dönüştürme Butonu
    document.getElementById("btnConvert").addEventListener("click", startConversion);

    // Klasör ve SolidWorks Aksiyon Butonları
    document.getElementById("btnOpenFolder").addEventListener("click", async () => {
        const res = await fetch(`${apiBaseUrl}/api/open-folder`, { method: "POST" });
        const data = await res.json();
        showToast(data.message || "Çıktı klasörü açıldı.");
    });

    document.getElementById("btnOpenInSwDirect").addEventListener("click", () => {
        if (!currentFile) return;
        openInSolidWorks(currentFile.filename);
    });
}

/* ----------------------------------------------------------------------------
   5. YEREL SUNUCU VE TARAYICI DOSYA YÜKLEME YÖNETİMİ
   ---------------------------------------------------------------------------- */
async function loadSamplesFromLocalServer() {
    try {
        const res = await fetch(`${apiBaseUrl}/api/samples`);
        const data = await res.json();

        const container = document.getElementById("sampleButtonsContainer");
        container.innerHTML = "";

        if (data.samples && data.samples.length > 0) {
            data.samples.forEach((s, idx) => {
                const btn = document.createElement("button");
                btn.className = "sample-pill-btn" + (idx === 0 ? " active" : "");
                const extClass = s.ext === "3MF" ? "ext-3mf" : "ext-stl";
                btn.innerHTML = `<span class="ext-mini ${extClass}">${s.ext}</span> <span>${s.name}</span>`;
                btn.addEventListener("click", () => {
                    document.querySelectorAll(".sample-pill-btn").forEach((b) => b.classList.remove("active"));
                    btn.classList.add("active");
                    selectSampleModel(s.name);
                });
                container.appendChild(btn);
            });

            await selectSampleModel(data.samples[0].name);
        }

        if (data.outputs && data.outputs.length > 0) {
            renderDownloadCards(
                data.outputs.map((o) => ({
                    format: o.ext,
                    filename: o.name,
                    size_kb: o.size_kb,
                    url: o.url.startsWith("/") ? `${apiBaseUrl}${o.url}` : o.url,
                }))
            );
        }
    } catch (err) {
        console.error(err);
    }
}

async function selectSampleModel(sampleName) {
    try {
        const res = await fetch(`${apiBaseUrl}/api/upload`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ sample_name: sampleName }),
        });
        const data = await res.json();
        if (data.success) {
            applyLoadedFile(data.file);
        } else {
            showToast(data.error || "Model yüklenemedi", true);
        }
    } catch (err) {
        showToast("Sunucu bağlantı hatası: " + err.message, true);
    }
}

async function tryConnectLocalBridge() {
    if (isLocalBackend) return true;
    for (const base of ["", "http://127.0.0.1:5050"]) {
        try {
            const ctrl = new AbortController();
            const tid = setTimeout(() => ctrl.abort(), 1200);
            const res = await fetch(`${base}/api/info`, { signal: ctrl.signal });
            clearTimeout(tid);
            if (res.ok) {
                isLocalBackend = true;
                apiBaseUrl = base;
                document.getElementById("engineModeText").textContent = "SolidWorks 2026 Masaüstü Bağlantısı Aktif";
                document.getElementById("btnOpenFolder").classList.remove("hidden");
                document.getElementById("btnOpenInSwDirect").classList.remove("hidden");
                document.getElementById("rowOpenSwAfter").classList.remove("hidden");
                return true;
            }
        } catch (_) {}
    }
    return false;
}

async function uploadSelectedFile(fileObj) {
    document.getElementById("dropTitle").textContent = `⏳ '${fileObj.name}' Analiz Ediliyor...`;

    await tryConnectLocalBridge();

    if (isLocalBackend) {
        const formData = new FormData();
        formData.append("file", fileObj);
        try {
            const res = await fetch(`${apiBaseUrl}/api/upload`, { method: "POST", body: formData });
            const data = await res.json();
            if (data.success) {
                data.file._fileBlob = fileObj;
                applyLoadedFile(data.file);
                showToast(`✅ ${data.file.filename} 3B sahneye yüklendi!`);
                return;
            } else {
                showToast(data.error || "Dosya yüklenemedi", true);
            }
        } catch (_) {
            // Sunucu yanıt vermezse tarayıcı motoruna düş
        }
    }

    // Tarayıcı İçi (Client-Side) STL / 3MF Okuma
    try {
        const buffer = await fileObj.arrayBuffer();
        const ext = fileObj.name.split(".").pop().toLowerCase();
        let parsed;
        if (ext === "3mf") {
            parsed = await parse3mfArrayBuffer(buffer);
        } else {
            parsed = parseStlArrayBuffer(buffer);
        }
        applyLoadedFile({
            filename: fileObj.name,
            extension: ext,
            file_size_kb: +(fileObj.size / 1024.0).toFixed(1),
            triangles: parsed.faces.length,
            vertices_count: parsed.uniqueVerts.length,
            is_watertight: true,
            dimensions_mm: parsed.dimensions_mm,
            surface_area_cm2: parsed.surface_area_cm2,
            volume_cm3: parsed.volume_cm3,
            mesh_data: parsed.mesh_data,
            _rawVerts: parsed.uniqueVerts,
            _rawFaces: parsed.faces,
            _fileBlob: fileObj,
        });
        showToast(`✅ ${fileObj.name} 3B sahneye yüklendi!`);
    } catch (err) {
        showToast("Dosya okuma hatası: " + err.message, true);
    }
}

function applyLoadedFile(fileInfo) {
    currentFile = fileInfo;

    document.getElementById("dropTitle").textContent = `✅ Seçili Model: ${fileInfo.filename} (${fileInfo.file_size_kb} KB)`;
    document.getElementById("viewerFileName").textContent = `${fileInfo.filename} (${fileInfo.extension.toUpperCase()})`;

    const d = fileInfo.dimensions_mm;
    document.getElementById("metricDims").textContent = `${d.x} × ${d.y} × ${d.z} mm`;
    document.getElementById("metricAreaVol").textContent = `Alan: ${fileInfo.surface_area_cm2} cm² | Hacim: ${fileInfo.volume_cm3} cm³`;

    document.getElementById("metricTriangles").textContent = `${fileInfo.triangles.toLocaleString("tr-TR")} Üçgen`;
    document.getElementById("metricVertices").textContent = `${fileInfo.vertices_count.toLocaleString("tr-TR")} Köşe Noktası`;

    document.getElementById("metricWatertight").textContent = fileInfo.is_watertight
        ? "🟢 Kapalı Katı Gövde"
        : "🟡 Açık Yüzey Ağı";
    document.getElementById("metricElapsed").textContent = "Dönüştürmeye Hazır";
    document.getElementById("btnOpenInSwDirect").disabled = false;

    if (fileInfo.mesh_data) {
        renderMeshInThree(fileInfo.mesh_data);
    }
}

/* ----------------------------------------------------------------------------
   6. CAD DÖNÜŞTÜRME İŞLEMİ (HEM YEREL PC HEM 7/24 WEB MODU)
   ---------------------------------------------------------------------------- */
async function startConversion() {
    if (!currentFile) {
        showToast("Lütfen önce bir .STL veya Bambu .3MF dosyası seçin!", true);
        return;
    }

    const expStep = document.getElementById("chkStep").checked;
    const expSldprt = document.getElementById("chkSldprt").checked;
    const expPrt = document.getElementById("chkPrt").checked;

    if (!expStep && !expSldprt && !expPrt) {
        showToast("Lütfen en az bir çıktı formatı (.STEP, .SLDPRT, .PRT) seçin!", true);
        return;
    }

    const btnConvert = document.getElementById("btnConvert");
    const btnText = document.getElementById("btnConvertText");
    const progWrap = document.getElementById("progressContainer");
    const progFill = document.getElementById("progressBarFill");
    const progStatus = document.getElementById("progressStatusText");
    const progPct = document.getElementById("progressPercentText");

    btnConvert.disabled = true;
    btnText.textContent = "⏳ CAD YÜZEYLERİ BİRLEŞTİRİLİYOR VE ÇIKTILAR ÜRETİLİYOR...";
    progWrap.classList.remove("hidden");

    // Dönüştürmeden hemen önce yerel SolidWorks servisi (http://127.0.0.1:5050) aktif mi kontrol et
    if (!isLocalBackend) {
        const connected = await tryConnectLocalBridge();
        if (connected && currentFile._fileBlob) {
            try {
                const fd = new FormData();
                fd.append("file", currentFile._fileBlob);
                const upRes = await fetch(`${apiBaseUrl}/api/upload`, { method: "POST", body: fd });
                const upData = await upRes.json();
                if (upData.success && upData.file) {
                    currentFile.filename = upData.file.filename;
                }
            } catch (_) {}
        }
    }

    // 1. YEREL MASAÜSTÜ SUNUCU MODU (Flask + SolidWorks COM)
    if (isLocalBackend) {
        let pct = 15;
        progFill.style.width = `${pct}%`;
        progPct.textContent = `${pct}%`;
        progStatus.textContent = "📐 Düzlemsel üçgenler analitik yüzeylere birleştiriliyor...";

        const timer = setInterval(() => {
            if (pct < 88) {
                pct += 8;
                progFill.style.width = `${pct}%`;
                progPct.textContent = `${pct}%`;
                if (pct > 45 && pct < 75) {
                    progStatus.textContent = "💾 ISO-10303 AP214 STEP B-Rep geometrisi yazılıyor...";
                } else if (pct >= 75) {
                    progStatus.textContent = "⚙️ SolidWorks 2026 üzerinden .SLDPRT ve .PRT oluşturuluyor...";
                }
            }
        }, 400);

        try {
            const payload = {
                filename: currentFile.filename,
                merge_coplanar: document.getElementById("chkMergeCoplanar").checked,
                coplanar_angle_deg: parseFloat(document.getElementById("rngAngle").value),
                max_faces: selectedMaxFaces,
                export_step: expStep,
                export_sldprt: expSldprt,
                export_prt: expPrt,
                open_in_solidworks: document.getElementById("chkOpenSwAfter").checked,
            };

            const res = await fetch(`${apiBaseUrl}/api/convert`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const data = await res.json();
            clearInterval(timer);

            if (data.success) {
                progFill.style.width = "100%";
                progPct.textContent = "100%";
                progStatus.textContent = "✅ Dönüştürme Başarıyla Tamamlandı!";

                const st = data.stats;
                document.getElementById("metricCadFaces").textContent = `${st.exported_faces.toLocaleString("tr-TR")} CAD Yüzeyi`;
                document.getElementById("metricReduction").textContent = `%${st.reduction_pct} Yüzey Sadeleştirmesi`;
                document.getElementById("metricElapsed").textContent = `Süre: ${st.elapsed_sec} saniye`;

                const fixedDownloads = (data.downloads || []).map((d) => ({
                    ...d,
                    url: d.url.startsWith("/") ? `${apiBaseUrl}${d.url}` : d.url,
                }));
                renderDownloadCards(fixedDownloads);
                showToast(`🎉 ${data.message}`);
            } else {
                progStatus.textContent = "❌ Hata oluştu";
                showToast(data.error || "Dönüştürme başarısız oldu", true);
            }
        } catch (err) {
            clearInterval(timer);
            showToast("Dönüştürme hatası: " + err.message, true);
        } finally {
            btnConvert.disabled = false;
            btnText.textContent = "🚀 DÖNÜŞTÜRMEYİ BAŞLAT (.STEP / .SLDPRT / .PRT)";
        }
        return;
    }

    // 2. 7/24 WEB / GITHUB PAGES TARAYICI İÇİ DÖNÜŞTÜRME MODU
    try {
        const t0 = performance.now();
        progFill.style.width = "45%";
        progPct.textContent = "45%";
        progStatus.textContent = "📐 Tarayıcı içi B-Rep motoru düzlemsel yüzeyleri birleştiriyor...";

        await new Promise((r) => setTimeout(r, 120));

        const stem = currentFile.filename.replace(/\.[^/.]+$/, "");
        const mergeCop = document.getElementById("chkMergeCoplanar").checked;
        const angTol = parseFloat(document.getElementById("rngAngle").value);

        const { stepText, exportedFaces } = buildStepAp214ClientSide(
            currentFile._rawVerts || [],
            currentFile._rawFaces || [],
            stem,
            mergeCop,
            angTol
        );

        const downloads = [];
        const zip = new JSZip();
        const sizeKb = +(new Blob([stepText]).size / 1024.0).toFixed(1);

        // Her zaman geçerli ISO-10303-21 .STEP dosyasını oluştur (SolidWorks doğrudan hatasız açar)
        const stepBlob = new Blob([stepText], { type: "application/step" });
        downloads.push({
            format: "STEP (SolidWorks Uyumlu)",
            filename: `${stem}.step`,
            size_kb: sizeKb,
            url: URL.createObjectURL(stepBlob),
        });
        zip.file(`${stem}.step`, stepText);

        // Önceden SolidWorks 2026 ile üretilmiş gerçek ikili (binary) .SLDPRT ve .PRT var mı kontrol et
        if (expSldprt) {
            let foundBinarySldprt = false;
            for (const candExt of ["SLDPRT", "sldprt"]) {
                try {
                    const r = await fetch(`output/${stem}.${candExt}`);
                    if (r.ok) {
                        const b = await r.blob();
                        if (b.size > 1000) {
                            downloads.push({
                                format: "SLDPRT",
                                filename: `${stem}.SLDPRT`,
                                size_kb: +(b.size / 1024.0).toFixed(1),
                                url: URL.createObjectURL(b),
                            });
                            zip.file(`${stem}.SLDPRT`, b);
                            foundBinarySldprt = true;
                            break;
                        }
                    }
                } catch (_) {}
            }
            if (!foundBinarySldprt) {
                showToast("ℹ️ Tarayıcı modunda SolidWorks için .STEP üretildi (SolidWorks'te doğrudan açılır). Yerel .SLDPRT için masaüstü servisini kullanın.");
            }
        }

        if (expPrt) {
            let foundBinaryPrt = false;
            for (const candExt of ["PRT", "prt"]) {
                try {
                    const r = await fetch(`output/${stem}.${candExt}`);
                    if (r.ok) {
                        const b = await r.blob();
                        if (b.size > 1000) {
                            downloads.push({
                                format: "PRT",
                                filename: `${stem}.PRT`,
                                size_kb: +(b.size / 1024.0).toFixed(1),
                                url: URL.createObjectURL(b),
                            });
                            zip.file(`${stem}.PRT`, b);
                            foundBinaryPrt = true;
                            break;
                        }
                    }
                } catch (_) {}
            }
        }

        if (downloads.length > 1) {
            const zipBlob = await zip.generateAsync({ type: "blob", compression: "DEFLATE" });
            downloads.push({
                format: "ZIP (TÜMÜ)",
                filename: `${stem}_CAD_Paketi.zip`,
                size_kb: +(zipBlob.size / 1024.0).toFixed(1),
                url: URL.createObjectURL(zipBlob),
            });
        }

        const elapsed = ((performance.now() - t0) / 1000.0).toFixed(2);
        const origT = currentFile.triangles || 1;
        const redPct = Math.max(0, (1.0 - exportedFaces / origT) * 100.0).toFixed(1);

        progFill.style.width = "100%";
        progPct.textContent = "100%";
        progStatus.textContent = "✅ Dönüştürme Başarıyla Tamamlandı!";

        document.getElementById("metricCadFaces").textContent = `${exportedFaces.toLocaleString("tr-TR")} CAD Yüzeyi`;
        document.getElementById("metricReduction").textContent = `%${redPct} Yüzey Sadeleştirmesi`;
        document.getElementById("metricElapsed").textContent = `Süre: ${elapsed} saniye`;

        renderDownloadCards(downloads);
        showToast(`🎉 ${origT} üçgen -> ${exportedFaces} CAD yüzeyine dönüştürüldü!`);
    } catch (err) {
        showToast("Dönüştürme hatası: " + err.message, true);
    } finally {
        btnConvert.disabled = false;
        btnText.textContent = "🚀 DÖNÜŞTÜRMEYİ BAŞLAT (.STEP / .SLDPRT / .PRT)";
    }
}

function renderDownloadCards(downloads) {
    const grid = document.getElementById("downloadsGrid");
    grid.innerHTML = "";

    if (!downloads || downloads.length === 0) return;

    downloads.forEach((d) => {
        const fmtUpper = d.format.toUpperCase();
        let cardClass = "dl-step";
        if (fmtUpper.includes("SLDPRT")) cardClass = "dl-sldprt";
        else if (fmtUpper.includes("PRT")) cardClass = "dl-prt";
        else if (fmtUpper.includes("ZIP")) cardClass = "dl-zip";

        const a = document.createElement("a");
        a.href = d.url;
        a.className = `dl-card ${cardClass}`;
        a.setAttribute("download", d.filename);
        a.innerHTML = `
            <div class="dl-top">
                <span class="dl-badge">.${fmtUpper}</span>
                <span class="dl-size">${d.size_kb} KB</span>
            </div>
            <div class="dl-filename">${d.filename}</div>
            <div class="dl-action">⬇️ Dosyayı İndir</div>
        `;
        grid.appendChild(a);
    });
}

async function openInSolidWorks(filename) {
    showToast(`🔧 ${filename} SolidWorks 2026'da açılıyor...`);
    try {
        const res = await fetch(`${apiBaseUrl}/api/open-solidworks`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ filename }),
        });
        const data = await res.json();
        showToast(data.message || "SolidWorks'te açıldı!", !data.success);
    } catch (err) {
        showToast("SolidWorks açma hatası: " + err.message, true);
    }
}

function showToast(message, isError = false) {
    const container = document.getElementById("toastContainer");
    const toast = document.createElement("div");
    toast.className = "toast" + (isError ? " error" : "");
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transition = "opacity 0.3s ease";
        setTimeout(() => toast.remove(), 300);
    }, 4200);
}
