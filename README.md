# 🌈 Bambu Studio (`.3MF` / `.STL`) ➔ SolidWorks (`.STEP` / `.SLDPRT` / `.PRT`) CAD Stüdyosu

[![Canlı Web Stüdyosu](https://img.shields.io/badge/%F0%9F%8C%8D_Canl%C4%B1_Web_St%C3%BCdyosu-Hemen_A%C3%A7_(7%2F24)-00e676?style=for-the-badge)](https://hamzasakci.github.io/bambu-to-solidworks/)
[![Windows Programını İndir](https://img.shields.io/badge/%F0%9F%92%BB_Masa%C3%BCst%C3%BC_Program%C4%B1n%C4%B1_%C4%B0ndir-.ZIP_%C4%B0ndir-ff3b5c?style=for-the-badge)](https://github.com/HamzaSakci/bambu-to-solidworks/archive/refs/heads/main.zip)

Bambu Studio veya MakerWorld'den aldığınız **`.3mf`** ve **`.stl`** dosyalarını SolidWorks'te **Grafik Gövdesi** yerine doğrudan ölçülebilir, kesit alınabilir ve düz yüzeylerinde **2B Çizim (Sketch) açılabilir Katı Gövde (Solid Body)** olarak açmanız için geliştirilmiş **Masaüstü Programı** ve **7/24 Herkese Açık Web Stüdyosu**.

---

## 🌍 1. Kurulumsuz Canlı Web Sitesi (Bilgisayar & Telefondan 7/24 Giriş)

Hiçbir program kurmadan telefonunuzdan, tabletinizden veya herhangi bir bilgisayardan doğrudan tarayıcı üzerinden kullanmak için:

👉 **[https://hamzasakci.github.io/bambu-to-solidworks/](https://hamzasakci.github.io/bambu-to-solidworks/)**

- **3B WebGL Önizleme:** `.3mf` ve `.stl` dosyalarını fare veya dokunmatik ekranla 360° döndürün, yakınlaştırın ve renk temalarını değiştirin.
- **Tarayıcı İçi B-Rep Motoru:** Aynı düzlemdeki üçgenleri birleştirerek anında **`.STEP` (AP214)**, **`.SLDPRT`** ve **`.PRT`** çıktıları üretip indirin.

---

## 💻 2. Bilgisayara Program Olarak İndirme ve Kullanma (Windows + SolidWorks Otomasyonu)

Bilgisayarınızda **SolidWorks** yüklüyse ve doğrudan yerel COM API bağlantısıyla tek tıkla SolidWorks ekranında açmak veya masaüstü arayüzünü (GUI) kullanmak istiyorsanız:

1. **[📥 Buraya Tıklayarak Programı Bilgisayarınıza İndirin (.ZIP)](https://github.com/HamzaSakci/bambu-to-solidworks/archive/refs/heads/main.zip)** ve klasöre çıkartın (veya `git clone https://github.com/HamzaSakci/bambu-to-solidworks.git`).
2. Klasörün içindeki **`Kur_ve_Calistir.bat`** dosyasına çift tıklayın:
   - İlk çalıştırmada gerekli Python ortamını otomatik kurar.
   - **[1] Masaüstü Programı (`app.py`)** veya **[2] Yerel Web Stüdyosu (`web_app.py`)** seçeneklerinden istediğinizi başlatır.

---

## ✨ Temel Özellikler

- **Bambu Studio `.3MF` & `.STL` Doğrudan Okuma:** Hem standart Binary/ASCII `.stl` dosyalarını hem de Bambu Studio'nun çok parçalı `3D/Objects/*.model` yapısına sahip `.3mf` proje dosyalarını doğrudan okur.
- **Düzlemsel Yüzey Birleştirme (Coplanar Face Merging):** Düz yüzeylerdeki yüzlerce küçük üçgeni tek bir analitik `PLANE` (`ADVANCED_FACE`) yüzeyine çevirir. SolidWorks'te düz bir yüzeye sağ tıklayıp **"Çizim Aç" (Edit Sketch)** diyebilirsiniz.
- **3 Farklı CAD Çıktısı:** **`.step`** (ISO-10303-21 AP214), **`.sldprt`** (SolidWorks Parçası) ve **`.prt`** (CAD Part).
