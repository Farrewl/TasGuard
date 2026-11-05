# TasGuard

Sistem penjaga isi tas secara real-time. Satu HP (**HP B, penjaga**) ditaruh di dalam tas dan membaca sensor cahaya serta akselerometer. Di laptop, model Random Forest menilai apakah pola sensor tampak `NORMAL` atau `SUSPICIOUS`. **Tanda utamanya adalah tas dibuka** (cahaya di dalam tas yang gelap naik); getaran, guncangan, atau berjalan saja tidak membunyikan alarm. Bila mencurigakan, laptop mengirim peringatan ke HP lain yang dipegang pengguna (**HP A, pemantau**) berupa tulisan, getaran, dan notifikasi.

> **Konsep satu arah:** HP B (di dalam tas) → laptop (AI) → HP A (dipegang pengguna). HP A hanya menerima. HP B hanya mengirim sensor dan tetap senyap, sehingga tidak berbunyi atau bergetar di dalam tas.

## Tujuan dan batas klaim

TasGuard membantu pemilik menyadari aktivitas tidak biasa pada isi tasnya di tempat umum.

**`SUSPICIOUS` bukan bukti bahwa pencurian benar-benar terjadi.** Sensor cahaya dan akselerometer hanya mengamati kondisi di dalam tas. Sistem tidak tahu siapa yang membuka atau memindahkan tas, dan tidak bisa membedakan dua aktivitas yang menghasilkan pola sensor yang sama.

Model dilatih pada data sintetis (besaran sinyalnya dikalibrasi dari rekaman HP nyata); angka evaluasinya mengukur kecocokan dengan pola simulasi, bukan akurasi deteksi pencurian nyata. Pada rekaman nyata AI dengan syarat tas dibuka setara dengan aturan cahaya saja (lihat [ai/README.md](ai/README.md)).

## Peran perangkat

Peran dipilih di layar sambung setiap HP; tidak bergantung urutan sambung. Satu peran hanya boleh dipegang satu HP.

| | HP A: Pemantau | HP B: Penjaga |
|---|---|---|
| Posisi | Dipegang pengguna | Di dalam tas |
| Mengirim sensor | Tidak | Ya (cahaya kamera + akselerometer, ±5 Hz) |
| Menjalankan AI | Tidak | Hasil AI-nya diproses di laptop |
| Menerima peringatan | Ya: tulisan, getaran, notifikasi | Tidak (senyap), hanya melihat status sendiri |
| Menutup peringatan | Ya, tombol "Saya Sudah Aman" | Tidak |
| Memulai penjagaan | Tidak | Ya, tombol "Mulai Penjagaan" |
| Lokasi (GPS) | Menampilkan posisi HP B saat bahaya | Mengirim posisi saat bahaya |

Pesan yang tidak sesuai peran diabaikan server (misalnya sensor dari HP A).

## Arsitektur

| Komponen | Perangkat | Tugas |
|---|---|---|
| HP B (penjaga) | HP Android + Chrome, di dalam tas | Membaca cahaya (kamera) dan akselerometer, mengirim via WebSocket |
| Laptop | Backend | FastAPI, kalibrasi dan buffer sensor, ekstraksi fitur, Random Forest, keputusan alarm, Kode Akses, dashboard |
| HP A (pemantau) | HP Android + Chrome, dipegang pengguna | Menampilkan status tas, menerima peringatan, menutup peringatan |
| Tunnel | Cloudflare quick tunnel | URL HTTPS agar browser HP boleh memakai kamera, sensor, dan notifikasi |

```text
HP B (penjaga, di dalam tas)
  ├── cahaya (kamera) ─┐
  └── akselerometer ───┴─► WebSocket "sensor" ±5 Hz
                                    │
                                    ▼
                    Laptop: FastAPI (server/)
                      ├── kalibrasi baseline 5 detik
                      ├── buffer 15 sampel (3 detik), evaluasi tiap 1 detik
                      ├── fitur ──► Random Forest ──► skor SUSPICIOUS
                      ├── keputusan: skor ≥ ambang pada 2 evaluasi berurutan + cooldown
                      └── dashboard (hanya di laptop)
                                    │ alarm / status
                                    ▼
                    HP A (pemantau, dipegang pengguna): tulisan + getaran + notifikasi
```

