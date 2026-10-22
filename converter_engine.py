"""
Bambu Studio (.3mf / .stl) -> SolidWorks (.step / .sldprt / .prt) Dönüştürücü Motoru
=====================================================================================
Bu modül:
1. Binary/ASCII .STL, Bambu Studio .3MF (çoklu parça ve component transform destekli) ve .OBJ dosyalarını okur.
2. Aynı düzlemdeki komşu üçgenleri (coplanar faces) tek bir analitik düzlem yüzeyde (PLANE + delik sınırları)
   birleştirerek SolidWorks'te "Çizim Açılabilir" (Sketchable) temiz B-Rep yüzeyler üretir.
3. ISO-10303-21 STEP (AP214 Automotive Design - ADVANCED_BREP & FACETED_BREP) dosyası üretir.
4. Bilgisayarda yüklü olan SolidWorks (COM API: SldWorks.Application) üzerinden doğrudan yerel
   .SLDPRT ve .PRT dosyaları oluşturur veya modeli tek tıkla SolidWorks ekranında açar.
"""

from __future__ import annotations

import math
import os
import shutil
import struct
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import trimesh


@dataclass
class PlanarFace:
    """Düzlemsel bir CAD yüzeyini (dış sınır + iç delik sınırları) temsil eder."""
    outer_loop: List[int]
    inner_loops: List[List[int]]
    normal: Tuple[float, float, float]
    ref_dir: Tuple[float, float, float]
    origin: Tuple[float, float, float]


@dataclass
class ConversionOptions:
    """Dönüştürme seçenekleri."""
    merge_coplanar: bool = True
    coplanar_angle_deg: float = 1.0
    max_faces: int = 25000  # Çok yüksek detaylı organik modeller için maksimum üçgen sınırı (0 = sınırsız)
    unit: str = "mm"  # "mm", "cm", "m", "inch"
    step_mode: str = "advanced_brep"  # "advanced_brep" veya "faceted_brep"
    export_step: bool = True
    export_sldprt: bool = True
    export_prt: bool = True
    open_in_solidworks_after: bool = False
    solidworks_import_mode: str = "step_brep"  # "step_brep" (önerilen) veya "direct_stl_solid"


@dataclass
class ConversionResult:
    """Dönüştürme işlemi sonucu."""
    input_file: str
    success: bool
    message: str
    original_triangles: int = 0
    processed_triangles: int = 0
    exported_faces: int = 0
    is_watertight: bool = False
    elapsed_sec: float = 0.0
    output_files: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 1. BAMBU STUDIO (.3MF) VE .STL OKUYUCU
# ---------------------------------------------------------------------------

def _parse_3mf_transform(transform_str: Optional[str]) -> np.ndarray:
    """
    3MF spesifikasyonundaki 3x4 afin dönüşüm matrisini (12 float) 4x4 homojen matrise çevirir.
    3MF formatı: "m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32"
    Burada satır vektörü [x, y, z, 1] sağdan çarpılır, yani sütun bazlı:
    x' = m00*x + m10*y + m20*z + m30
    """
    mat = np.eye(4, dtype=np.float64)
    if not transform_str:
        return mat
    parts = [float(x) for x in transform_str.strip().split()]
    if len(parts) != 12:
        return mat
    # 3MF standard matrix layout:
    mat[0, 0], mat[1, 0], mat[2, 0] = parts[0], parts[1], parts[2]
    mat[0, 1], mat[1, 1], mat[2, 1] = parts[3], parts[4], parts[5]
    mat[0, 2], mat[1, 2], mat[2, 2] = parts[6], parts[7], parts[8]
    mat[0, 3], mat[1, 3], mat[2, 3] = parts[9], parts[10], parts[11]
    return mat


def load_bambu_3mf(file_path: str) -> trimesh.Trimesh:
    """
    Bambu Studio / MakerWorld .3mf dosyalarını (Production Extension / 3D/Objects/*.model dahil)
    tam hiyerarşi ve dönüşüm matrisleriyle okuyup tek bir birleşik Trimesh olarak döndürür.
    """
    meshes: List[trimesh.Trimesh] = []

    with zipfile.ZipFile(file_path, "r") as zf:
        namelist = zf.namelist()
        model_files = [n for n in namelist if n.lower().endswith(".model")]
        if not model_files:
            raise ValueError(f".3mf arşivinde .model dosyası bulunamadı: {file_path}")

        # Tüm .model dosyalarındaki <object id="..."> nesnelerini önbelleğe al
        # Anahtar: (norm_model_path, object_id)
        objects_map: Dict[Tuple[str, str], dict] = {}
        build_items: List[Tuple[str, str, np.ndarray]] = []

        def norm_path(p: str) -> str:
            return p.lstrip("/").replace("\\", "/")

        for mf in model_files:
            norm_mf = norm_path(mf)
            xml_bytes = zf.read(mf)
            root = ET.fromstring(xml_bytes)

            # Namespace'den bağımsız yerel etiket adı yardımcısı
            def local_tag(elem) -> str:
                return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

            for child in root:
                ltag = local_tag(child)
                if ltag == "resources":
                    for obj_elem in child:
                        if local_tag(obj_elem) != "object":
                            continue
                        obj_id = obj_elem.attrib.get("id", "")
                        obj_name = obj_elem.attrib.get("name", f"Object_{obj_id}")
                        obj_data = {
                            "name": obj_name,
                            "vertices": None,
                            "triangles": None,
                            "components": [],  # list of (target_model_path, target_obj_id, transform_4x4)
                        }
                        for sub in obj_elem:
                            stag = local_tag(sub)
                            if stag == "mesh":
                                verts = []
                                tris = []
                                for mchild in sub:
                                    mtag = local_tag(mchild)
                                    if mtag == "vertices":
                                        for v in mchild:
                                            if local_tag(v) == "vertex":
                                                verts.append((
                                                    float(v.attrib["x"]),
                                                    float(v.attrib["y"]),
                                                    float(v.attrib["z"]),
                                                ))
                                    elif mtag == "triangles":
                                        for t in mchild:
                                            if local_tag(t) == "triangle":
                                                tris.append((
                                                    int(t.attrib["v1"]),
                                                    int(t.attrib["v2"]),
                                                    int(t.attrib["v3"]),
                                                ))
                                if verts and tris:
                                    obj_data["vertices"] = np.array(verts, dtype=np.float64)
                                    obj_data["triangles"] = np.array(tris, dtype=np.int64)
                            elif stag == "components":
                                for comp in sub:
                                    if local_tag(comp) == "component":
                                        comp_obj_id = comp.attrib.get("objectid", "")
                                        comp_transform = _parse_3mf_transform(comp.attrib.get("transform"))
                                        # p:path attribute (3MF Production Extension used by Bambu Studio)
                                        comp_path = None
                                        for k, v in comp.attrib.items():
                                            if k.endswith("path"):
                                                comp_path = norm_path(v)
                                                break
                                        if not comp_path:
                                            comp_path = norm_mf
                                        obj_data["components"].append((comp_path, comp_obj_id, comp_transform))
                        objects_map[(norm_mf, obj_id)] = obj_data

                elif ltag == "build":
                    for item in child:
                        if local_tag(item) == "item":
                            item_obj_id = item.attrib.get("objectid", "")
                            item_transform = _parse_3mf_transform(item.attrib.get("transform"))
                            item_path = None
                            for k, v in item.attrib.items():
                                if k.endswith("path"):
                                    item_path = norm_path(v)
                                    break
                            if not item_path:
                                item_path = norm_mf
                            build_items.append((item_path, item_obj_id, item_transform))

        def instantiate_object(model_path: str, obj_id: str, parent_tf: np.ndarray, depth: int = 0):
            if depth > 16:
                return
            key = (model_path, obj_id)
            if key not in objects_map:
                # Fallback: aynı obj_id'yi diğer model dosyalarında ara
                candidates = [k for k in objects_map if k[1] == obj_id]
                if not candidates:
                    return
                key = candidates[0]

            obj_data = objects_map[key]
            if obj_data["vertices"] is not None and obj_data["triangles"] is not None:
                v = obj_data["vertices"]
                f = obj_data["triangles"]
                # 4x4 dönüşüm uygula
                ones = np.ones((len(v), 1), dtype=np.float64)
                vh = np.hstack([v, ones])
                vt = (parent_tf @ vh.T).T[:, :3]
                meshes.append(trimesh.Trimesh(vertices=vt, faces=f, process=False))

            for comp_path, comp_id, comp_tf in obj_data["components"]:
                combined_tf = parent_tf @ comp_tf
                instantiate_object(comp_path, comp_id, combined_tf, depth + 1)

        if build_items:
            for b_path, b_id, b_tf in build_items:
                instantiate_object(b_path, b_id, b_tf)
        else:
            # Build listesi boşsa tüm mesh nesnelerini doğrudan ekle
            for (m_path, o_id), o_data in objects_map.items():
                if o_data["vertices"] is not None:
                    instantiate_object(m_path, o_id, np.eye(4, dtype=np.float64))

    if not meshes:
        raise ValueError(f".3mf dosyasından hiçbir 3B mesh geometrisi çıkarılamadı: {file_path}")

    if len(meshes) == 1:
        combined = meshes[0]
    else:
        combined = trimesh.util.concatenate(meshes)

    return combined


