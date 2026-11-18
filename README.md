# AI©el-ghifari Cosmic — Paket Upgrade

Paket ini berisi frontend HTML dan backend Flask/Groq yang telah diperbarui. Desain Cosmic, URL backend, dan dua aset gambar badut asli dipertahankan.

## Isi paket

- `index.html` — frontend untuk GitHub Pages.
- `flask_app.py` — backend Flask untuk PythonAnywhere; nama file cocok dengan WSGI yang mengimpor `from flask_app import app as application`.
- `requirements.txt` — dependensi backend, termasuk `pypdf` untuk ekstraksi teks PDF.

## 1. Pasang backend di PythonAnywhere

1. Unduh `flask_app.py` dari paket ini.
2. Buka direktori aplikasi PythonAnywhere: `/home/Nurdinhidayatullohelghifari/mysite`.
3. Buat backup sebelum mengganti file:

   ```bash
   cp flask_app.py flask_app.py.backup-20261118
   ```

4. Unggah file baru dan pastikan namanya tetap `flask_app.py`.
5. Jika menggunakan virtualenv, aktifkan virtualenv tersebut lalu jalankan:

   ```bash
   pip install -r requirements.txt
   ```

   Jika tidak menggunakan virtualenv dan Flask sudah tersedia, paket PDF dapat dipasang dengan `pip install --user pypdf`.

6. Pastikan environment variable `GROQ_API_KEY` tetap disimpan di server, bukan di HTML. Pertahankan juga `GROQ_MODEL`, `GROQ_VISION_MODEL`, dan `FRONTEND_ORIGINS` jika sudah diatur.
7. WSGI harus tetap mengarah ke aplikasi Flask:

   ```python
   from flask_app import app as application
   ```

8. Klik **Reload** pada Web tab PythonAnywhere.

## 2. Pasang frontend di GitHub Pages

1. Unduh `index.html`.
2. Ganti file `index.html` pada repository GitHub Pages dengan file baru.
3. Commit dan push perubahan.
4. Buka ulang website dengan hard refresh (`Ctrl+F5`).

## 3. Pemeriksaan setelah pemasangan

Buka endpoint berikut:

- `https://nurdinhidayatullohelghifari.pythonanywhere.com/health`
- `https://nurdinhidayatullohelghifari.pythonanywhere.com/api/time?timezone=Asia/Jakarta`
- `https://nurdinhidayatullohelghifari.pythonanywhere.com/api/time?timezone=Asia/Makassar`
- `https://nurdinhidayatullohelghifari.pythonanywhere.com/api/time?timezone=Asia/Jayapura`

Pada `/health`, periksa `api_key_configured: true`, `time_endpoint: true`, dan `document_extraction_endpoint: true`. Jika `pdf_extraction_available` bernilai `false`, instal `pypdf` di environment PythonAnywhere yang dipakai web app lalu reload.

Di website, uji:

1. Kirim `Jam berapa sekarang?` — harus menampilkan waktu dari server dengan zona WIB.
2. Kirim `Jam berapa sekarang di WITA?` — harus menampilkan zona WITA.
3. Pilih skill Web Search Engine dan minta pencarian — harus menjalankan pencarian backend jika koneksi keluar hosting diizinkan.
4. Lampirkan file DOCX/XLSX/PPTX atau PDF teks — isi teks diekstrak sebelum dikirim ke AI.
5. Lampirkan satu gambar — gambar diteruskan ke model vision.

## Batasan yang perlu diketahui

- PDF hasil scan/foto memerlukan OCR; endpoint PDF saat ini mengekstrak teks yang tertanam, bukan OCR halaman gambar.
- XLSX mengekstrak teks/nilai mentah dari XML workbook; bukan kalkulasi ulang rumus Excel atau mempertahankan format visual.
- Audio/video belum ditranskripsikan otomatis. Berikan transkrip terlebih dahulu.
- Pengiriman email, penulisan acara ke kalender, unggah cloud, geocoding peta, dan generasi gambar belum terhubung ke layanan eksternal. Profil skill-nya tersedia untuk membantu menyusun pekerjaan, tetapi tidak boleh dianggap melakukan aksi tersebut.
- Input chat dibatasi 4.500 karakter pada frontend agar prompt skill tetap berada dalam batas konteks backend. Lampiran dokumen dibatasi 6 MB pada frontend; gambar dibatasi 5 MB.

## Catatan verifikasi

Paket diperiksa dengan `py_compile` untuk backend, `node --check` untuk JavaScript frontend, pencocokan 51 ID skill frontend/backend, dan uji fungsi jam/zona waktu serta ekstraksi DOCX menggunakan test harness lokal. Lingkungan kerja ini tidak memiliki Flask terpasang dan tidak memiliki akses ke akun PythonAnywhere/Groq Anda; karena itu, pengujian endpoint Flask live dan panggilan Groq produksi harus dilakukan setelah file dipasang dan web app di-reload.