Aturan threshold lama (PPT minggu lalu: terang bertahan + getar bersamaan, ambang absolut) masih dihitung sebagai pembanding (*shadow*) dan ditampilkan di dashboard, tetapi tidak membunyikan alarm. Bedanya dengan sistem minggu lalu: ambang kini relatif terhadap baseline yang dikalibrasi otomatis, dan keputusan diambil oleh Random Forest yang memadukan 11 fitur dari jendela 3 detik menjadi skor (gerakan lembut pun tertangkap), dengan syarat tas dibuka. Perbandingan lengkap dan caranya diuji pada rekaman nyata ada di [ai/README.md](ai/README.md). Detail AI, dataset, dan hasil model juga di sana.

### Peringatan yang diterima HP A

| Kondisi | Tampilan di HP A | Getaran / notifikasi |
|---|---|---|
| Tas dibuka dan polanya mencurigakan | **BAHAYA**, merah: "Ada aktivitas mencurigakan di dalam tas" + tombol Saya Sudah Aman + peta posisi HP B | Getar panjang + notifikasi |
| HP B berhenti mengirim data saat menjaga | "HP penjaga tidak mengirim data" (kuning) | Getar pendek + notifikasi |
| Koneksi HP B terputus saat menjaga | "HP penjaga terputus" (kuning) | Getar pendek + notifikasi |
| HP B berhenti menjaga dengan sengaja | "Penjagaan belum dimulai" | Tidak ada (bukan kondisi bahaya) |
| Semua normal | "Tas aman" (hijau) | Tidak ada |

Batas waktu "tidak mengirim data" dan "terputus" diatur `SENSOR_TIMEOUT_S`. Selama kejadian mencurigakan berlanjut, peringatan BAHAYA dikirim ulang ke HP A setiap `AI_COOLDOWN_S` detik walau belum ditutup, supaya tidak ada yang terlewat. Jika HP A sedang tidak tersambung saat bahaya terjadi, status bahaya tetap tersimpan di laptop dan langsung ditampilkan begitu HP A tersambung kembali.

## Teknologi

- Tanpa aplikasi Android native, Termux, atau broker MQTT: kedua HP membuka halaman web di Chrome Android.
- Frontend: HTML, CSS, JavaScript biasa (PWA dengan service worker untuk notifikasi).
- Backend: Python, FastAPI, WebSocket; AI memakai numpy, scikit-learn, dan joblib.
- Cloudflare quick tunnel menyediakan HTTPS.
- Sensor, kamera, getar, dan notifikasi bergantung pada dukungan serta izin browser Android. Halaman harus tetap terbuka dan layar HP mungkin perlu tetap menyala; HP A dan HP B meminta layar tetap menyala saat bekerja.

## Struktur folder

```text
.
├── start.py                  # menyiapkan .venv, .env, server, tunnel, dan QR code
├── README.md
├── .env                      # konfigurasi lokal (tidak di-commit, dibuat otomatis)
├── server/
│   ├── main.py               # FastAPI: WebSocket /ws, /dashboard, /api/status, /api/label
│   ├── registry.py           # peran HP A/B, status penjaga, pengiriman peringatan satu arah
│   ├── decision_engine.py    # AI: kalibrasi, buffer, inference, keputusan alarm
│   ├── detector.py           # aturan threshold lama (pembanding) dan log sensor
│   ├── config.py             # membaca .env
│   └── requirements.txt
├── web/
│   ├── index.html, style.css               # halaman HP (layar sambung + tampilan per peran)
│   ├── app.js                              # koneksi, autentikasi, pemilihan peran, bagian bersama
│   ├── monitor.js                          # logika HP A (pemantau)
│   ├── guard.js                            # logika HP B (penjaga)
│   ├── dashboard.html, .js, .css           # dashboard laptop
│   ├── minimap.js, minimap.css             # peta mini lokasi
│   └── sw.js, manifest.webmanifest, icons/ # PWA dan notifikasi
└── ai/                       # dataset sintetis, fitur, pelatihan, inference (lihat ai/README.md)
```

File yang dibuat saat berjalan dan tidak di-commit: `.env`, `.venv/`, `sensor_log.csv`, `ai_log.csv`.

## Menjalankan

Dari folder utama proyek:

```bash
python3 start.py
```

`start.py` menyiapkan virtual environment, memasang dependensi, membuat `.env` (termasuk Kode Akses acak), menjalankan server dan tunnel, menunggu tunnel benar-benar tersambung, lalu mencetak URL HTTPS dan QR code.

Opsi:

```bash
python3 start.py --no-tunnel      # hanya lokal (HP harus satu Wi-Fi dengan laptop; kamera tidak jalan tanpa HTTPS)
python3 start.py --new-key        # buat ulang Kode Akses
python3 start.py --port 8080      # pilih port
```