def load_mesh_file(file_path: str) -> trimesh.Trimesh:
    """
    .stl, .3mf, .obj, .ply dosyalarını yükleyip tek bir Trimesh nesnesi döndürür.
    """
    ext = Path(file_path).suffix.lower()
    if ext == ".3mf":
        return load_bambu_3mf(file_path)

    loaded = trimesh.load(file_path, force="mesh", process=False)
    if isinstance(loaded, trimesh.Scene):
        if len(loaded.geometry) == 0:
            raise ValueError(f"Dosyada 3B geometri bulunamadı: {file_path}")
        loaded = trimesh.util.concatenate(list(loaded.geometry.values()))

    if not isinstance(loaded, trimesh.Trimesh) or len(loaded.faces) == 0:
        raise ValueError(f"Geçerli bir üçgen ağ (mesh) okunamadı: {file_path}")

    return loaded


# ---------------------------------------------------------------------------
# 2. MESH TEMİZLEME, ÖLÇEKLEME VE YÜZEY AZALTMA (DECIMATION)
# ---------------------------------------------------------------------------

def decimate_mesh_vertex_clustering(vertices: np.ndarray, faces: np.ndarray, target_faces: int) -> Tuple[np.ndarray, np.ndarray]:
    """
    Çok yüksek detaylı STL/3MF dosyalarını SolidWorks'ün katı gövde sınırına (ör. 25.000 yüzey)
    uygun hale getirmek için hızlı 3B ızgara (vertex clustering) tabanlı yüzey sadeleştirme yapar.
    """
    if len(faces) <= target_faces or target_faces <= 0:
        return vertices, faces

    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    diag = float(np.linalg.norm(bbox_max - bbox_min))
    if diag < 1e-9:
        return vertices, faces

    # İkili arama ile hedef üçgen sayısına en yakın hücre boyutunu bul
    ratio = math.sqrt(len(faces) / float(target_faces))
    low_grid = max(10, int(120 / max(ratio, 1.0)))
    high_grid = 800

    best_v, best_f = vertices, faces
    for _ in range(8):
        mid_grid = (low_grid + high_grid) // 2
        cell_size = diag / float(mid_grid)
        grid_coords = np.floor((vertices - bbox_min) / cell_size).astype(np.int64)

        # Her ızgara hücresindeki köşeleri ortalama konumda birleştir
        _, inverse_idx = np.unique(grid_coords, axis=0, return_inverse=True)
        n_clusters = int(inverse_idx.max()) + 1

        new_verts = np.zeros((n_clusters, 3), dtype=np.float64)
        counts = np.bincount(inverse_idx)
        for dim in range(3):
            new_verts[:, dim] = np.bincount(inverse_idx, weights=vertices[:, dim]) / counts

        mapped_faces = inverse_idx[faces]
        # Dejenere (iki köşesi aynı hücreye düşen) üçgenleri filtrele
        valid_mask = (
            (mapped_faces[:, 0] != mapped_faces[:, 1])
            & (mapped_faces[:, 1] != mapped_faces[:, 2])
            & (mapped_faces[:, 2] != mapped_faces[:, 0])
        )
        new_faces = mapped_faces[valid_mask]

        if len(new_faces) > 0:
            # Tekrarlanan üçgenleri kaldır
            sorted_f = np.sort(new_faces, axis=1)
            _, unique_f_idx = np.unique(sorted_f, axis=0, return_index=True)
            new_faces = new_faces[np.sort(unique_f_idx)]

        best_v, best_f = new_verts, new_faces
        if abs(len(new_faces) - target_faces) / float(target_faces) < 0.12:
            break
        if len(new_faces) > target_faces:
            high_grid = mid_grid - 1
        else:
            low_grid = mid_grid + 1

    return best_v, best_f


