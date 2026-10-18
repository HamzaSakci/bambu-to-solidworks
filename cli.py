"""
Bambu Studio (.3mf / .stl) -> SolidWorks (.step / .sldprt / .prt) Komut Satırı (CLI) Aracı
=========================================================================================
Örnek Kullanım:
  python cli.py parca.stl
  python cli.py model.3mf --out ./output --open-sw
  python cli.py --batch ./input --out ./output --formats step,sldprt,prt
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from converter_engine import ConversionOptions, convert_cad_file


def collect_input_files(paths: List[str], batch_dir: str | None) -> List[Path]:
    supported = {".stl", ".3mf", ".obj", ".ply"}
    files: List[Path] = []

    for p in paths:
        path_obj = Path(p)
        if path_obj.is_dir():
            for child in sorted(path_obj.iterdir()):
                if child.is_file() and child.suffix.lower() in supported:
                    files.append(child)
        elif path_obj.is_file() and path_obj.suffix.lower() in supported:
            files.append(path_obj)

    if batch_dir:
        bdir = Path(batch_dir)
        if bdir.is_dir():
            for child in sorted(bdir.iterdir()):
                if child.is_file() and child.suffix.lower() in supported:
                    files.append(child)

    # Tekrarları kaldır
    unique: List[Path] = []
    seen = set()
    for f in files:
        resolved = str(f.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(f)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bambu Studio (.3mf / .stl) dosyalarını SolidWorks (.step, .sldprt, .prt) formatlarına dönüştürür."
    )
    parser.add_argument("files", nargs="*", help="Dönüştürülecek .stl, .3mf veya .obj dosyaları")
    parser.add_argument("--batch", "-b", type=str, default=None, help="Klasördeki tüm STL/3MF dosyalarını toplu dönüştür")
    parser.add_argument("--out", "-o", type=str, default="output", help="Çıktı klasörü (varsayılan: ./output)")
    parser.add_argument(
        "--formats",
        "-f",
        type=str,
        default="step,sldprt,prt",
        help="Üretilecek formatlar, virgülle ayrılmış: step,sldprt,prt (varsayılan: step,sldprt,prt)",
    )
    parser.add_argument(
        "--no-merge",
        action="store_true",
        help="Düzlemsel komşu üçgenleri birleştirmeyi kapat (ham üçgen yüzeyler)",
    )
    parser.add_argument(
        "--angle",
        type=float,
        default=1.0,
        help="Düzlemsel yüzey birleştirme açı toleransı (derece, varsayılan: 1.0)",
    )
    parser.add_argument(
        "--max-faces",
        type=int,
        default=25000,
        help="Maksimum üçgen sayısı sınırı (0 = sınırsız, varsayılan: 25000)",
    )
    parser.add_argument(
        "--unit",
        choices=["mm", "cm", "m"],
        default="mm",
        help="Birim sistemi (varsayılan: mm)",
    )
    parser.add_argument(
        "--open-sw",
        action="store_true",
        help="Dönüştürme bittikten sonra modeli doğrudan SolidWorks'te aç",
    )

    args = parser.parse_args()
    target_files = collect_input_files(args.files, args.batch)

    if not target_files:
        # Eğer argüman verilmediyse input/ klasörüne bak
        default_in = Path("input")
        if default_in.is_dir():
            target_files = collect_input_files([], str(default_in))

    if not target_files:
        parser.print_help()
        print("\n[!] Dönüştürülecek .stl veya .3mf dosyası bulunamadı.")
        print("    İpucu: Arayüzü (GUI) başlatmak için 'python app.py' komutunu çalıştırın.")
        return 1

    fmt_set = {x.strip().lower().lstrip(".") for x in args.formats.split(",") if x.strip()}
    options = ConversionOptions(
        merge_coplanar=not args.no_merge,
        coplanar_angle_deg=args.angle,
        max_faces=args.max_faces,
        unit=args.unit,
        export_step=("step" in fmt_set or "stp" in fmt_set),
        export_sldprt=("sldprt" in fmt_set),
        export_prt=("prt" in fmt_set),
        open_in_solidworks_after=args.open_sw,
    )

    print("=" * 72)
    print(f"🚀 Bambu Studio (STL/3MF) -> SolidWorks (STEP / SLDPRT / PRT) Dönüştürücü")
    print(f"📂 Toplam Dosya: {len(target_files)} | Çıktı Klasörü: {Path(args.out).resolve()}")
    print("=" * 72)

    ok_count = 0
    for idx, fpath in enumerate(target_files, 1):
        print(f"\n[{idx}/{len(target_files)}] İşleniyor: {fpath.name}")

        def on_prog(msg: str, pct: float):
            print(f"   [{int(pct * 100):3d}%] {msg}")

        res = convert_cad_file(
            input_path=str(fpath),
            output_dir=args.out,
            options=options,
            progress_callback=on_prog,
        )
        if res.success:
            ok_count += 1
            print(f"   ✔ {res.message}")
            for fmt, out_p in res.output_files.items():
                print(f"      -> [{fmt.upper()}] {out_p}")
            for w in res.warnings:
                print(f"      [Uyarı] {w}")
        else:
            print(f"   ✖ {res.message}")

    print("\n" + "=" * 72)
    print(f"✅ Tamamlandı: {ok_count}/{len(target_files)} dosya başarıyla dönüştürüldü.")
    print("=" * 72)
    return 0 if ok_count == len(target_files) else 2


if __name__ == "__main__":
    sys.exit(main())