Dashboard laptop: `http://127.0.0.1:8000/dashboard`. Dashboard hanya bisa dibuka dari laptop, tidak melalui tunnel.

### Windows

Prasyarat: Python 3.11 atau lebih baru dari python.org (aktifkan **Add python.exe to PATH**) dan `cloudflared` (`winget install --id Cloudflare.cloudflared`).

```powershell
cd "C:\path\ke\IoT_Kelompok-10_Kelas-C"
py start.py
```

- Hindari interpreter MSYS2/Cygwin untuk virtual environment proyek.
- Jika `.venv` rusak atau tidak lengkap, hapus folder `.venv` lalu jalankan ulang.
- Jika paket gagal dipasang pada versi Python yang sangat baru, pakai Python 3.12 atau 3.13.
- Izinkan akses jaringan privat di Windows Firewall bila ingin mengakses server lewat jaringan lokal.

## Alur penggunaan

1. Jalankan `py start.py` di laptop, tunggu banner dan QR muncul (±15-30 detik), lalu buka dashboard laptop.
2. **HP A (pemantau, yang dipegang):** scan QR atau buka URL tunnel, tempel Kode Akses, pilih peran **HP A - Pemantau**, tekan **Sambungkan**. Izinkan notifikasi saat diminta. Layar menampilkan "Menunggu HP penjaga".
3. **HP B (penjaga, yang ditaruh di tas):** buka URL yang sama, pilih peran **HP B - Penjaga**, tekan **Sambungkan**.
4. Di HP B tekan **Mulai Penjagaan** dan izinkan kamera serta sensor. Masukkan HP B ke dalam tas lalu **biarkan diam**. Kalibrasi baru selesai setelah HP diam 5 detik berturut-turut (hitungan mundur mengulang bila HP bergerak), jadi tidak masalah bila HP baru masuk tas beberapa detik setelah tombol ditekan. Tombol **Masuk Mode Tas** menggelapkan layar; tekan layar 3 detik untuk keluar.
5. Di HP A status berubah menjadi "HP penjaga sedang kalibrasi" lalu "Tas aman". Di dashboard status AI menjadi "Memantau".
6. Bila skor SUSPICIOUS melewati ambang pada 2 evaluasi berurutan, HP A menampilkan BAHAYA, bergetar, dan menampilkan notifikasi. HP B tetap senyap.
7. Setelah memeriksa tas, tekan **Saya Sudah Aman** di HP A untuk menutup peringatan.
8. Untuk merekam data uji AI, bagian **Rekam data (untuk uji AI)** di HP A disembunyikan dari pengguna biasa. Buka HP A dengan menambahkan `?rekam=1` pada alamat halaman (sekali saja, pengaturannya diingat di HP itu; `?rekam=0` menyembunyikannya lagi), lalu pilih label sebelum melakukan kondisinya dan tekan **Selesai**. Pengaturan yang sama ada di dashboard laptop dan keduanya selalu sinkron. Label dikosongkan otomatis saat penjagaan dimulai atau dihentikan.

Untuk menghentikan penjagaan, tekan **Hentikan Penjagaan** di HP B (HP A tidak mendapat peringatan karena ini disengaja).

## Protokol WebSocket (`/ws`)

Pesan berformat JSON UTF-8. Pesan pertama harus `auth` dengan peran yang dipilih HP.

| Arah | Pesan | Keterangan |
|---|---|---|
| HP → laptop | `auth` | `key` (Kode Akses), `device_id`, `role` (`A` atau `B`) |
| HP B → laptop | `guard` | `active` true/false: penjagaan dimulai atau dihentikan sengaja |
| HP B → laptop | `sensor` | `ts`, `light` (`source`, `value`), `accel_mag`, `accel`, `battery` |
| HP B → laptop | `location` | Lokasi GPS (`lat`, `lng`, `acc`), dikirim saat status tas bahaya |
| HP A → laptop | `dismiss` | Menutup peringatan: status tas kembali aman |
| HP A → laptop | `label` | `name`: label kondisi untuk merekam data uji AI (kosong = tanpa label); hanya nama dari daftar server yang diterima |
| HP A/B → laptop | `ping` | Pemeriksaan koneksi |
| laptop → HP | `auth_ok` | `role`, `status`, `peer` (snapshot pasangan), `self` (snapshot diri), `labels` (daftar label), `label` (label aktif) |
| laptop → HP | `auth_fail` | `reason`: `key`, `blocked`, `timeout`, `peran` (tidak valid), `peran_terpakai` |
| laptop → HP | `peer` | Tiap detik: `peer` (snapshot pasangan), `self` (snapshot diri), dan `label` (label rekaman aktif) |
| laptop → HP A | `alarm` | `level` HIGH (AI) atau WARN (data berhenti / terputus), `from`, `reason`, `server_ts` |
| laptop → HP | `pong` | Balasan ping |