def clean_and_prepare_mesh(
    mesh: trimesh.Trimesh,
    max_faces: int = 25000,
) -> Tuple[np.ndarray, np.ndarray, bool]:
    """
    Mesh köşelerini birleştirir, dejenere yüzeyleri temizler ve gerekirse yüzey sayısını optimize eder.
    Dönüş: (vertices, faces, is_watertight)
    """
    # Yakın köşeleri birleştir (1e-5 mm tolerans)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    # Dejenere ve tekrarlanan üçgenleri kaldır
    nondegenerate = mesh.nondegenerate_faces(height=1e-7)
    mesh.update_faces(nondegenerate)
    unique_f = mesh.unique_faces()
    mesh.update_faces(unique_f)
    mesh.remove_unreferenced_vertices()

    vertices = np.array(mesh.vertices, dtype=np.float64)
    faces = np.array(mesh.faces, dtype=np.int64)

    if max_faces and max_faces > 0 and len(faces) > max_faces:
        vertices, faces = decimate_mesh_vertex_clustering(vertices, faces, max_faces)
        mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
        nondegenerate = mesh.nondegenerate_faces(height=1e-7)
        mesh.update_faces(nondegenerate)
        mesh.remove_unreferenced_vertices()
        vertices = np.array(mesh.vertices, dtype=np.float64)
        faces = np.array(mesh.faces, dtype=np.int64)

    try:
        trimesh.repair.fix_normals(mesh)
        vertices = np.array(mesh.vertices, dtype=np.float64)
        faces = np.array(mesh.faces, dtype=np.int64)
    except Exception:
        pass

    return vertices, faces, bool(mesh.is_watertight)


# ---------------------------------------------------------------------------
# 3. DÜZLEMSEL YÜZEY BİRLEŞTİRME (COPLANAR POLYGON MERGING)
# ---------------------------------------------------------------------------

