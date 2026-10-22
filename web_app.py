r"""
Bambu Studio (.3MF / .STL) -> SolidWorks (.STEP / .SLDPRT / .PRT) Web Stüdyosu (Flask Backend)
==============================================================================================
Çalıştırmak için:
  .\.venv\Scripts\python.exe web_app.py
Tarayıcıda otomatik olarak http://127.0.0.1:5050 adresini açar.
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
import webbrowser
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import trimesh
from flask import Flask, jsonify, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
INPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# pythonw.exe (arka plan penceresiz mod) altında stdout/stderr None olduğunda
# Flask/Werkzeug loglarının çökmemesi için server.log dosyasına yönlendir
if sys.stdout is None or sys.stderr is None:
    _log_file = open(BASE_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_file
    if sys.stderr is None:
        sys.stderr = _log_file
elif hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from converter_engine import (
    ConversionOptions,
    convert_cad_file,
    load_mesh_file,
    open_file_in_solidworks,
)

app = Flask(
    __name__,
    template_folder=str(BASE_DIR / "templates"),
    static_folder=str(BASE_DIR / "static"),
)
app.config["MAX_CONTENT_LENGTH"] = 250 * 1024 * 1024  # 250 MB maksimum dosya boyutu


@app.after_request
def add_cors_and_pna_headers(response):
    """
    GitHub Pages (https://hamzasakci.github.io/bambu-to-solidworks/) üzerinden
    yerel SolidWorks sunucusuna (http://127.0.0.1:5050) doğrudan bağlanabilmesi için
    CORS ve Private Network Access (PNA) başlıklarını ekler.
    """
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        resp = app.make_default_options_response()
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
        resp.headers["Access-Control-Allow-Private-Network"] = "true"
        return resp


def ensure_demo_models() -> None:
    """Kullanıcının tek tıkla test edebilmesi için örnek mekanik Bambu parçaları oluşturur."""
    demo_stl = INPUT_DIR / "bambu_flans_adaptor.stl"
    if not demo_stl.exists():
        try:
            # Çokgen prizma + kademeli göbekten oluşan şık bir mekanik parça
            outer = trimesh.creation.cylinder(radius=28.0, height=8.0, sections=36)
            outer.apply_translation([0, 0, 4.0])
            hub = trimesh.creation.cylinder(radius=16.0, height=18.0, sections=36)
            hub.apply_translation([0, 0, 13.0])
            fin1 = trimesh.creation.box(extents=[52.0, 8.0, 14.0])
            fin1.apply_translation([0, 0, 7.0])
            fin2 = trimesh.creation.box(extents=[8.0, 52.0, 14.0])
            fin2.apply_translation([0, 0, 7.0])
            combined = trimesh.util.concatenate([outer, hub, fin1, fin2])
            combined.export(str(demo_stl))
        except Exception:
            pass


def build_mesh_preview_payload(file_path: Path) -> Dict[str, Any]:
    """
    Three.js WebGL 3B görüntüleyici için mesh köşe/üçgen dizilerini ve CAD ölçümlerini hazırlar.
    """
    mesh = load_mesh_file(str(file_path))
    orig_tri_count = int(len(mesh.faces))
    orig_vert_count = int(len(mesh.vertices))

    vertices = np.array(mesh.vertices, dtype=np.float64)
    faces = np.array(mesh.faces, dtype=np.int64)

    bbox_min = vertices.min(axis=0) if len(vertices) else np.zeros(3)
    bbox_max = vertices.max(axis=0) if len(vertices) else np.zeros(3)
    dims = bbox_max - bbox_min

    try:
        area_mm2 = float(mesh.area)
    except Exception:
        area_mm2 = 0.0

    try:
        vol_mm3 = float(abs(mesh.volume)) if mesh.is_watertight else 0.0
    except Exception:
        vol_mm3 = 0.0

    # WebGL akıcılığı için önizleme mesh'i çok büyükse sadeleştir (en fazla 35.000 üçgen)
    preview_faces = faces
    if len(preview_faces) > 35000:
        step = max(1, len(preview_faces) // 35000)
        preview_faces = preview_faces[::step]

    # Three.js BufferGeometry için düzleştirilmiş pozisyon listesi (x,y,z)
    # Merkeze hizalanmış koordinatlar
    center = 0.5 * (bbox_min + bbox_max)
    centered_v = np.round(vertices - center, 4)

    return {
        "filename": file_path.name,
        "filepath": str(file_path),
        "extension": file_path.suffix.lower().lstrip("."),
        "file_size_kb": round(file_path.stat().st_size / 1024.0, 1),
        "triangles": orig_tri_count,
        "vertices_count": orig_vert_count,
        "is_watertight": bool(mesh.is_watertight),
        "dimensions_mm": {
            "x": round(float(dims[0]), 2),
            "y": round(float(dims[1]), 2),
            "z": round(float(dims[2]), 2),
        },
        "surface_area_cm2": round(area_mm2 / 100.0, 2),
        "volume_cm3": round(vol_mm3 / 1000.0, 2),
        "mesh_data": {
            "vertices": centered_v.flatten().tolist(),
            "indices": preview_faces.flatten().tolist(),
        },
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/samples", methods=["GET"])
def list_samples():
    ensure_demo_models()
    supported = {".stl", ".3mf", ".obj", ".ply"}
    samples: List[Dict[str, Any]] = []
    for f in sorted(INPUT_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in supported:
            samples.append(
                {
                    "name": f.name,
                    "ext": f.suffix.lower().lstrip(".").upper(),
                    "size_kb": round(f.stat().st_size / 1024.0, 1),
                }
            )

    outputs: List[Dict[str, Any]] = []
    for f in sorted(OUTPUT_DIR.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if f.is_file() and f.suffix.lower() in {".step", ".stp", ".sldprt", ".prt", ".zip"}:
            outputs.append(
                {
                    "name": f.name,
                    "ext": f.suffix.lower().lstrip(".").upper(),
                    "size_kb": round(f.stat().st_size / 1024.0, 1),
                    "url": f"/api/download/{f.name}",
                }
            )

    return jsonify({"samples": samples, "outputs": outputs})


@app.route("/api/upload", methods=["POST"])
def upload_or_select_file():
    try:
        # 1. Hazır örnek dosya seçildiyse
        if request.is_json:
            data = request.get_json(silent=True) or {}
            sample_name = data.get("sample_name", "")
            if sample_name:
                target = (INPUT_DIR / Path(sample_name).name).resolve()
                if not target.exists():
                    return jsonify({"success": False, "error": f"Örnek dosya bulunamadı: {sample_name}"}), 404
                payload = build_mesh_preview_payload(target)
                return jsonify({"success": True, "file": payload})

        # 2. Yeni dosya yüklendiyse
        if "file" not in request.files:
            return jsonify({"success": False, "error": "Yüklenecek dosya bulunamadı."}), 400

        up_file = request.files["file"]
        if not up_file or not up_file.filename:
            return jsonify({"success": False, "error": "Geçersiz dosya adı."}), 400

        orig_name = Path(up_file.filename).name
        ext = Path(orig_name).suffix.lower()
        if ext not in {".stl", ".3mf", ".obj", ".ply"}:
            return jsonify(
                {"success": False, "error": f"Desteklenmeyen dosya uzantısı ({ext}). Lütfen .STL veya .3MF yükleyin."}
            ), 400

        raw_stem = Path(orig_name).stem
        tr_map = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
        safe_stem = secure_filename(raw_stem.translate(tr_map))
        if not safe_stem:
            import hashlib
            short_h = hashlib.md5(raw_stem.encode("utf-8", errors="ignore")).hexdigest()[:6]
            safe_stem = f"bambu_model_{short_h}"
        save_path = INPUT_DIR / f"{safe_stem}{ext}"
        up_file.save(str(save_path))

        payload = build_mesh_preview_payload(save_path)
        return jsonify({"success": True, "file": payload})

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/convert", methods=["POST"])
def convert_endpoint():
    try:
        data = request.get_json(force=True)
        filename = data.get("filename", "")
        if not filename:
            return jsonify({"success": False, "error": "Dönüştürülecek dosya belirtilmedi."}), 400

        in_path = (INPUT_DIR / Path(filename).name).resolve()
        if not in_path.exists():
            return jsonify({"success": False, "error": f"Girdi dosyası bulunamadı: {filename}"}), 404

        options = ConversionOptions(
            merge_coplanar=bool(data.get("merge_coplanar", True)),
            coplanar_angle_deg=float(data.get("coplanar_angle_deg", 1.0)),
            max_faces=int(data.get("max_faces", 25000)),
            unit=str(data.get("unit", "mm")),
            step_mode=str(data.get("step_mode", "advanced_brep")),
            export_step=bool(data.get("export_step", True)),
            export_sldprt=bool(data.get("export_sldprt", True)),
            export_prt=bool(data.get("export_prt", True)),
            open_in_solidworks_after=bool(data.get("open_in_solidworks", False)),
        )

        res = convert_cad_file(
            input_path=str(in_path),
            output_dir=str(OUTPUT_DIR),
            options=options,
        )

        if not res.success:
            return jsonify({"success": False, "error": res.message}), 500

        # Üretilen dosyalar için indirme bilgileri ve toplu ZIP paketi oluştur
        downloads: List[Dict[str, Any]] = []
        zip_name = f"{in_path.stem}_CAD_Paketi.zip"
        zip_path = OUTPUT_DIR / zip_name

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for fmt_key, f_str in res.output_files.items():
                p = Path(f_str)
                if p.exists():
                    zf.write(p, arcname=p.name)
                    downloads.append(
                        {
                            "format": fmt_key.upper(),
                            "filename": p.name,
                            "size_kb": round(p.stat().st_size / 1024.0, 1),
                            "url": f"/api/download/{p.name}",
                        }
                    )

        if len(downloads) > 1 and zip_path.exists():
            downloads.append(
                {
                    "format": "ZIP (TÜMÜ)",
                    "filename": zip_path.name,
                    "size_kb": round(zip_path.stat().st_size / 1024.0, 1),
                    "url": f"/api/download/{zip_path.name}",
                }
            )

        reduction_pct = round(max(0.0, (1.0 - res.exported_faces / max(res.original_triangles, 1)) * 100.0), 1)

        return jsonify(
            {
                "success": True,
                "message": res.message,
                "stats": {
                    "original_triangles": res.original_triangles,
                    "processed_triangles": res.processed_triangles,
                    "exported_faces": res.exported_faces,
                    "reduction_pct": reduction_pct,
                    "is_watertight": res.is_watertight,
                    "elapsed_sec": round(res.elapsed_sec, 2),
                },
                "downloads": downloads,
                "warnings": res.warnings,
            }
        )

    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/open-solidworks", methods=["POST"])
def api_open_solidworks():
    data = request.get_json(force=True)
    filename = data.get("filename", "")
    if not filename:
        return jsonify({"success": False, "error": "Dosya adı belirtilmedi."}), 400

    # Önce doğrudan adı dene, yoksa stem üzerinden .sldprt / .step ara
    cand = OUTPUT_DIR / Path(filename).name
    if not cand.exists():
        stem = Path(filename).stem
        for ext in (".SLDPRT", ".sldprt", ".step", ".PRT", ".prt"):
            alt = OUTPUT_DIR / f"{stem}{ext}"
            if alt.exists():
                cand = alt
                break

    if not cand.exists():
        return jsonify({"success": False, "error": "Dönüştürülmüş çıktı dosyası bulunamadı. Önce dönüştürmeyi başlatın."}), 404

    ok, msg = open_file_in_solidworks(str(cand))
    return jsonify({"success": ok, "message": msg})


@app.route("/api/open-folder", methods=["POST"])
def api_open_folder():
    try:
        os.startfile(str(OUTPUT_DIR))
        return jsonify({"success": True, "message": "Çıktı klasörü Windows Gezgini'nde açıldı."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/download/<path:filename>", methods=["GET"])
def api_download(filename: str):
    return send_from_directory(str(OUTPUT_DIR), filename, as_attachment=True)


def get_lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.route("/api/info", methods=["GET"])
def api_info():
    port = int(os.environ.get("PORT", 5050))
    lan_ip = get_lan_ip()
    return jsonify({
        "mode": "local_solidworks",
        "lan_url": f"http://{lan_ip}:{port}",
        "public_url": "https://hamzasakci.github.io/bambu-to-solidworks/",
        "github_repo": "https://github.com/HamzaSakci/bambu-to-solidworks",
        "download_zip": "https://github.com/HamzaSakci/bambu-to-solidworks/archive/refs/heads/main.zip",
    })


def main():
    ensure_demo_models()
    port = int(os.environ.get("PORT", 5050))
    lan_ip = get_lan_ip()
    local_url = f"http://127.0.0.1:{port}"
    lan_url = f"http://{lan_ip}:{port}"
    print("=" * 76)
    print(f"🌈 Bambu Studio -> SolidWorks Web Stüdyosu Başlatıldı!")
    print(f"💻 Bilgisayar Tarayıcı Adresi : {local_url}")
    print(f"📱 Telefon (Aynı Wi-Fi) Adresi: {lan_url}")
    print(f"🌍 Herkese Açık Web Adresi    : https://hamzasakci.github.io/bambu-to-solidworks/")
    print("=" * 76)

    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.0, lambda: webbrowser.open(local_url)).start()

    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
