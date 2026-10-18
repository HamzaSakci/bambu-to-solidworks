"""
Bambu Studio (.3mf / .stl) -> SolidWorks (.step / .sldprt / .prt) Masaüstü Stüdyosu (GUI)
=========================================================================================
Özellikler:
- Sürükle & Bırak (Drag & Drop) ile .STL ve Bambu Studio .3MF dosyalarını ekleme
- Fare ile döndürülebilen / yakınlaştırılabilen Etkileşimli 3B CAD Önizleme Ekranı
- Düzlemsel Yüzey Birleştirme (Coplanar Face Merging) ile SolidWorks'te çizim açılabilir yüzeyler
- Tek tıkla .STEP, .SLDPRT ve .PRT üretimi + doğrudan SolidWorks'te açma
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional

import customtkinter as ctk
import numpy as np

from converter_engine import (
    ConversionOptions,
    ConversionResult,
    convert_cad_file,
    load_mesh_file,
    open_file_in_solidworks,
)

# Sürükle-bırak desteği (tkinterdnd2 varsa etkinleştir)
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class Interactive3DViewer(tk.Canvas):
    """
    Seçilen .STL / .3MF modelini fare ile 360° döndürerek ve yakınlaştırarak
    incelemeyi sağlayan hızlı 3B CAD Önizleme Tuvali.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, bg="#141820", highlightthickness=1, highlightbackground="#2a3242", **kwargs)
        self.vertices: Optional[np.ndarray] = None
        self.faces: Optional[np.ndarray] = None
        self.face_normals: Optional[np.ndarray] = None
        self.yaw: float = math.radians(38.0)
        self.pitch: float = math.radians(26.0)
        self.zoom: float = 1.0
        self._last_mouse_x: int = 0
        self._last_mouse_y: int = 0
        self.part_info_text: str = "Önizleme için listeden bir .STL veya .3MF dosyası seçin"

        self.bind("<ButtonPress-1>", self._on_mouse_down)
        self.bind("<B1-Motion>", self._on_mouse_drag)
        self.bind("<MouseWheel>", self._on_mouse_wheel)
        self.bind("<Configure>", lambda e: self.redraw())

    def set_mesh(self, vertices: np.ndarray, faces: np.ndarray, title: str = ""):
        if len(vertices) == 0 or len(faces) == 0:
            self.vertices = None
            self.faces = None
            self.redraw()
            return

        # Görsel akıcılık için önizlemede maksimum 2.500 üçgen örnekle
        if len(faces) > 2500:
            step = max(1, len(faces) // 2500)
            sampled_faces = faces[::step]
        else:
            sampled_faces = faces

        # Merkeze hizala ve birim küreye ölçekle
        bbox_min = vertices.min(axis=0)
        bbox_max = vertices.max(axis=0)
        dims = bbox_max - bbox_min
        center = 0.5 * (bbox_min + bbox_max)
        max_span = float(np.max(dims))
        if max_span < 1e-9:
            max_span = 1.0

        norm_v = (vertices - center) / max_span
        v0 = norm_v[sampled_faces[:, 0]]
        v1 = norm_v[sampled_faces[:, 1]]
        v2 = norm_v[sampled_faces[:, 2]]
        cross = np.cross(v1 - v0, v2 - v0)
        lengths = np.linalg.norm(cross, axis=1, keepdims=True)
        lengths = np.where(lengths > 1e-12, lengths, 1.0)
        normals = cross / lengths

        self.vertices = norm_v
        self.faces = sampled_faces
        self.face_normals = normals
        self.zoom = 1.0
        self.part_info_text = (
            f"{title}  |  Boyut: {dims[0]:.1f} × {dims[1]:.1f} × {dims[2]:.1f} mm  |  "
            f"Üçgen: {len(faces):,}"
        )
        self.redraw()

    def _on_mouse_down(self, event):
        self._last_mouse_x = event.x
        self._last_mouse_y = event.y

    def _on_mouse_drag(self, event):
        dx = event.x - self._last_mouse_x
        dy = event.y - self._last_mouse_y
        self._last_mouse_x = event.x
        self._last_mouse_y = event.y
        self.yaw += dx * 0.012
        self.pitch = max(-math.pi / 2.1, min(math.pi / 2.1, self.pitch + dy * 0.012))
        self.redraw()

    def _on_mouse_wheel(self, event):
        if event.delta > 0:
            self.zoom = min(3.5, self.zoom * 1.12)
        else:
            self.zoom = max(0.35, self.zoom / 1.12)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = max(self.winfo_width(), 100)
        h = max(self.winfo_height(), 100)

        # Arka plan ızgara çizgileri
        cx, cy = w * 0.5, h * 0.52
        self.create_text(
            14,
            14,
            anchor="nw",
            text=self.part_info_text,
            fill="#94a3b8",
            font=("Segoe UI", 9, "bold"),
        )
        self.create_text(
            14,
            h - 12,
            anchor="sw",
            text="🖱️ Sol Tık Sürükle: 3B Döndür   |   Tekerlek: Yakınlaştır",
            fill="#64748b",
            font=("Segoe UI", 8),
        )

        if self.vertices is None or self.faces is None or self.face_normals is None:
            self.create_text(
                cx,
                cy,
                text="📦 Bambu (.3MF) veya .STL Dosyası Seçin / Sürükleyin",
                fill="#475569",
                font=("Segoe UI", 12, "bold"),
            )
            return

        # 3B -> 2B ortografik projeksiyon matrisi (Z-yukarı CAD görünümü)
        cyaw, syaw = math.cos(self.yaw), math.sin(self.yaw)
        cpit, spit = math.cos(self.pitch), math.sin(self.pitch)

        # İlk olarak Z ekseni etrafında yaw, sonra X ekseni etrafında pitch
        rot = np.array(
            [
                [cyaw, -syaw, 0.0],
                [syaw * spit, cyaw * spit, cpit],
                [syaw * cpit, cyaw * cpit, -spit],
            ],
            dtype=np.float64,
        )

        rv = self.vertices @ rot.T
        rn = self.face_normals @ rot.T

        scale = min(w, h) * 0.68 * self.zoom
        sx = cx + rv[:, 0] * scale
        sy = cy - rv[:, 1] * scale
        sz = rv[:, 2]

        f0 = self.faces[:, 0]
        f1 = self.faces[:, 1]
        f2 = self.faces[:, 2]

        # Ressam algoritması (Painter's Algorithm): derinliğe göre sırala
        tri_z = (sz[f0] + sz[f1] + sz[f2]) / 3.0
        order = np.argsort(tri_z)

        # Işık yönü
        light = np.array([0.35, 0.45, 0.82], dtype=np.float64)
        light /= np.linalg.norm(light)
        intensity = np.abs(rn @ light)

        x0, y0 = sx[f0], sy[f0]
        x1, y1 = sx[f1], sy[f1]
        x2, y2 = sx[f2], sy[f2]

        show_edges = len(self.faces) <= 1200
        for idx in order:
            val = float(intensity[idx])
            r = int(38 + val * 85)
            g = int(95 + val * 115)
            b = int(155 + val * 90)
            color = f"#{r:02x}{g:02x}{b:02x}"
            edge_color = "#1e293b" if show_edges else color
            self.create_polygon(
                float(x0[idx]),
                float(y0[idx]),
                float(x1[idx]),
                float(y1[idx]),
                float(x2[idx]),
                float(y2[idx]),
                fill=color,
                outline=edge_color,
            )


class BaseWindow(ctk.CTk, TkinterDnD.DnDWrapper if HAS_DND else object):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if HAS_DND:
            try:
                self.TkdndVersion = TkinterDnD._require(self)
            except Exception:
                pass


class BambuToSolidWorksApp(BaseWindow):
    def __init__(self):
        super().__init__()
        self.title("Bambu Studio (.3MF / .STL) ➔ SolidWorks (.STEP / .SLDPRT / .PRT) Dönüştürücü")
        self.geometry("1180x760")
        self.minsize(980, 660)

        self.file_queue: List[Path] = []
        self.converted_results: Dict[str, ConversionResult] = {}
        self.selected_file: Optional[Path] = None
        self.output_dir = Path("output").resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.is_converting = False

        self._build_ui()

        # Sürükle-bırak bağla
        if HAS_DND:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self._on_drop_files)
            except Exception:
                pass

        # Başlangıçta input/ klasöründe örnek dosya varsa yükle
        default_in = Path("input")
        if default_in.is_dir():
            initial = [
                p for p in sorted(default_in.iterdir())
                if p.is_file() and p.suffix.lower() in {".stl", ".3mf", ".obj", ".ply"}
            ]
            if initial:
                self._add_files(initial)

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=5)
        self.grid_columnconfigure(1, weight=7)
        self.grid_rowconfigure(1, weight=1)

        # ÜST BAŞLIK BARI
        header = ctk.CTkFrame(self, corner_radius=10, fg_color="#111827")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=14, pady=(12, 6))
        header.grid_columnconfigure(0, weight=1)

        title_lbl = ctk.CTkLabel(
            header,
            text="⚙️ Bambu Studio (.3MF / .STL) ➔ SolidWorks (.STEP / .SLDPRT / .PRT) Stüdyosu",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        title_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=(10, 2))

        sub_lbl = ctk.CTkLabel(
            header,
            text="Bambu Studio 3MF ve STL modellerini düzlemsel yüzeyleri birleştirerek SolidWorks'te çizim açılabilir Katı Gövdeye dönüştürür",
            font=ctk.CTkFont(size=12),
            text_color="#9ca3af",
        )
        sub_lbl.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        # SOL PANEL: DOSYA LİSTESİ VE AYARLAR
        left_panel = ctk.CTkFrame(self, corner_radius=10)
        left_panel.grid(row=1, column=0, sticky="nsew", padx=(14, 7), pady=6)
        left_panel.grid_columnconfigure(0, weight=1)
        left_panel.grid_rowconfigure(2, weight=1)

        # Dosya ekleme butonları
        btn_row = ctk.CTkFrame(left_panel, fg_color="transparent")
        btn_row.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        btn_row.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkButton(
            btn_row,
            text="➕ STL / 3MF Ekle",
            command=self._browse_files,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            height=34,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            btn_row,
            text="📁 Klasör Ekle",
            command=self._browse_folder,
            fg_color="#334155",
            hover_color="#475569",
            height=34,
        ).grid(row=0, column=1, sticky="ew", padx=4)

        ctk.CTkButton(
            btn_row,
            text="🗑️ Listeyi Temizle",
            command=self._clear_files,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            height=34,
        ).grid(row=0, column=2, sticky="ew", padx=(4, 0))

        dnd_hint = "💡 İpucu: Bambu (.3mf) veya (.stl) dosyalarını doğrudan bu pencereye sürükleyip bırakabilirsiniz."
        ctk.CTkLabel(
            left_panel,
            text=dnd_hint,
            font=ctk.CTkFont(size=11),
            text_color="#60a5fa",
        ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 4))

        # Kaydırılabilir dosya listesi
        self.file_scroll = ctk.CTkScrollableFrame(left_panel, label_text="📋 Dönüştürülecek Dosyalar (0)")
        self.file_scroll.grid(row=2, column=0, sticky="nsew", padx=12, pady=6)
        self.file_scroll.grid_columnconfigure(0, weight=1)

        # Dönüştürme Ayarları Çerçevesi
        settings_frame = ctk.CTkFrame(left_panel, corner_radius=8, fg_color="#1e293b")
        settings_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=(6, 12))
        settings_frame.grid_columnconfigure((0, 1, 2), weight=1)

        ctk.CTkLabel(
            settings_frame,
            text="🛠️ Çıktı Formatları ve CAD Geometri Ayarları",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(10, 6))

        self.var_step = ctk.BooleanVar(value=True)
        self.var_sldprt = ctk.BooleanVar(value=True)
        self.var_prt = ctk.BooleanVar(value=True)

        ctk.CTkCheckBox(settings_frame, text=".STEP (AP214)", variable=self.var_step).grid(
            row=1, column=0, sticky="w", padx=12, pady=4
        )
        ctk.CTkCheckBox(settings_frame, text=".SLDPRT (SolidWorks)", variable=self.var_sldprt).grid(
            row=1, column=1, sticky="w", padx=8, pady=4
        )
        ctk.CTkCheckBox(settings_frame, text=".PRT (CAD Part)", variable=self.var_prt).grid(
            row=1, column=2, sticky="w", padx=8, pady=4
        )

        self.var_merge_coplanar = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            settings_frame,
            text="Düzlemsel Üçgenleri Birleştir (SolidWorks'te Çizim Açılabilir Düz Yüzeyler)",
            variable=self.var_merge_coplanar,
        ).grid(row=2, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 4))

        # Maksimum yüzey sayısı ve açı toleransı
        ctk.CTkLabel(settings_frame, text="Maks. Üçgen Sınırı:", font=ctk.CTkFont(size=12)).grid(
            row=3, column=0, sticky="w", padx=12, pady=4
        )
        self.max_faces_menu = ctk.CTkOptionMenu(
            settings_frame,
            values=[
                "25000 (Önerilen - Katı Gövde)",
                "10000 (Hızlı / Hafif)",
                "50000 (Yüksek Detay)",
                "Sınırsız (Orijinal Mesh)",
            ],
            height=28,
        )
        self.max_faces_menu.grid(row=3, column=1, columnspan=2, sticky="ew", padx=12, pady=4)

        self.var_open_sw = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            settings_frame,
            text="İşlem Bitince Modeli Doğrudan SolidWorks Ekranında Aç",
            variable=self.var_open_sw,
            text_color="#34d399",
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=12, pady=(6, 8))

        # Çıktı klasörü seçimi
        out_row = ctk.CTkFrame(settings_frame, fg_color="transparent")
        out_row.grid(row=5, column=0, columnspan=3, sticky="ew", padx=12, pady=(2, 10))
        out_row.grid_columnconfigure(0, weight=1)

        self.out_entry = ctk.CTkEntry(out_row, height=28)
        self.out_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.out_entry.insert(0, str(self.output_dir))

        ctk.CTkButton(
            out_row,
            text="📂 Klasör Seç",
            width=95,
            height=28,
            command=self._choose_output_dir,
        ).grid(row=0, column=1, padx=(0, 4))

        ctk.CTkButton(
            out_row,
            text="↗️ Aç",
            width=55,
            height=28,
            fg_color="#475569",
            command=self._open_output_folder,
        ).grid(row=0, column=2)

        # SAĞ PANEL: 3B ÖNİZLEME VE LOG / AKSİYONLAR
        right_panel = ctk.CTkFrame(self, corner_radius=10)
        right_panel.grid(row=1, column=1, sticky="nsew", padx=(7, 14), pady=6)
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(0, weight=7)
        right_panel.grid_rowconfigure(1, weight=3)

        # 3B Etkileşimli Önizleme
        viewer_container = ctk.CTkFrame(right_panel, corner_radius=8)
        viewer_container.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 6))
        viewer_container.grid_columnconfigure(0, weight=1)
        viewer_container.grid_rowconfigure(0, weight=1)

        self.viewer = Interactive3DViewer(viewer_container)
        self.viewer.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)

        # Durum & Log konsolu
        log_frame = ctk.CTkFrame(right_panel, corner_radius=8)
        log_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_box = ctk.CTkTextbox(log_frame, font=ctk.CTkFont(family="Consolas", size=12), height=130)
        self.log_box.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        self._log("✨ Bambu Studio -> SolidWorks Dönüştürücü hazır.")
        self._log("📌 .3mf veya .stl dosyalarınızı ekleyip 'DÖNÜŞTÜRMEYİ BAŞLAT' butonuna tıklayın.")

        # Alt İlerleme Çubuğu ve Ana Aksiyon Butonları
        bottom_bar = ctk.CTkFrame(self, corner_radius=10, fg_color="#111827")
        bottom_bar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(4, 12))
        bottom_bar.grid_columnconfigure(0, weight=1)

        self.status_lbl = ctk.CTkLabel(
            bottom_bar,
            text="Hazır",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="#93c5fd",
        )
        self.status_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=(8, 2))

        self.progress_bar = ctk.CTkProgressBar(bottom_bar)
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 12))
        self.progress_bar.set(0.0)

        self.open_sw_btn = ctk.CTkButton(
            bottom_bar,
            text="🔧 Seçili Çıktıyı SolidWorks'te Aç",
            command=self._open_selected_in_sw,
            fg_color="#0f766e",
            hover_color="#115e59",
            height=42,
            width=220,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.open_sw_btn.grid(row=0, column=1, rowspan=2, padx=(8, 8), pady=10)

        self.convert_btn = ctk.CTkButton(
            bottom_bar,
            text="🚀 DÖNÜŞTÜRMEYİ BAŞLAT (.STEP / .SLDPRT / .PRT)",
            command=self._start_conversion,
            fg_color="#16a34a",
            hover_color="#15803d",
            height=42,
            width=320,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.convert_btn.grid(row=0, column=2, rowspan=2, padx=(4, 16), pady=10)

    def _log(self, text: str):
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def _on_drop_files(self, event):
        raw = event.data
        if not raw:
            return
        paths = self.tk.splitlist(raw)
        valid_paths: List[Path] = []
        for p in paths:
            po = Path(p)
            if po.is_dir():
                for child in sorted(po.iterdir()):
                    if child.suffix.lower() in {".stl", ".3mf", ".obj", ".ply"}:
                        valid_paths.append(child)
            elif po.is_file() and po.suffix.lower() in {".stl", ".3mf", ".obj", ".ply"}:
                valid_paths.append(po)
        if valid_paths:
            self._add_files(valid_paths)

    def _browse_files(self):
        selected = filedialog.askopenfilenames(
            title="Bambu Studio (.3mf) veya .STL Dosyalarını Seçin",
            filetypes=[
                ("Bambu & 3B Mesh Dosyaları", "*.stl *.3mf *.obj *.ply"),
                ("STL Dosyaları (*.stl)", "*.stl"),
                ("Bambu Studio 3MF (*.3mf)", "*.3mf"),
                ("Tüm Dosyalar", "*.*"),
            ],
        )
        if selected:
            self._add_files([Path(p) for p in selected])

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="İçinde STL / 3MF Bulunan Klasörü Seçin")
        if folder:
            found = [
                p for p in sorted(Path(folder).iterdir())
                if p.is_file() and p.suffix.lower() in {".stl", ".3mf", ".obj", ".ply"}
            ]
            if found:
                self._add_files(found)
            else:
                messagebox.showinfo("Bilgi", "Seçilen klasörde .stl veya .3mf dosyası bulunamadı.")

    def _clear_files(self):
        self.file_queue.clear()
        self.selected_file = None
        self._refresh_file_list()
        self.viewer.set_mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))

    def _add_files(self, new_paths: List[Path]):
        existing = {str(p.resolve()) for p in self.file_queue}
        added = 0
        for p in new_paths:
            res = str(p.resolve())
            if res not in existing and p.exists():
                self.file_queue.append(p.resolve())
                existing.add(res)
                added += 1
        if added > 0:
            self._log(f"➕ {added} dosya listeye eklendi.")
            self._refresh_file_list()
            if self.selected_file is None and self.file_queue:
                self._select_file(self.file_queue[0])

    def _refresh_file_list(self):
        for child in self.file_scroll.winfo_children():
            child.destroy()
        self.file_scroll.configure(label_text=f"📋 Dönüştürülecek Dosyalar ({len(self.file_queue)})")

        for idx, fpath in enumerate(self.file_queue):
            row = ctk.CTkFrame(
                self.file_scroll,
                fg_color="#2563eb" if self.selected_file == fpath else "#1e293b",
                corner_radius=6,
            )
            row.grid(row=idx, column=0, sticky="ew", padx=4, pady=3)
            row.grid_columnconfigure(0, weight=1)

            badge = "🟢" if str(fpath) in self.converted_results else "📄"
            btn = ctk.CTkButton(
                row,
                text=f"{badge} {fpath.name}  ({fpath.stat().st_size / 1024:.1f} KB)",
                anchor="w",
                fg_color="transparent",
                hover_color="#334155",
                command=lambda p=fpath: self._select_file(p),
            )
            btn.grid(row=0, column=0, sticky="ew", padx=4, pady=2)

            del_btn = ctk.CTkButton(
                row,
                text="✖",
                width=28,
                fg_color="transparent",
                hover_color="#991b1b",
                command=lambda p=fpath: self._remove_single_file(p),
            )
            del_btn.grid(row=0, column=1, padx=4, pady=2)

    def _remove_single_file(self, fpath: Path):
        self.file_queue = [p for p in self.file_queue if p != fpath]
        if self.selected_file == fpath:
            self.selected_file = self.file_queue[0] if self.file_queue else None
            if self.selected_file:
                self._select_file(self.selected_file)
            else:
                self.viewer.set_mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=int))
        self._refresh_file_list()

    def _select_file(self, fpath: Path):
        self.selected_file = fpath
        self._refresh_file_list()
        try:
            mesh = load_mesh_file(str(fpath))
            self.viewer.set_mesh(
                np.array(mesh.vertices, dtype=np.float64),
                np.array(mesh.faces, dtype=np.int64),
                title=fpath.name,
            )
        except Exception as e:
            self._log(f"⚠️ Önizleme yüklenemedi ({fpath.name}): {e}")

    def _choose_output_dir(self):
        d = filedialog.askdirectory(title="Çıktı Klasörünü Seçin", initialdir=str(self.output_dir))
        if d:
            self.output_dir = Path(d).resolve()
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, str(self.output_dir))

    def _open_output_folder(self):
        out_p = Path(self.out_entry.get().strip() or "output").resolve()
        out_p.mkdir(parents=True, exist_ok=True)
        os.startfile(str(out_p))

    def _open_selected_in_sw(self):
        target = None
        if self.selected_file and str(self.selected_file) in self.converted_results:
            res = self.converted_results[str(self.selected_file)]
            target = res.output_files.get("sldprt") or res.output_files.get("step") or res.output_files.get("prt")
        elif self.selected_file:
            out_p = Path(self.out_entry.get().strip() or "output").resolve()
            for ext in (".sldprt", ".step", ".prt"):
                cand = out_p / f"{self.selected_file.stem}{ext}"
                if cand.exists():
                    target = str(cand)
                    break

        if not target:
            messagebox.showwarning(
                "Henüz Dönüştürülmedi",
                "Lütfen önce dosyayı dönüştürün, ardından SolidWorks'te açabilirsiniz.",
            )
            return

        self._log(f"🔧 SolidWorks'te açılıyor: {Path(target).name}...")
        threading.Thread(target=self._open_sw_worker, args=(target,), daemon=True).start()

    def _open_sw_worker(self, target_path: str):
        ok, msg = open_file_in_solidworks(target_path)
        self.after(0, lambda: self._log(f"{'✅' if ok else '⚠️'} {msg}"))

    def _parse_max_faces(self) -> int:
        val = self.max_faces_menu.get()
        if "10000" in val:
            return 10000
        if "25000" in val:
            return 25000
        if "50000" in val:
            return 50000
        return 0

    def _start_conversion(self):
        if self.is_converting:
            return
        if not self.file_queue:
            messagebox.showwarning("Dosya Seçilmedi", "Lütfen önce en az bir .STL veya Bambu .3MF dosyası ekleyin.")
            return
        if not (self.var_step.get() or self.var_sldprt.get() or self.var_prt.get()):
            messagebox.showwarning("Format Seçilmedi", "Lütfen en az bir çıktı formatı (.STEP, .SLDPRT, .PRT) seçin.")
            return

        out_dir = Path(self.out_entry.get().strip() or "output").resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir = out_dir

        options = ConversionOptions(
            merge_coplanar=self.var_merge_coplanar.get(),
            coplanar_angle_deg=1.0,
            max_faces=self._parse_max_faces(),
            export_step=self.var_step.get(),
            export_sldprt=self.var_sldprt.get(),
            export_prt=self.var_prt.get(),
            open_in_solidworks_after=self.var_open_sw.get(),
        )

        self.is_converting = True
        self.convert_btn.configure(state="disabled", text="⏳ Dönüştürülüyor...")
        self.progress_bar.set(0.05)

        threading.Thread(
            target=self._conversion_worker,
            args=(list(self.file_queue), str(out_dir), options),
            daemon=True,
        ).start()

    def _conversion_worker(self, files: List[Path], out_dir: str, options: ConversionOptions):
        total = len(files)
        ok_count = 0

        for idx, fpath in enumerate(files):
            base_prog = idx / float(total)
            step_span = 1.0 / float(total)

            # Yalnızca son dosyada SolidWorks'ü ekranda açık bırak
            file_opts = ConversionOptions(
                merge_coplanar=options.merge_coplanar,
                coplanar_angle_deg=options.coplanar_angle_deg,
                max_faces=options.max_faces,
                unit=options.unit,
                step_mode=options.step_mode,
                export_step=options.export_step,
                export_sldprt=options.export_sldprt,
                export_prt=options.export_prt,
                open_in_solidworks_after=(options.open_in_solidworks_after and idx == total - 1),
            )

            def cb(msg: str, pct: float, b=base_prog, s=step_span, fn=fpath.name):
                overall = min(0.99, b + pct * s)
                self.after(0, lambda: self._update_progress(f"[{idx + 1}/{total}] {fn}: {msg}", overall))

            res = convert_cad_file(
                input_path=str(fpath),
                output_dir=out_dir,
                options=file_opts,
                progress_callback=cb,
            )

            if res.success:
                ok_count += 1
                self.converted_results[str(fpath)] = res
                out_list_str = ", ".join(f".{k.upper()}" for k in res.output_files.keys())
                self.after(
                    0,
                    lambda r=res, fn=fpath.name, ol=out_list_str: self._log(
                        f"✅ {fn}: {r.message} -> Üretilen: [{ol}]"
                    ),
                )
                for w in res.warnings:
                    self.after(0, lambda warn=w: self._log(f"   ℹ️ {warn}"))
            else:
                self.after(0, lambda r=res, fn=fpath.name: self._log(f"❌ {fn}: {r.message}"))

        self.after(0, lambda: self._finish_conversion(ok_count, total, out_dir))

    def _update_progress(self, text: str, pct: float):
        self.status_lbl.configure(text=text)
        self.progress_bar.set(pct)

    def _finish_conversion(self, ok_count: int, total: int, out_dir: str):
        self.is_converting = False
        self.convert_btn.configure(state="normal", text="🚀 DÖNÜŞTÜRMEYİ BAŞLAT (.STEP / .SLDPRT / .PRT)")
        self.progress_bar.set(1.0)
        self.status_lbl.configure(text=f"✅ Tamamlandı ({ok_count}/{total} dosya dönüştürüldü) -> {out_dir}")
        self._refresh_file_list()


if __name__ == "__main__":
    app = BambuToSolidWorksApp()
    app.mainloop()