def _orthogonal_basis(normal: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Birim normal vektöre dik iki birim eksen (u_axis, v_axis) döndürür."""
    nx, ny, nz = float(normal[0]), float(normal[1]), float(normal[2])
    if abs(nx) < 0.9:
        ref = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        ref = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    u = ref - np.dot(ref, normal) * normal
    u_norm = float(np.linalg.norm(u))
    if u_norm < 1e-12:
        u = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    else:
        u = u / u_norm
    v = np.cross(normal, u)
    v_norm = float(np.linalg.norm(v))
    if v_norm > 1e-12:
        v = v / v_norm
    return u, v


def build_planar_faces(
    vertices: np.ndarray,
    faces: np.ndarray,
    merge_coplanar: bool = True,
    angle_tol_deg: float = 1.0,
    plane_dist_tol: float = 1e-3,
) -> List[PlanarFace]:
    """
    Üçgen ağı (mesh) alır:
    - merge_coplanar=True ise aynı düzlemdeki komşu üçgenleri tek bir çokgen yüzeyde
      (dış sınır + varsa iç delik sınırları) birleştirir ve doğrusal (collinear) kenarları sadeleştirir.
    - Topolojik olarak her zaman %100 geçerli, T-kavşağı (T-junction) içermeyen PlanarFace listesi döndürür.
    """
    n_tris = len(faces)
    if n_tris == 0:
        return []

    # Üçgen normallerini ve alanlarını hesapla
    v0 = vertices[faces[:, 0]]
    v1 = vertices[faces[:, 1]]
    v2 = vertices[faces[:, 2]]
    cross_vecs = np.cross(v1 - v0, v2 - v0)
    norms = np.linalg.norm(cross_vecs, axis=1)
    areas = 0.5 * norms

    safe_norms = np.where(norms > 1e-12, norms, 1.0)
    unit_normals = cross_vecs / safe_norms[:, None]

    if not merge_coplanar or n_tris == 1:
        result: List[PlanarFace] = []
        for i in range(n_tris):
            if norms[i] <= 1e-12:
                continue
            n_vec = unit_normals[i]
            u_vec, _ = _orthogonal_basis(n_vec)
            f = faces[i]
            orig = vertices[f[0]]
            result.append(
                PlanarFace(
                    outer_loop=[int(f[0]), int(f[1]), int(f[2])],
                    inner_loops=[],
                    normal=(float(n_vec[0]), float(n_vec[1]), float(n_vec[2])),
                    ref_dir=(float(u_vec[0]), float(u_vec[1]), float(u_vec[2])),
                    origin=(float(orig[0]), float(orig[1]), float(orig[2])),
                )
            )
        return result

    # Yönlü yarı-kenar (directed half-edge) haritası: (u, v) -> [tri_idx, ...]
    half_edge_to_tri: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for t_idx in range(n_tris):
        if norms[t_idx] <= 1e-12:
            continue
        a, b, c = int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])
        half_edge_to_tri[(a, b)].append(t_idx)
        half_edge_to_tri[(b, c)].append(t_idx)
        half_edge_to_tri[(c, a)].append(t_idx)

    # Komşuluk grafiği (yalnızca manifold kenarlar: (u,v) tek üçgen ve (v,u) tek üçgen)
    adj: List[List[int]] = [[] for _ in range(n_tris)]
    for (u, v), t_list in half_edge_to_tri.items():
        if u < v and len(t_list) == 1:
            opp_list = half_edge_to_tri.get((v, u), [])
            if len(opp_list) == 1:
                t1, t2 = t_list[0], opp_list[0]
                if t1 != t2:
                    adj[t1].append(t2)
                    adj[t2].append(t1)

    cos_tol = math.cos(math.radians(max(angle_tol_deg, 0.05)))
    visited = np.zeros(n_tris, dtype=bool)
    # Büyük alanlı üçgenlerden başlayarak düzlem bölgeleri büyüt
    order = np.argsort(-areas)

    raw_clusters: List[List[int]] = []
    for seed in order:
        seed_idx = int(seed)
        if visited[seed_idx] or norms[seed_idx] <= 1e-12:
            continue
        visited[seed_idx] = True
        seed_n = unit_normals[seed_idx]
        seed_p = vertices[faces[seed_idx, 0]]

        cluster = [seed_idx]
        queue = deque([seed_idx])
        while queue:
            curr = queue.popleft()
            for nb in adj[curr]:
                if visited[nb] or norms[nb] <= 1e-12:
                    continue
                # Hem komşu üçgenle hem de tohum düzlemle açı kontrolü (silindirik yüzeyde kaymayı önler)
                if float(np.dot(unit_normals[nb], seed_n)) < cos_tol:
                    continue
                if float(np.dot(unit_normals[nb], unit_normals[curr])) < cos_tol:
                    continue
                # Üçgenin 3 köşesinin tohum düzleme dik uzaklığı
                tri_pts = vertices[faces[nb]]
                dists = np.abs((tri_pts - seed_p) @ seed_n)
                if float(np.max(dists)) > plane_dist_tol:
                    continue
                visited[nb] = True
                cluster.append(nb)
                queue.append(nb)
        raw_clusters.append(cluster)

    # Her çok-üçgenli kümenin sınır döngülerini (boundary loops) çıkar ve doğrula
    valid_clusters: List[dict] = []
    for cluster in raw_clusters:
        if len(cluster) == 1:
            t_idx = cluster[0]
            valid_clusters.append({
                "merged": False,
                "tris": [t_idx],
                "outer_loop": [int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])],
                "inner_loops": [],
                "normal": unit_normals[t_idx],
            })
            continue

        # İç kenarları iptal et: (u, v) ve (v, u) aynı kümedeyse birbirini götürür
        edge_set = set()
        valid_half_edges = True
        for t_idx in cluster:
            a, b, c = int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])
            for u, v in ((a, b), (b, c), (c, a)):
                if (v, u) in edge_set:
                    edge_set.remove((v, u))
                elif (u, v) in edge_set:
                    # Non-manifold kenar: aynı yönlü kenar iki kez geçti
                    valid_half_edges = False
                else:
                    edge_set.add((u, v))

        if not valid_half_edges or not edge_set:
            for t_idx in cluster:
                valid_clusters.append({
                    "merged": False,
                    "tris": [t_idx],
                    "outer_loop": [int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])],
                    "inner_loops": [],
                    "normal": unit_normals[t_idx],
                })
            continue

        # Her sınır köşesinin giriş/çıkış derecesi tam 1 olmalı (basit ayrık döngüler)
        next_v: Dict[int, int] = {}
        in_deg: Dict[int, int] = defaultdict(int)
        degree_ok = True
        for u, v in edge_set:
            if u in next_v:
                degree_ok = False
                break
            next_v[u] = v
            in_deg[v] += 1
            if in_deg[v] > 1:
                degree_ok = False
                break

        if not degree_ok or any(in_deg.get(u, 0) != 1 for u in next_v):
            for t_idx in cluster:
                valid_clusters.append({
                    "merged": False,
                    "tris": [t_idx],
                    "outer_loop": [int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])],
                    "inner_loops": [],
                    "normal": unit_normals[t_idx],
                })
            continue

        # Döngüleri takip et
        loops: List[List[int]] = []
        unvisited_verts = set(next_v.keys())
        while unvisited_verts:
            start = next(iter(unvisited_verts))
            loop = []
            curr = start
            while curr in unvisited_verts:
                unvisited_verts.remove(curr)
                loop.append(curr)
                curr = next_v[curr]
            if curr != start or len(loop) < 3:
                degree_ok = False
                break
            loops.append(loop)

        if not degree_ok or not loops:
            for t_idx in cluster:
                valid_clusters.append({
                    "merged": False,
                    "tris": [t_idx],
                    "outer_loop": [int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])],
                    "inner_loops": [],
                    "normal": unit_normals[t_idx],
                })
            continue

        # Alan ağırlıklı ortalama birim normal
        weighted_n = np.zeros(3, dtype=np.float64)
        for t_idx in cluster:
            weighted_n += areas[t_idx] * unit_normals[t_idx]
        wn_norm = float(np.linalg.norm(weighted_n))
        avg_normal = weighted_n / wn_norm if wn_norm > 1e-12 else unit_normals[cluster[0]]
        u_axis, v_axis = _orthogonal_basis(avg_normal)

        # 2B işaretli alanlarla dış döngü (outer_loop > 0) ve delik döngülerini (inner_loops < 0) ayır
        outer_loops = []
        inner_loops = []
        for lp in loops:
            pts_3d = vertices[lp]
            x2d = pts_3d @ u_axis
            y2d = pts_3d @ v_axis
            # Shoelace formülü
            signed_area = 0.5 * float(np.sum(x2d * np.roll(y2d, -1) - np.roll(x2d, -1) * y2d))
            if signed_area > 1e-9:
                outer_loops.append((signed_area, lp))
            elif signed_area < -1e-9:
                inner_loops.append(lp)
            else:
                degree_ok = False
                break

        if not degree_ok or len(outer_loops) != 1:
            # Birden fazla dış ada veya bozuk alan varsa güvenle üçgenlere geri dön
            for t_idx in cluster:
                valid_clusters.append({
                    "merged": False,
                    "tris": [t_idx],
                    "outer_loop": [int(faces[t_idx, 0]), int(faces[t_idx, 1]), int(faces[t_idx, 2])],
                    "inner_loops": [],
                    "normal": unit_normals[t_idx],
                })
            continue

        valid_clusters.append({
            "merged": True,
            "tris": cluster,
            "outer_loop": outer_loops[0][1],
            "inner_loops": inner_loops,
            "normal": avg_normal,
        })

    # Şimdi komşu iki birleştirilmiş yüzey arasındaki doğrusal (collinear) ara köşeleri
    # her iki yüzeyde simetrik olarak sadeleştir (T-junction oluşmasını %100 engeller)
    tri_to_cluster: Dict[int, int] = {}
    for c_idx, c_data in enumerate(valid_clusters):
        for t_idx in c_data["tris"]:
            tri_to_cluster[t_idx] = c_idx

    # Her sınır köşesinin kaç farklı kümeye değdiğini hesapla
    vert_clusters: Dict[int, set] = defaultdict(set)
    for c_idx, c_data in enumerate(valid_clusters):
        for v in c_data["outer_loop"]:
            vert_clusters[v].add(c_idx)
        for ilp in c_data["inner_loops"]:
            for v in ilp:
                vert_clusters[v].add(c_idx)

    def simplify_collinear_loop(loop: List[int], current_c_idx: int) -> List[int]:
        if len(loop) <= 3:
            return loop
        pts = loop[:]
        changed = True
        while changed and len(pts) > 3:
            changed = False
            n_pts = len(pts)
            for i in range(n_pts):
                u = pts[(i - 1) % n_pts]
                w = pts[i]
                v = pts[(i + 1) % n_pts]
                if u == v:
                    continue
                # Köşe w yalnızca 2 birleşik kümeye (veya açık sınıra) ait olmalı
                touching = vert_clusters[w]
                if len(touching) > 2:
                    continue
                if not all(valid_clusters[cid]["merged"] for cid in touching):
                    continue
                # u -> w -> v doğrusal mı?
                pu, pw, pv = vertices[u], vertices[w], vertices[v]
                vec1 = pw - pu
                vec2 = pv - pw
                len1 = float(np.linalg.norm(vec1))
                len2 = float(np.linalg.norm(vec2))
                if len1 < 1e-9 or len2 < 1e-9:
                    continue
                cos_ang = float(np.dot(vec1, vec2)) / (len1 * len2)
                if cos_ang < 0.99995:  # ~0.57 derece
                    continue
                # w noktasının u-v doğrusuna dik uzaklığı
                uv = pv - pu
                uv_len = float(np.linalg.norm(uv))
                if uv_len < 1e-9:
                    continue
                dist_line = float(np.linalg.norm(np.cross(pw - pu, uv))) / uv_len
                if dist_line <= 1e-4:
                    pts.pop(i)
                    changed = True
                    break
        return pts

    planar_faces: List[PlanarFace] = []
    for c_idx, c_data in enumerate(valid_clusters):
        n_vec = c_data["normal"]
        u_vec, _ = _orthogonal_basis(n_vec)
        if c_data["merged"]:
            outer_lp = simplify_collinear_loop(c_data["outer_loop"], c_idx)
            inner_lps = [simplify_collinear_loop(ilp, c_idx) for ilp in c_data["inner_loops"]]
        else:
            outer_lp = c_data["outer_loop"]
            inner_lps = []
        orig = vertices[outer_lp[0]]
        planar_faces.append(
            PlanarFace(
                outer_loop=outer_lp,
                inner_loops=inner_lps,
                normal=(float(n_vec[0]), float(n_vec[1]), float(n_vec[2])),
                ref_dir=(float(u_vec[0]), float(u_vec[1]), float(u_vec[2])),
                origin=(float(orig[0]), float(orig[1]), float(orig[2])),
            )
        )

    return planar_faces


# ---------------------------------------------------------------------------
# 4. ISO-10303-21 STEP (AP214 AUTOMOTIVE DESIGN) YAZICI
# ---------------------------------------------------------------------------

def _fmt_float(val: float) -> str:
    if abs(val) < 1e-12:
        return "0."
    s = f"{val:.7f}".rstrip("0")
    if s.endswith("."):
        return s
    return s


def _to_ascii_identifier(text: str) -> str:
    """Türkçe ve Unicode karakterleri STEP standardına uygun 7-bit ASCII tanımlayıcıya dönüştürür."""
    tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    mapped = text.translate(tr_map)
    cleaned = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in ("_", "-"))) else "_" for ch in mapped)
    cleaned = cleaned.strip("_")
    return cleaned or "Bambu_Part"


def export_step_ap214(
    vertices: np.ndarray,
    planar_faces: List[PlanarFace],
    output_path: str,
    part_name: str = "Bambu_Part",
    unit: str = "mm",
    step_mode: str = "advanced_brep",
) -> None:
    """
    Analitik düzlemsel yüzeyleri (PlanarFace) içeren ISO-10303-21 STEP AP214 dosyası üretir.
    - step_mode="advanced_brep": ADVANCED_BREP_SHAPE_REPRESENTATION (ADVANCED_FACE + PLANE + EDGE_CURVE + LINE)
      SolidWorks'te her düz yüzeyde doğrudan 2B çizim (Sketch) açılmasına ve kenar ölçümü yapılmasına olanak tanır.
    - step_mode="faceted_brep": FACETED_BREP (POLY_LOOP + FACE_SURFACE + PLANE)
      Çok yüksek yüzeyli modellerde daha küçük dosya boyutu ve hızlı SolidWorks içe aktarımı sağlar.
    """
    safe_part_name = _to_ascii_identifier(part_name)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    lines: List[str] = []
    entity_id = 0

    def add_entity(text: str) -> int:
        nonlocal entity_id
        entity_id += 1
        lines.append(f"#{entity_id}={text};")
        return entity_id

    # 1. Uygulama ve Ürün Tanımı (AP214 Automotive Design)
    app_ctx = add_entity("APPLICATION_CONTEXT('automotive_design')")
    add_entity(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{app_ctx})")
    prod_ctx = add_entity(f"PRODUCT_CONTEXT('',#${app_ctx},'mechanical')".replace("#$", "#"))
    prod = add_entity(f"PRODUCT('{safe_part_name}','{safe_part_name}','',(#{prod_ctx}))")
    pdf = add_entity(f"PRODUCT_DEFINITION_FORMATION('','',#{prod})")
    pdc = add_entity(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{app_ctx},'design')")
    pd = add_entity(f"PRODUCT_DEFINITION('design','',#{pdf},#{pdc})")
    pds = add_entity(f"PRODUCT_DEFINITION_SHAPE('','',#{pd})")

    # 2. Birim ve Geometrik Bağlam (mm, radyan, steradyan)
    if unit == "cm":
        len_unit = add_entity("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.CENTI.,.METRE.))")
    elif unit == "m":
        len_unit = add_entity("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.))")
    else:
        len_unit = add_entity("(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT(.MILLI.,.METRE.))")

    ang_unit = add_entity("(NAMED_UNIT(*)PLANE_ANGLE_UNIT()SI_UNIT($,.RADIAN.))")
    solid_ang_unit = add_entity("(NAMED_UNIT(*)SOLID_ANGLE_UNIT()SI_UNIT($,.STERADIAN.))")
    uncert = add_entity(
        f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-05),#{len_unit},'distance_accuracy_value','confusion accuracy')"
    )
    geom_ctx = add_entity(
        f"(GEOMETRIC_REPRESENTATION_CONTEXT(3)GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{uncert}))"
        f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{len_unit},#{ang_unit},#{solid_ang_unit}))"
        f"REPRESENTATION_CONTEXT('Context #1','3D Context with UNIT and UNCERTAINTY'))"
    )

    # Dünya eksen takımı
    origin_pt = add_entity("CARTESIAN_POINT('',(0.,0.,0.))")
    dir_z = add_entity("DIRECTION('',(0.,0.,1.))")
    dir_x = add_entity("DIRECTION('',(1.,0.,0.))")
    world_axis = add_entity(f"AXIS2_PLACEMENT_3D('',#{origin_pt},#{dir_z},#{dir_x})")

    # Yalnızca kullanılan köşeleri kaydet
    used_verts = set()
    for pf in planar_faces:
        used_verts.update(pf.outer_loop)
        for ilp in pf.inner_loops:
            used_verts.update(ilp)

    cart_pt_ids: Dict[int, int] = {}
    vert_pt_ids: Dict[int, int] = {}
    # 1500 yüzeyden büyük organik/yüksek poligonlu modellerde SolidWorks'ün hızlıca
    # Katı Gövde oluşturabilmesi için otomatik olarak FACETED_BREP modunu kullan
    use_advanced = (step_mode.lower() != "faceted_brep") and (len(planar_faces) <= 1500)

    for v_idx in sorted(used_verts):
        vx, vy, vz = float(vertices[v_idx, 0]), float(vertices[v_idx, 1]), float(vertices[v_idx, 2])
        cp_id = add_entity(f"CARTESIAN_POINT('',({_fmt_float(vx)},{_fmt_float(vy)},{_fmt_float(vz)}))")
        cart_pt_ids[v_idx] = cp_id
        if use_advanced:
            vp_id = add_entity(f"VERTEX_POINT('',#{cp_id})")
            vert_pt_ids[v_idx] = vp_id

    face_entity_ids: List[int] = []

    if use_advanced:
        # Paylaşılan yönsüz kenarlar: (min_v, max_v) -> edge_curve_id
        edge_curve_cache: Dict[Tuple[int, int], int] = {}

        def get_edge_curve(u: int, v: int) -> Tuple[int, bool]:
            if u < v:
                key = (u, v)
                orient = True
            else:
                key = (v, u)
                orient = False
            ec_id = edge_curve_cache.get(key)
            if ec_id is None:
                v_start, v_end = key
                p_start = vertices[v_start]
                p_end = vertices[v_end]
                diff = p_end - p_start
                length = float(np.linalg.norm(diff))
                if length > 1e-12:
                    d_vec = diff / length
                else:
                    d_vec = np.array([1.0, 0.0, 0.0], dtype=np.float64)
                dir_id = add_entity(
                    f"DIRECTION('',({_fmt_float(float(d_vec[0]))},{_fmt_float(float(d_vec[1]))},{_fmt_float(float(d_vec[2]))}))"
                )
                vec_id = add_entity(f"VECTOR('',#{dir_id},1.)")
                line_id = add_entity(f"LINE('',#{cart_pt_ids[v_start]},#{vec_id})")
                ec_id = add_entity(f"EDGE_CURVE('',#{vert_pt_ids[v_start]},#{vert_pt_ids[v_end]},#{line_id},.T.)")
                edge_curve_cache[key] = ec_id
            return ec_id, orient

        def build_edge_loop(loop_verts: List[int]) -> int:
            oriented_edges = []
            n_lv = len(loop_verts)
            for i in range(n_lv):
                u = loop_verts[i]
                v = loop_verts[(i + 1) % n_lv]
                if u == v:
                    continue
                ec_id, is_fwd = get_edge_curve(u, v)
                sense = ".T." if is_fwd else ".F."
                oe_id = add_entity(f"ORIENTED_EDGE('',*,*,#{ec_id},{sense})")
                oriented_edges.append(f"#{oe_id}")
            return add_entity(f"EDGE_LOOP('',({','.join(oriented_edges)}))")

        for pf in planar_faces:
            if len(pf.outer_loop) < 3:
                continue
            outer_el = build_edge_loop(pf.outer_loop)
            fob_id = add_entity(f"FACE_OUTER_BOUND('',#{outer_el},.T.)")
            bounds = [f"#{fob_id}"]
            for ilp in pf.inner_loops:
                if len(ilp) >= 3:
                    inner_el = build_edge_loop(ilp)
                    fb_id = add_entity(f"FACE_BOUND('',#{inner_el},.T.)")
                    bounds.append(f"#{fb_id}")

            nx, ny, nz = pf.normal
            rx, ry, rz = pf.ref_dir
            ndir_id = add_entity(f"DIRECTION('',({_fmt_float(nx)},{_fmt_float(ny)},{_fmt_float(nz)}))")
            rdir_id = add_entity(f"DIRECTION('',({_fmt_float(rx)},{_fmt_float(ry)},{_fmt_float(rz)}))")
            ax_id = add_entity(f"AXIS2_PLACEMENT_3D('',#{cart_pt_ids[pf.outer_loop[0]]},#{ndir_id},#{rdir_id})")
            plane_id = add_entity(f"PLANE('',#{ax_id})")
            af_id = add_entity(f"ADVANCED_FACE('',({','.join(bounds)}),#{plane_id},.T.)")
            face_entity_ids.append(af_id)

        shell_faces_str = ",".join(f"#{fid}" for fid in face_entity_ids)
        closed_shell = add_entity(f"CLOSED_SHELL('',({shell_faces_str}))")
        solid_brep = add_entity(f"MANIFOLD_SOLID_BREP('{safe_part_name}',#{closed_shell})")
        shape_rep = add_entity(
            f"ADVANCED_BREP_SHAPE_REPRESENTATION('{safe_part_name}',(#{solid_brep},#{world_axis}),#{geom_ctx})"
        )
    else:
        for pf in planar_faces:
            if len(pf.outer_loop) < 3:
                continue
            pts_str = ",".join(f"#{cart_pt_ids[v]}" for v in pf.outer_loop)
            pl_id = add_entity(f"POLY_LOOP('',({pts_str}))")
            fob_id = add_entity(f"FACE_OUTER_BOUND('',#{pl_id},.T.)")
            bounds = [f"#{fob_id}"]
            for ilp in pf.inner_loops:
                if len(ilp) >= 3:
                    ipts_str = ",".join(f"#{cart_pt_ids[v]}" for v in ilp)
                    ipl_id = add_entity(f"POLY_LOOP('',({ipts_str}))")
                    fb_id = add_entity(f"FACE_BOUND('',#{ipl_id},.T.)")
                    bounds.append(f"#{fb_id}")

            nx, ny, nz = pf.normal
            rx, ry, rz = pf.ref_dir
            ndir_id = add_entity(f"DIRECTION('',({_fmt_float(nx)},{_fmt_float(ny)},{_fmt_float(nz)}))")
            rdir_id = add_entity(f"DIRECTION('',({_fmt_float(rx)},{_fmt_float(ry)},{_fmt_float(rz)}))")
            ax_id = add_entity(f"AXIS2_PLACEMENT_3D('',#{cart_pt_ids[pf.outer_loop[0]]},#{ndir_id},#{rdir_id})")
            plane_id = add_entity(f"PLANE('',#{ax_id})")
            fs_id = add_entity(f"FACE_SURFACE('',({','.join(bounds)}),#{plane_id},.T.)")
            face_entity_ids.append(fs_id)

        shell_faces_str = ",".join(f"#{fid}" for fid in face_entity_ids)
        closed_shell = add_entity(f"CLOSED_SHELL('',({shell_faces_str}))")
        faceted_brep = add_entity(f"FACETED_BREP('{safe_part_name}',#{closed_shell})")
        shape_rep = add_entity(
            f"FACETED_BREP_SHAPE_REPRESENTATION('{safe_part_name}',(#{faceted_brep},#{world_axis}),#{geom_ctx})"
        )

    add_entity(f"SHAPE_DEFINITION_REPRESENTATION(#{pds},#{shape_rep})")

    header = f"""ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('Bambu/STL to SolidWorks STEP AP214 Converter'),'2;1');