`alarm` hanya dikirim ke HP A. Sensor, `guard`, dan `location` hanya diterima dari HP B; `dismiss` dan `label` hanya dari HP A. Pesan dari peran yang salah diabaikan. Snapshot HP B memuat status tas (`status`), penjagaan (`guarding`), kondisi tautan (`link`: `ok`, `silent`, `lost`, `idle`), baterai, lokasi, dan status AI.

## Konfigurasi `.env`

Dibuat otomatis oleh `start.py`; semua nilai dapat diubah tanpa menyentuh kode.

| Kunci | Default | Arti |
|---|---:|---|
| `PORT` | `8000` | Port server |
| `ACCESS_KEY` | acak | Kode Akses |
| `AI_COOLDOWN_S` | `10` | Jeda minimum antar alarm AI |
| `SENSOR_TIMEOUT_S` | `10` | Lama tanpa data (atau terputus) dari HP B yang sedang menjaga sebelum HP A diberi peringatan `WARN` |
| `LOG_CSV` | `true` | Simpan `sensor_log.csv` dan `ai_log.csv` |
| `AI_ENABLED` | `true` | `false` = kembali ke aturan threshold lama sebagai penentu alarm |
| `AI_CALIBRATION_S` | `5` | Lama HP B harus diam (detik berturut-turut) agar baseline kalibrasi diambil |
| `AI_MODEL_PATH` | kosong | Path model lain (kosong = `ai/models/tasguard_rf.joblib`) |
| `AI_REQUIRE_LIGHT` | `true` | Alarm AI hanya berbunyi bila tas terbuka (cahaya naik di atas baseline). `false` mengaktifkan kembali deteksi dari gerakan saja, tetapi lebih sering salah alarm |
| `AI_THRESHOLD`, `AI_CONSECUTIVE` | kosong | Menimpa ambang skor / jumlah evaluasi berurutan milik model (untuk penyetelan) |
| `ALARM_COOLDOWN_S` | `30` | Aturan lama: jeda minimum antar alarm |
| `LUX_THRESHOLD` | `15` | Aturan lama: ambang terang sumber `ambient_sensor` (lux) |
| `LUMA_THRESHOLD` | `40` | Aturan lama: ambang terang sumber `camera` (luma) |
| `LIGHT_HOLD_MS` | `500` | Aturan lama: lama terang harus bertahan |
| `ACCEL_THRESHOLD` | `2.5` | Aturan lama: ambang deviasi akselerasi |
| `SHAKE_MIN_SAMPLES` | `3` | Aturan lama: jumlah sampel getar minimum dalam 1 detik |
| `COINCIDENCE_WINDOW_S` | `2` | Aturan lama: jarak waktu maksimum antara terang dan getar |

Pengaturan aturan lama (`ALARM_COOLDOWN_S` sampai `COINCIDENCE_WINDOW_S`) hanya memengaruhi pembanding di dashboard dan indikator "Terang/Bergerak"; keputusan alarm AI tidak memakainya. Fitur dan baseline jalur AI didefinisikan di `ai/feature_extraction.py`.

## Keamanan

- Kode Akses dibuat acak dan disimpan di `.env`; perbandingannya memakai `hmac.compare_digest`.
- Percobaan autentikasi dibatasi (5 kegagalan memblokir IP selama 60 detik). Hanya ada dua peran; satu peran tidak dapat diambil alih HP lain selama pemegangnya masih aktif (diam > 15 detik dianggap lepas).
- Dashboard, `/api/status`, dan `/api/label` hanya dapat diakses dari localhost laptop.
- Banner `start.py` mencetak URL login `/?key=KODE#key=KODE`. Bagian `?key=` ikut terkirim sebagai query pada request HTTP pertama (dan bisa terlihat di log tunnel); bagian `#key=` tidak dikirim. Halaman langsung menghapus kode dari address bar setelah dibaca. Untuk menghindari query, buka URL tunnel dengan `#key=KODE` saja.

## Keterbatasan