FILE_NAME('{safe_part_name}.step','{timestamp}',('User'),('Bambu2SolidWorks'),'Bambu2SolidWorks Engine 1.0','SolidWorks','');
FILE_SCHEMA(('AUTOMOTIVE_DESIGN {{ 1 0 10303 214 1 1 1 1 }}'));
ENDSEC;
DATA;
"""
    footer = "\nENDSEC;\nEND-ISO-10303-21;\n"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(lines))
        f.write(footer)


# ---------------------------------------------------------------------------
# 5. SOLIDWORKS COM API ENTEGRASYONU (.SLDPRT VE .PRT ÜRETİMİ + DOĞRUDAN AÇMA)
# ---------------------------------------------------------------------------

def convert_with_solidworks(
    step_path: str,
    stl_source_path: Optional[str],
    out_sldprt_path: Optional[str],
    out_prt_path: Optional[str],
    open_after: bool = False,
    import_mode: str = "step_brep",
    fallback_mesh: Optional[trimesh.Trimesh] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Bilgisayarda kurulu olan SolidWorks (SldWorks.Application) COM bağlantısını kullanarak:
    - Türkçe / Çince / Unicode karakterli dosya yollarında SolidWorks ANSI okuyucusunun hata vermemesi için
      geçici ASCII çalışma yolu (TEMP) üzerinden .STEP dosyasını SolidWorks'e aktarır.
    - Yerel .SLDPRT ve .PRT formatlarında kaydeder ve hedef yola taşır.
    - open_after=True ise SolidWorks penceresini görünür yapıp modeli ekrana odaklar.
    """
    import tempfile
    saved: Dict[str, str] = {}
    warnings: List[str] = []

    try:
        import pythoncom
        import win32com.client
        from win32com.client import VARIANT
    except ImportError:
        warnings.append("pywin32 kurulu olmadığı için SolidWorks COM otomasyonu çalıştırılamadı.")
        return saved, warnings

    pythoncom.CoInitialize()
    tmp_dir = Path(tempfile.gettempdir())
    uid = f"bambu2sw_{os.getpid()}_{int(time.time() * 1000) % 100000}"
    tmp_step = tmp_dir / f"{uid}.step"
    tmp_stl = tmp_dir / f"{uid}.stl"
    tmp_sldprt = tmp_dir / f"{uid}.sldprt"
    tmp_prt = tmp_dir / f"{uid}.prt"

    try:
        sw_app = win32com.client.Dispatch("SldWorks.Application")
        # 3D Interconnect'i geçici olarak kapat (doğrudan düzenlenebilir B-Rep gövde oluşması için)
        SW_PREF_3D_INTERCONNECT = 469
        orig_3di = False
        try:
            orig_3di = bool(sw_app.GetUserPreferenceToggle(SW_PREF_3D_INTERCONNECT))
            if orig_3di:
                sw_app.SetUserPreferenceToggle(SW_PREF_3D_INTERCONNECT, False)
        except Exception:
            pass

        # Arka planda uyarı pencerelerini bastır
        try:
            sw_app.CommandInProgress = True
        except Exception:
            pass

        abs_step = str(Path(step_path).resolve())
        shutil.copy2(abs_step, str(tmp_step))
        model_doc = None

        # 1. Aşama: ASCII geçici yoldaki STEP dosyasını SolidWorks'e yükle
        try:
            imp_var = VARIANT(pythoncom.VT_DISPATCH, None)
            err_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            model_doc = sw_app.LoadFile4(str(tmp_step), "r", imp_var, err_var)
        except Exception as e_load:
            warnings.append(f"SolidWorks STEP LoadFile4 uyarısı: {e_load}")

        # 2. Aşama (Garantili Yedek): Eğer STEP üzerinden yüklenemediyse Binary STL üzerinden yükle
        if model_doc is None and fallback_mesh is not None:
            try:
                fallback_mesh.export(str(tmp_stl))
                imp_var = VARIANT(pythoncom.VT_DISPATCH, None)
                err_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                model_doc = sw_app.LoadFile4(str(tmp_stl), "r", imp_var, err_var)
            except Exception as e_stl:
                warnings.append(f"SolidWorks STL yedek yükleme uyarısı: {e_stl}")

        if model_doc is not None:
            # .SLDPRT olarak kaydet (önce ASCII temp yola kaydet, sonra Unicode hedefe kopyala)
            if out_sldprt_path:
                abs_sldprt = str(Path(out_sldprt_path).resolve())
                Path(abs_sldprt).parent.mkdir(parents=True, exist_ok=True)
                try:
                    res = model_doc.SaveAs3(str(tmp_sldprt), 0, 1)
                except Exception:
                    try:
                        res = model_doc.SaveAs(str(tmp_sldprt))
                    except Exception as e_save:
                        res = -1
                        warnings.append(f"SLDPRT kaydetme uyarısı: {e_save}")

                if tmp_sldprt.exists():
                    shutil.copy2(str(tmp_sldprt), abs_sldprt)
                    saved["sldprt"] = abs_sldprt
                else:
                    warnings.append(f".sldprt dosyası kaydedilemedi (SolidWorks dönüş kodu: {res}).")

            # .PRT olarak kaydet
            if out_prt_path:
                abs_prt = str(Path(out_prt_path).resolve())
                Path(abs_prt).parent.mkdir(parents=True, exist_ok=True)
                try:
                    model_doc.SaveAs3(str(tmp_prt), 0, 1)
                except Exception:
                    try:
                        model_doc.SaveAs(str(tmp_prt))
                    except Exception:
                        pass

                if tmp_prt.exists():
                    shutil.copy2(str(tmp_prt), abs_prt)
                elif out_sldprt_path and Path(out_sldprt_path).exists():
                    shutil.copy2(out_sldprt_path, abs_prt)
                elif Path(abs_step).exists():
                    shutil.copy2(abs_step, abs_prt)

                if Path(abs_prt).exists():
                    saved["prt"] = abs_prt

            # Arka plandaki geçici belgeyi kapat
            try:
                doc_title = model_doc.GetTitle() if callable(getattr(model_doc, "GetTitle", None)) else model_doc.GetTitle
                sw_app.CloseDoc(doc_title)
            except Exception:
                pass

            if open_after:
                target_open = saved.get("sldprt") or abs_step
                sw_app.Visible = True
                try:
                    err_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    warn_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
                    opened_doc = sw_app.OpenDoc6(target_open, 1, 0, "", err_var, warn_var)
                    if opened_doc:
                        opened_doc.Visible = True
                        opened_doc.ViewZoomtofit2()
                except Exception:
                    pass
        else:
            warnings.append("SolidWorks modeli arka planda yükleyemedi, ancak .STEP dosyası başarıyla üretildi.")
            if out_prt_path and Path(abs_step).exists():
                abs_prt = str(Path(out_prt_path).resolve())
                shutil.copy2(abs_step, abs_prt)
                saved["prt"] = abs_prt

        # 3D Interconnect ayarını eski haline döndür
        try:
            if orig_3di:
                sw_app.SetUserPreferenceToggle(SW_PREF_3D_INTERCONNECT, True)
            sw_app.CommandInProgress = False
        except Exception:
            pass

    except Exception as exc:
        warnings.append(f"SolidWorks bağlantısı sırasında hata: {exc}")
        if out_prt_path and Path(step_path).exists() and not Path(out_prt_path).exists():
            shutil.copy2(step_path, out_prt_path)
            saved["prt"] = str(Path(out_prt_path).resolve())
    finally:
        for tf in (tmp_step, tmp_stl, tmp_sldprt, tmp_prt):
            try:
                if tf.exists():
                    tf.unlink()
            except Exception:
                pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    return saved, warnings


def open_file_in_solidworks(file_path: str) -> Tuple[bool, str]:
    """Verilen .sldprt veya .step dosyasını SolidWorks'ü görünür yaparak ekranda açar."""
    try:
        import pythoncom
        import win32com.client
        from win32com.client import VARIANT
    except ImportError:
        return False, "pywin32 kütüphanesi bulunamadı."

    pythoncom.CoInitialize()
    try:
        abs_path = str(Path(file_path).resolve())
        if not Path(abs_path).exists():
            return False, f"Dosya bulunamadı: {abs_path}"

        sw_app = win32com.client.Dispatch("SldWorks.Application")
        sw_app.Visible = True
        ext = Path(abs_path).suffix.lower()

        if ext in (".sldprt", ".prt"):
            err_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            warn_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            doc = sw_app.OpenDoc6(abs_path, 1, 0, "", err_var, warn_var)
        else:
            imp_var = VARIANT(pythoncom.VT_DISPATCH, None)
            err_var = VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
            doc = sw_app.LoadFile4(abs_path, "r", imp_var, err_var)

        if doc is not None:
            try:
                doc.Visible = True
                doc.ViewZoomtofit2()
            except Exception:
                pass
            return True, f"{Path(abs_path).name} SolidWorks'te açıldı!"
        return False, "Dosya SolidWorks'te açılamadı."
    except Exception as e:
        return False, f"SolidWorks açma hatası: {e}"
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 6. ANA DÖNÜŞTÜRME HATTI (PIPELINE)
# ---------------------------------------------------------------------------