- Model hanya melihat sensor di dalam tas dan tidak tahu identitas orang yang membuka atau memindahkan tas.
- Pencurian yang membawa seluruh tas **tanpa membukanya** tidak terdeteksi, karena tanda utamanya adalah tas dibuka.
- Pemilik yang membuka tasnya sendiri juga memicu alarm karena belum ada fitur jeda penjagaan.
- Data latih sintetis; hasilnya belum membuktikan akurasi deteksi pencurian nyata. Pada rekaman uji kami hanya sekitar separuh pengambilan tas dibuka yang terdeteksi.
- AI hanya mendukung cahaya dari kamera; sumber `ambient_sensor` (lux) tidak didukung.
- Kamera di dalam tas dapat menghasilkan nilai yang berbeda dari simulasi (auto-exposure, noise).
- Peringatan di HP A berjalan lewat halaman web yang terbuka: Chrome dapat menahan halaman yang berada di latar belakang atau layar mati, sehingga HP A sebaiknya dibiarkan menyala dengan halaman TasGuard di depan. Tidak ada push notification saat halaman tertutup.
- Laptop harus menyala; URL quick tunnel berubah setiap kali dijalankan ulang.
- Sensor, layar, GPS, dan koneksi internet menguras baterai, terutama pada HP B.

## Troubleshooting

| Gejala | Pemeriksaan awal |
|---|---|
| Port sudah dipakai | Periksa proses server, atau gunakan `--port` |
| Server berhenti saat startup | Baca pesan error; periksa versi Python dan dependensi |
| HP tidak terhubung | Pakai URL HTTPS tunnel dan pastikan internet aktif |
| Browser HP menampilkan "Error 1033" (Cloudflare Tunnel error) | Tunnel tidak tersambung. `start.py` memakai HTTP/2 agar tidak terblokir jaringan yang memutus UDP, dan menunggu sampai tunnel tersambung sebelum mencetak banner. Pastikan hanya satu `start.py` yang berjalan, pakai URL dari banner terbaru, atau coba hotspot HP |
| "Port 8000 sedang dipakai" saat start | Ada `start.py` lama yang masih berjalan (tekan Ctrl+C di terminalnya) atau proses lain di port itu |
| "Peran ini sedang dipakai HP lain" | Peran A atau B sudah dipegang HP lain yang masih aktif. Putuskan HP tersebut (tombol Putuskan) atau tunggu 15 detik setelah HP itu mati |
| Layar HP lama masih memakai peran lama | Muat ulang halaman; server tidak menyimpan halaman di cache |
| HP A tidak menerima peringatan | Pastikan HP A tersambung (lencana "Laptop terhubung"), halaman TasGuard terbuka dan layar menyala, serta izin notifikasi diberikan |
| HP A bergetar tetapi tidak ada notifikasi | Izinkan notifikasi untuk situs ini di pengaturan Chrome |
| Sensor cahaya tidak terbaca di HP B | Periksa izin kamera/sensor di browser, lalu tekan Izinkan kamera |
| Dashboard tidak bisa dibuka dari HP | Memang dibatasi untuk localhost laptop |
| Model AI tidak tersedia | Pastikan `ai/models/tasguard_rf.joblib` ada dan numpy, scikit-learn, joblib terpasang; atau latih ulang (lihat `ai/README.md`) |
| Status AI "Sumber cahaya tidak didukung" | Aplikasi memakai sensor cahaya bawaan (`ambient_sensor`, lux) bila tersedia. Di Chrome Android buka `chrome://flags`, nonaktifkan **Generic Sensor Extra Classes**, lalu muat ulang halaman agar aplikasi memakai kamera (izin kamera tetap harus diberikan) |
| Alarm berbunyi padahal tas hanya digoyang | Tidak seharusnya terjadi bila `AI_REQUIRE_LIGHT=true`. Lihat dashboard: **Tas terbuka (cahaya naik)** harus "tidak". Bila "ya", cahaya di tas ikut naik (mis. kamera menghadap celah cahaya atau baseline terlalu gelap) |
| Alarm kadang nyala, kadang tidak | Lihat dashboard: **Baseline cahaya** harus kecil (1-10 luma) bila HP B di dalam tas gelap. Baseline besar berarti kalibrasi terjadi saat HP masih terkena cahaya: hentikan lalu mulai ulang penjagaan, masukkan HP ke tas, dan biarkan diam sampai selesai. Tas yang dibuka kurang dari 1-2 detik dengan gerakan sangat pelan memang berada di batas deteksi |
| Hasil AI tidak konsisten | Lihat dashboard: kalibrasi selesai? window valid? Hentikan lalu mulai ulang penjagaan di HP B agar kalibrasi diulang |
| Alarm tidak pernah muncul atau terlalu sering | Lihat skor di dashboard dan `ai_log.csv`; setel `AI_THRESHOLD` / `AI_CONSECUTIVE` di `.env` lalu jalankan ulang |