def convert_cad_file(
    input_path: str,
    output_dir: Optional[str] = None,
    options: Optional[ConversionOptions] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> ConversionResult:
    """
    Tek bir .stl / .3mf / .obj dosyasını alır ve seçilen ayarlara göre
    .step, .sldprt ve .prt dosyalarına dönüştürür.
    """
    t0 = time.perf_counter()
    if options is None:
        options = ConversionOptions()

    def notify(msg: str, pct: float):
        if progress_callback:
            try:
                progress_callback(msg, pct)
            except Exception:
                pass

    in_path = Path(input_path).resolve()
    if not in_path.exists():
        return ConversionResult(
            input_file=str(in_path),
            success=False,
            message=f"Girdi dosyası bulunamadı: {in_path}",
        )

    out_folder = Path(output_dir).resolve() if output_dir else in_path.parent
    out_folder.mkdir(parents=True, exist_ok=True)
    stem = in_path.stem

    try:
        notify(f"📂 '{in_path.name}' okunuyor...", 0.10)
        mesh = load_mesh_file(str(in_path))
        orig_tris = len(mesh.faces)

        notify(f"🔧 Mesh temizleniyor ({orig_tris:,} üçgen)...", 0.25)
        # Çok yüksek poligonlu organik modellerde SolidWorks'ün hızlıca .SLDPRT katı/yüzey gövdesi
        # oluşturabilmesi için hedef üçgen sayısını optimize et
        effective_max_faces = options.max_faces
        if orig_tris > 6000 and (options.export_sldprt or options.export_prt):
            effective_max_faces = min(options.max_faces if options.max_faces > 0 else 4500, 4500)

        vertices, faces, is_watertight = clean_and_prepare_mesh(mesh, max_faces=effective_max_faces)
        proc_tris = len(faces)

        if options.merge_coplanar:
            notify(f"📐 Düzlemsel yüzeyler birleştiriliyor ({proc_tris:,} üçgen)...", 0.45)
        else:
            notify(f"📐 Yüzey topolojisi hazırlanıyor ({proc_tris:,} üçgen)...", 0.45)

        planar_faces = build_planar_faces(
            vertices,
            faces,
            merge_coplanar=options.merge_coplanar,
            angle_tol_deg=options.coplanar_angle_deg,
        )
        exp_faces = len(planar_faces)

        step_path = out_folder / f"{stem}.step"
        sldprt_path = out_folder / f"{stem}.sldprt" if options.export_sldprt else None
        prt_path = out_folder / f"{stem}.prt" if options.export_prt else None

        output_files: Dict[str, str] = {}
        warnings: List[str] = []

        # STEP dosyasını her zaman üret (SLDPRT ve PRT üretimi için de temel teşkil eder)
        notify(f"💾 STEP (AP214) dosyası yazılıyor ({exp_faces:,} CAD yüzeyi)...", 0.65)
        export_step_ap214(
            vertices=vertices,
            planar_faces=planar_faces,
            output_path=str(step_path),
            part_name=stem,
            unit=options.unit,
            step_mode=options.step_mode,
        )
        if options.export_step:
            output_files["step"] = str(step_path)

        # SolidWorks (.sldprt / .prt) dönüşümü
        if options.export_sldprt or options.export_prt or options.open_in_solidworks_after:
            notify("⚙️ SolidWorks üzerinden .SLDPRT ve .PRT oluşturuluyor...", 0.82)
            fallback_trimesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            sw_saved, sw_warns = convert_with_solidworks(
                step_path=str(step_path),
                stl_source_path=str(in_path) if in_path.suffix.lower() == ".stl" else None,
                out_sldprt_path=str(sldprt_path) if sldprt_path else None,
                out_prt_path=str(prt_path) if prt_path else None,
                open_after=options.open_in_solidworks_after,
                import_mode=options.solidworks_import_mode,
                fallback_mesh=fallback_trimesh,
            )
            output_files.update(sw_saved)
            warnings.extend(sw_warns)

        # Eğer kullanıcı sadece .sldprt/.prt istedi ve .step istemediyse geçici .step'i silebiliriz
        # ancak .step her zaman yedek olarak yararlıdır. Yine de seçeneğe saygı duyalım:
        if not options.export_step and step_path.exists() and ("sldprt" in output_files or "prt" in output_files):
            try:
                step_path.unlink()
            except Exception:
                pass

        elapsed = time.perf_counter() - t0
        notify("✅ Tamamlandı!", 1.0)

        reduction_pct = (1.0 - exp_faces / max(orig_tris, 1)) * 100.0
        msg = (
            f"Başarılı! {orig_tris:,} üçgen -> {exp_faces:,} CAD yüzeyine dönüştürüldü "
            f"(%{max(0.0, reduction_pct):.1f} yüzey sadeleştirmesi, {elapsed:.2f} sn)."
        )
        return ConversionResult(
            input_file=str(in_path),
            success=True,
            message=msg,
            original_triangles=orig_tris,
            processed_triangles=proc_tris,
            exported_faces=exp_faces,
            is_watertight=is_watertight,
            elapsed_sec=elapsed,
            output_files=output_files,
            warnings=warnings,
        )

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return ConversionResult(
            input_file=str(in_path),
            success=False,
            message=f"Hata: {exc}",
            elapsed_sec=elapsed,
        )
