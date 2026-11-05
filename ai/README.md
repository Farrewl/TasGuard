# Modul AI TasGuard

Modul ini berisi seluruh bagian AI: pembangkit dataset sintetis (besaran sinyalnya dikalibrasi dari rekaman HP nyata), definisi fitur, pelatihan Random Forest, inference yang dipakai server, dan alat untuk menguji model pada rekaman HP sungguhan.

> **Batas klaim:** model dilatih pada data sintetis yang polanya didefinisikan oleh tim. Model mengklasifikasikan pola sensor sebagai `NORMAL` atau `SUSPICIOUS`; hasilnya **bukan bukti** bahwa pencurian benar-benar terjadi dan **bukan** akurasi deteksi pencurian nyata.

## Isi folder

```text
ai/
├── feature_extraction.py   # satu-satunya definisi fitur (dipakai generator, training, dan server)
├── inference.py            # memuat model dan menghitung skor SUSPICIOUS
├── fit_simulator.py        # mengukur sinyal rekaman nyata -> kalibrasi_nyata.json
├── kalibrasi_nyata.json    # hasil ukur (masukan generator; ikut git karena sensor_log.csv tidak)
├── generate_dataset.py     # pembangkit dataset sintetis (deterministik, seed tetap)
├── validate_dataset.py     # 57 pemeriksaan kontrak dataset
├── audit_dataset.py        # membuat dataset/dataset_report.md
├── train_model.py          # melatih model, memilih ambang, membuat laporan
├── compare_real.py         # membandingkan aturan vs AI pada rekaman HP berlabel (--replay) -> models/real_comparison.md
├── uji_terkontrol.py       # aliran sensor buatan: getaran saja vs tas dibuka (tanpa HP dan server)
├── dataset/                # CSV hasil generator, dataset_config.json, dataset_report.md
└── models/                 # tasguard_rf.joblib, metrics.json, model_report.md, real_comparison.md
```

File `*.csv` tidak ikut git (lihat `.gitignore`); buat ulang dengan `generate_dataset.py`. Model `models/tasguard_rf.joblib` ikut git sehingga server langsung dapat memakainya.

## Alur kerja

```text
sensor_log.csv ──► fit_simulator.py ──► kalibrasi_nyata.json
                                              │
                          generate_dataset.py ◄┘ ──► dataset/*.csv ──► validate_dataset.py   (kontrak data)
                                                         ├──► audit_dataset.py      (dataset_report.md)
                                                         └──► train_model.py ──► models/tasguard_rf.joblib
                                                                                         │
                                          server/decision_engine.py ◄── inference.py ◄───┘
                                          (fitur dari feature_extraction.py)
```

## Dataset sintetis

| Item | Nilai |
|---|---|
| Sesi | 4.000 (8 skenario × 500 sesi), tiap sesi 7 detik @ 5 Hz = 35 sampel |
| Sampel mentah / window fitur | 140.000 / 20.000 (5 window per sesi) |
| Window / stride | 3 detik (15 sampel) / 1 detik |
| Split (per sesi, tiap skenario) | train 350, validation 75, test 75 sesi, sehingga window 14.000 / 3.000 / 3.000 |
| Kelas window | NORMAL 13.327 (66,6 %), SUSPICIOUS 6.673 (33,4 %) |
| Seed | 20261105 (hasil identik tiap dijalankan) |

### Skenario

| Kelas | Skenario | Pola yang disimulasikan |
|---|---|---|
| NORMAL | `normal_idle` | Tas diam, noise sensor, kadang gerakan atau sentakan kecil |
| NORMAL | `normal_walking` | Tas dibawa berjalan (gerak periodik 1,45-2,25 Hz) |
| NORMAL | `normal_handling` | Tas dipegang/diangkat: 1-3 gerakan pendek (0,2-0,6 detik), kedipan cahaya kecil |
| NORMAL | `normal_vibration` | Getaran atau guncangan kuat **tanpa tas dibuka** (kendaraan, dijatuhkan, dibenturkan), 1,5-4,5 detik |
| SUSPICIOUS | `suspicious_open_move` | Tas **dibuka** (kejadian 1,4-4,0 detik; satu bukaan median 0,8 detik, kenaikan cahaya mengikuti rekaman nyata), tangan bergerak pelan atau tidak sama sekali |
| SUSPICIOUS | `suspicious_open_rummage` | Tas dibuka dan isinya diaduk: 2-4 gerakan berulang |
| SUSPICIOUS | `suspicious_open_pull` | Tas dibuka dan barang ditarik kuat, sering ada tarikan kedua |
| SUSPICIOUS | `suspicious_combined` | Tas dibuka disertai kombinasi komponen cahaya dan gerakan |

Semua pola di atas adalah **asumsi tim**, bukan rekaman pencurian.

**Konsep: barang di dalam tas hanya bisa diambil bila tas dibuka.** HP penjaga ada di dalam tas yang gelap, jadi tas dibuka berarti cahaya naik jelas. Semua skenario SUSPICIOUS memuat tas dibuka; getaran saja (`normal_vibration`, `normal_walking`) berlabel NORMAL. Sebagian sesi mencurigakan juga memuat berjalan biasa (peluang 15 %).

### Asal label

Label window ditentukan oleh `event_fraction`, yaitu porsi sampel dalam window yang berada di dalam kejadian mencurigakan simulasi:

- `event_fraction >= 0.4` → `SUSPICIOUS`;
- selain itu → `NORMAL`.

Akibatnya window dari skenario mencurigakan yang kejadiannya hanya sebagian atau di luar window berlabel `NORMAL`. Window "ambigu" (`0 < event_fraction < 0.4`) tidak dipakai untuk melatih.

### Kalibrasi dari rekaman nyata

Dataset tetap sintetis, tetapi besaran sinyalnya diukur dari rekaman HP penjaga (HP B di dalam tas) oleh [fit_simulator.py](fit_simulator.py) dan disimpan di [kalibrasi_nyata.json](kalibrasi_nyata.json). Rekaman nyata **tidak disalin** ke dataset dan **tidak dipakai melatih** model; yang diambil hanya angka ringkas di bawah ini. Rentang tebakan awal simulator ternyata meleset jauh dari sensor asli:

| Besaran | Hasil ukur pada rekaman | Dipakai di simulator |
|---|---|---|
| Baseline tas gelap | 0-6 luma, tanpa noise (tas tertutup) | separuh sesi berbaseline gelap (`P_DARK_BAG = 0,5`; angka 0,5 adalah keputusan desain, bukan hasil ukur); sebelumnya 9-79 luma |
| Kenaikan cahaya maksimum saat tas dibuka | median 0,18 (sekitar 46 luma), sebaran lebar (sigma log 0,81) | lognormal, menggantikan rentang seragam 0,15-0,8 |
| Lama satu bukaan | median 0,8 detik (sigma log 0,86) | lognormal, menggantikan "hampir seluruh kejadian" |
| Gerak: persentil ke-95 simpangan akselerasi | diam 0,09; dipegang 7,7; berjalan 8,5; digoyang 16,7; tas dibuka 1,5-2,7 (m/s²) | skala amplitudo per skenario, dihitung iteratif dari sesi percobaan agar persentil simulasi cocok |
| Batas sensor akselerasi | jenuh di 78,44 m/s² (8 g) | `ACCEL_MAX = 78,48` (sebelumnya 39,24) |

Pengukuran mengubah dua asumsi lama: gerakan normal di rekaman nyata jauh lebih kuat daripada simulator, sedangkan gerakan tangan saat tas dibuka justru lembut. Margin cahaya "tas dibuka" juga diturunkan dari 0,08 (sekitar 20 luma) menjadi `LIGHT_MARGIN = 0,04` (sekitar 10 luma), karena tas yang dibuka sedikit atau miring hanya menaikkan cahaya sekitar 25 luma.

**Pembagian rekaman agar ujian tetap adil.** Hanya sebagian rekaman yang dipakai mengukur: segmen tas dibuka yang mulai pukul 16:05-16:54 (kondisi 1 dan 2) dan segmen normal yang mulai sebelum 15:50. Kondisi 3 (HP miring, tas dibuka lebar) dan segmen normal setelah 15:50 tidak disentuh dan menjadi bahan ujian.

Batas kalibrasi: ukuran diambil dari satu orang, satu HP, dan satu tas (35 segmen tas dibuka, 13 segmen normal). `normal_handling` mentok di batas skala 25 (`ACCEL_SCALE_MAX`) karena letupan gerak di simulator lebih singkat daripada rekaman: persentil ke-95 simulasi 6,0 vs nyata 7,7 m/s².

### Artefak sensor

Pada kedua kelas dengan peluang sama disuntikkan lonjakan (spike), nilai macet (`held_value`), dropout (diisi nilai sebelumnya, `dropout_locf`), dan clipping. Semuanya dicatat di `tasguard_artifact_log.csv`. Karena sebarannya sama pada kedua kelas, artefak tidak menjadi jalan pintas bagi model.

### Membuat ulang dan memeriksa

```bash
python ai/fit_simulator.py           # opsional: ukur ulang sinyal dari sensor_log.csv -> ai/kalibrasi_nyata.json
python ai/generate_dataset.py        # ±15 detik, menulis ke ai/dataset/ (membaca ai/kalibrasi_nyata.json)
python ai/validate_dataset.py        # 57 pemeriksaan: skema, split per sesi, label, kebocoran, batas nilai
python ai/audit_dataset.py           # membuat ai/dataset/dataset_report.md
```

Laporan audit lengkap (distribusi, rentang sensor, tumpang tindih kelas, kebocoran): [dataset/dataset_report.md](dataset/dataset_report.md).

## Fitur

Semua fitur dihitung oleh `compute_features()` di [feature_extraction.py](feature_extraction.py) dari satu window (15 sampel) dan satu periode kalibrasi (baseline). Generator, training, dan server memanggil fungsi yang sama sehingga fitur training dan runtime identik.

| Fitur | Arti | Dipakai model |
|---|---|:---:|
| `light_mean`, `light_max`, `light_min` | Statistik cahaya absolut dalam window | tidak |
| `light_delta` | Cahaya sampel terakhir dikurangi sampel pertama (bertanda) | ya |
| `light_change_rate` | Rata-rata laju perubahan cahaya | ya |
| `light_elevated_duration` | Detik cahaya di atas baseline + margin | ya |
| `bright_ratio` | Porsi sampel yang jauh di atas baseline | ya |
| `accel_mean`, `accel_max`, `accel_std` | Statistik magnitudo akselerasi | ya |
| `accel_change_rate` | Rata-rata laju perubahan akselerasi | ya |
| `motion_duration` | Detik gerakan melebihi margin dari baseline | ya |
| `motion_event_count` | Jumlah episode gerakan terpisah dalam window | ya |
| `light_motion_coincidence` | Porsi sampel saat cahaya naik dan bergerak bersamaan | ya |

Tiga fitur cahaya absolut tidak dipakai karena bergantung pada skala dan sensor tiap HP. Sisanya dihitung relatif terhadap baseline sehingga yang diukur adalah **perubahan terhadap kondisi awal di dalam tas**, bukan nilai absolut.

**Baseline** adalah median periode kalibrasi (cahaya dan akselerasi). Cahaya dihitung "naik" bila melebihi baseline + `LIGHT_MARGIN` (0,04 pada skala 0..1, sekitar 10 luma) atau 2,5 × MAD baseline bila lebih besar; gerakan memakai margin dari MAD periode itu:

- dataset: 1 detik pertama setiap sesi (dijamin bebas aktivitas);
- server: 5 detik pertama setelah pemantauan dimulai (`AI_CALIBRATION_S`).

**Skala cahaya:** nilai kamera diubah menjadi `luma / 255` oleh `normalize_light()`. Sumber `ambient_sensor` (lux) tidak didukung model karena tidak punya padanan skala di dataset.

## Pelatihan dan hasil

```bash
python ai/train_model.py
```

Model: Random Forest (100 pohon, `max_depth=8`, `min_samples_split=4`, `min_samples_leaf=2`, `class_weight="balanced"`, `random_state=42`) dengan 11 fitur. Prosedur:

1. Latih di split `train` (window ambigu dikeluarkan).
2. Pilih ambang di split `validation`: **tengah dataran** ambang yang membuat paling banyak 10 % sesi NORMAL memicu alarm dan deteksinya hampir terbaik, dengan aturan 2 window berurutan dan syarat tas dibuka. Hasilnya **0.5**.
3. Evaluasi split `test` satu kali, dibandingkan dengan aturan threshold lama (aproksimasi pada fitur window: cahaya naik ≥ 0,4 detik dan gerakan ≥ 0,6 detik).
4. Simpan `models/tasguard_rf.joblib` (model, urutan fitur, ambang), `models/metrics.json`, dan `models/model_report.md`.

### Hasil pada data uji sintetis (3.000 window, 600 sesi)

| Metode | F1 window | Sesi NORMAL yang alarm | Sesi SUSPICIOUS yang terdeteksi |
|---|---|---|---|
| Aturan lama (aproksimasi pada fitur window) | 0,67 | 4,3 % | 65,7 % |
| Aturan A: cahaya naik saja (`compare_real.py --selftest`) | 0,73 | 5,7 % | 77,7 % |
| Random Forest saja, tanpa syarat tas dibuka | 0,84 | 9,0 % | 95,7 % |
| Random Forest + syarat tas dibuka (ambang 0,5, 2 window berurutan; dipakai server) | 0,74 (ROC AUC 0,93) | 5,0 % | 77,7 % |

Fitur terpenting: `light_elevated_duration` (0,34), `light_motion_coincidence` (0,18), `accel_std` (0,10), `light_change_rate` (0,08), `accel_max` (0,07). Rincian lengkap, termasuk sweep ambang dan hasil per skenario, ada di [models/model_report.md](models/model_report.md).

Cara membaca angka ini:

- Hanya mengukur kecocokan dengan definisi pola di generator; tidak menunjukkan akurasi pada pencurian nyata.
- Aturan lama dihitung dari fitur window, bukan dijalankan sebagai detektor waktu nyata, dan data dirancang oleh tim yang sama. Perbandingannya indikatif, bukan uji lapangan.
- Setelah kalibrasi dengan rekaman nyata, dataset tidak lagi hampir terpisah oleh satu fitur (AUC fitur terbaik `light_elevated_duration` 0,871 dibanding 0,996 sebelumnya) dan angkanya jauh lebih rendah daripada versi tebakan awal (F1 0,74 vs 0,92; deteksi 77,7 % vs 100 %). Penurunan itu wajar: bukaan nyata singkat dan sebagian tidak melewati margin cahaya.
- **Dengan syarat tas dibuka, AI praktis sama dengan aturan "cahaya naik saja"**: deteksi sesi sama (77,7 %), F1 hampir sama (0,74 vs 0,73). Syarat itu membatasi deteksi AI pada window yang cahayanya naik, sehingga yang tersisa hanya penyaringan salah alarm.
- Tanpa syarat tas dibuka model lebih sensitif (95,7 %) tetapi lebih sering salah alarm (9,0 %), terutama oleh gerakan; lihat bagian hasil rekaman nyata di bawah.

## Inference di server

`server/decision_engine.py` memakai `inference.py` dan `feature_extraction.py` untuk HP penjaga (HP B, di dalam tas), satu-satunya sumber sensor. Sensor dari HP pemantau (HP A) tidak diterima:

1. **Kalibrasi:** baseline diambil dari bagian terbaru yang **stabil** (cahaya hampir tetap dan HP diam) selama `AI_CALIBRATION_S` (5 detik) berturut-turut, bukan dari 5 detik pertama. HP boleh masih dipegang atau baru dimasukkan ke tas; kalibrasi menunggu sampai HP diam. Bila HP tidak pernah diam selama 45 detik (misalnya dibawa berjalan), baseline diambil dari sampel terbaru apa adanya (status "kalibrasi darurat").
2. **Buffer:** 15 sampel terbaru; evaluasi sekali tiap 1 detik.
3. **Validasi window:** rentang waktu 2,2-3,8 detik dan tidak ada celah data > 1 detik. Jika tidak valid, tidak ada inference dan hitungan berurutan di-reset.
4. **Inference:** fitur dihitung, model memberi skor `probability_suspicious`.
5. **Keputusan:** alarm `HIGH` bila skor ≥ ambang **dan tas terbuka** (`light_elevated_duration` ≥ 0,4 detik di atas baseline, `AI_REQUIRE_LIGHT`) pada `consecutive` (2) evaluasi berurutan, lalu cooldown `AI_COOLDOWN_S` (10 detik). Tanpa kenaikan cahaya, getaran sekuat apa pun tidak membunyikan alarm. Window berurutan saling tumpang tindih, jadi dua prediksi bukan bukti independen.
6. **Kasus khusus:** model tidak ada atau sumber cahaya bukan kamera → status saja, tanpa alarm; jeda data > 3 detik atau penjagaan dihentikan lalu dimulai lagi → kalibrasi ulang. Data berhenti atau koneksi HP B putus saat menjaga menghasilkan `WARN` ke HP A, bukan `SUSPICIOUS`.

**Baseline cahaya hanya boleh turun.** Bila cahaya stabil di level yang jelas lebih gelap (selisih ≥ 0,08, sekitar 20 luma) selama 5 detik, baseline cahaya diturunkan otomatis. Ini memperbaiki baseline yang terlanjur diambil saat HP masih terkena cahaya. Baseline tidak pernah dinaikkan: pencuri membuat cahaya naik, sehingga baseline yang naik akan menyembunyikan pencurian. Baseline yang dipakai terlihat di dashboard.

Alarm `HIGH` hanya dikirim ke HP A (pemantau); HP B tetap senyap. Aturan threshold lama tetap dihitung sebagai pembanding (*shadow*) dan ditampilkan di dashboard, tetapi tidak membunyikan alarm.

## Apa bedanya dengan aturan di PPT minggu lalu?

Sistem minggu lalu sepenuhnya rule-based: cahaya >= 15 lux atau luma 40 selama 500 ms **dan** >= 3 sampel getar dengan deviasi > 2,5 m/s² dalam 1 detik, cooldown 30 detik. Perbedaannya dengan sistem sekarang:

| Aspek | PPT minggu lalu | Sistem sekarang | Jenis perbaikan |
|---|---|---|---|
| Ambang | absolut (15 lux / luma 40 / 2,5 m/s²), disetel manual | relatif terhadap baseline; kalibrasi otomatis menunggu HP diam; baseline hanya turun | rekayasa sinyal (bukan AI) |
| Syarat alarm | cahaya DAN getar kuat (slide 2 menulis ATAU, slide 6 dan 8 menulis DAN) | tas dibuka (cahaya naik) wajib, lalu AI menilai pola; getaran saja tidak membunyikan alarm | desain sistem |
| Keputusan | AND keras dua ambang tetap | fusi lunak 11 fitur pada jendela 3 detik, menghasilkan skor 0-1 | **AI** (Random Forest) |
| Gerakan lembut | tidak terdeteksi (butuh > 2,5 m/s²) | skor naik walau gerakan pelan | **AI** (dipelajari dari data) |
| Keluaran | alarm atau tidak | skor yang bisa disetel (`AI_THRESHOLD`) | **AI** |

Bukti awal dari rekaman uji tim (belum berlabel): dari 1.131 momen tas terbuka di sesi tas gelap, hanya 25 % yang memenuhi syarat getar aturan PPT (median deviasi saat tas terbuka 1,93 m/s², di bawah ambang 2,5). Ini menjelaskan gejala "membuka tas pelan tidak membunyikan alarm" pada aturan yang butuh getaran kuat.

**Catatan jujur.** Pada data sintetis maupun rekaman nyata, AI dengan syarat tas dibuka setara dengan aturan "cahaya naik saja" (bagian hasil di bawah). Perbaikan terbesar atas sistem minggu lalu datang dari **baseline relatif dan margin cahaya yang sesuai sensor asli** (rekayasa sinyal), bukan dari Random Forest. Klaim keunggulan AI tidak didukung data kami; laporan perbandingan mengikuti angka apa adanya.

## Hasil pada rekaman nyata

Rekaman dibuat tim dengan memeragakan sendiri kondisi di depan HP (5 November 2026, HP B memakai sensor cahaya kamera di dalam tas gelap), bukan pencuri sungguhan. Lima kondisi dengan peran berbeda agar ujian tidak sirkular:

| Kondisi (mulai) | Isi | Peran |
|---|---|---|
| 0a normal (15:32) | tas tertutup: diam, berjalan, dipegang, digoyang | mengukur simulator |
| 0b normal akhir (15:50) | sama dengan 0a | **ujian** (10 segmen normal) |
| 1 atas, buka lebar (16:05) | HP menghadap atas, tas dibuka lebar | mengukur simulator dan menyetel ambang |
| 2 atas, buka sedikit (16:44) | HP menghadap atas, tas dibuka sedikit | mengukur simulator |
| 3 miring, buka lebar (16:55) | HP miring, tas dibuka lebar | **ujian** (22 segmen tas dibuka) |

Hasil ujian dengan model dan ambang 0,5 seperti yang dipakai server (segmen = satu pengambilan berlabel; alarm = 2 window berurutan; zona batas 3 detik dibuang). Laporan lengkap: [models/real_comparison.md](models/real_comparison.md). Rekaman mentahnya (`sensor_log.csv`, `ai_log.csv`) tidak ikut git (`*.csv` diabaikan), jadi angka ini hanya bisa diulang oleh yang memegang berkas rekaman tersebut; dataset sintetisnya dapat dibuat ulang oleh siapa pun dengan `generate_dataset.py`.

| Metode | Salah alarm (10 segmen normal) | Tas dibuka terdeteksi (22 segmen) |
|---|---|---|
| Aturan PPT minggu lalu (ambang absolut) | 0 | 0 (0 %) |
| Aturan A: cahaya naik saja | 0 | 12 (55 %) |
| Aturan B: cahaya naik dan getar | 0 | 12 (55 %) |
| AI saja, tanpa syarat tas dibuka | 2 (20 %) | 20 (91 %) |
| **AI + syarat tas dibuka (dipakai server)** | **0** | **12 (55 %)** |
| *Pembanding: margin lama 0,08 dan model lama, ujian sama; Aturan A / AI + syarat* | *0 / 0* | *8 (36 %) / 5 (23 %)* |

Cara membaca:

1. **Aturan PPT minggu lalu tidak mendeteksi satu pun** (0 dari 22): ambang absolut luma 40 tidak pernah tercapai karena HP di dalam tas gelap dan tas dibuka hanya menaikkan cahaya sekitar 25-30 luma. Ini bukti yang paling jelas bahwa ambang relatif terhadap baseline diperlukan.
2. **Perbaikan 23-36 % menjadi 55 % datang dari margin cahaya 0,04, bukan dari AI.** Aturan A dan AI sama-sama naik. Margin dipilih setelah melihat besar kenaikan cahaya di seluruh kondisi, termasuk kondisi 3, sehingga ujian kondisi 3 tidak sepenuhnya independen. Ujian yang bersih membutuhkan rekaman baru yang belum pernah dilihat saat menyetel.
3. **AI dengan syarat tas dibuka sama persis dengan aturan cahaya saja** (12 dari 22 pada keduanya). Karena tas dibuka wajib, gerbang cahaya yang menentukan; AI hanya bisa menyaring, tidak bisa menambah deteksi. Pada data uji sintetis pola yang sama muncul.
4. **Angka "AI saja 91 %" bukan bukti AI mendeteksi pencurian.** Window berlabel mencurigakan yang tasnya masih tertutup (836 window) mendapat skor median 0,74, sedangkan window normal tertutup 0,06. Model memisahkan gerakan tangan yang lembut (accel_std median 0,5) dari tas diam atau tas yang diguncang kuat. Itu cocok dengan cara merekam: label menempel di seluruh pengambilan 15-20 detik padahal tas terbuka hanya sekitar 10 % waktunya (median 1-2 detik), dan besar gerak tiap kelas diukur dari rekaman berlabel yang sama. Hasilnya khas gaya rekaman satu orang. Varian ini juga salah alarm 2 dari 10 segmen normal (dipegang dan digoyang), persis keluhan awal alarm tanpa tas dibuka, sehingga syarat tas dibuka tetap dipertahankan.
   Percobaan terkontrol (`python ai/uji_terkontrol.py`, aliran sensor buatan, tanpa syarat tas dibuka) menunjukkan akibatnya: getaran **lembut** (0,5-2 m/s²) tanpa tas dibuka dibunyikan alarm pada 100 % ulangan, sedangkan getaran **kuat** (4 m/s²) 0 %, kebalikan dari keluhan awal. Dengan syarat tas dibuka (seperti server), tas dibuka terdeteksi 100 % pada semua lama (1-5 detik) dan kuat gerakan (0-4 m/s²), termasuk tas dibuka sedikit (luma naik hanya ke 60), dan getaran saja tidak pernah membunyikan alarm karena gerbang cahaya.
5. **Hanya 55 % segmen tas dibuka yang terdeteksi.** Sebagian pengambilan tidak pernah menaikkan cahaya melewati margin (sekitar 10 luma) atau terbukanya hanya sekejap; segmen seperti itu tidak bisa terdeteksi oleh syarat tas dibuka.
6. Sampel kecil: 22 segmen tas dibuka dan 10 segmen normal, dari satu orang, satu HP, dan satu tas. Selisih beberapa segmen bisa kebetulan.

Menjalankan ulang (fitur dihitung ulang dari `sensor_log.csv` dengan kode yang sekarang; berkas log tidak diubah):

```bash
python ai/compare_real.py --replay \
  --kondisi "15:32=0a normal awal (dipakai menyetel simulator)" --kondisi "15:50=0b normal akhir (ujian)" \
  --kondisi "16:05=1 atas, buka lebar (dipakai menyetel simulator)" \
  --kondisi "16:44=2 atas, buka sedikit (dipakai menyetel simulator)" \
  --kondisi "16:55=3 miring, buka lebar (ujian)" \
  --abaikan "0a" --abaikan "1 atas" --abaikan "2 atas"
```

## Merekam dan mengevaluasi data nyata

Dataset sintetis tidak membuktikan perilaku di dunia nyata. Untuk menilainya, rekam kondisi sungguhan:

1. Jalankan server (`py start.py`), buka `http://127.0.0.1:8000/dashboard` di laptop. Sambungkan HP B dengan peran **Penjaga**, tekan **Mulai Penjagaan**, lalu masukkan ke dalam tas. HP A tidak diperlukan untuk merekam data.
2. Tunggu status AI "Memantau" dan pastikan **Baseline cahaya** kecil (1-10 luma). Pilih label di bagian **Rekam data (untuk uji AI)** pada **HP A** (cocok bila merekam sendirian; bagian ini tersembunyi sampai halaman dibuka dengan `?rekam=1`) atau di dashboard laptop; keduanya sinkron. Lakukan kondisinya 15-20 detik, tekan **Selesai (tanpa label)**, tutup tas, diamkan 5 detik, lalu pengambilan berikutnya. Jangan mengganti label di tengah pengambilan. Label dikosongkan otomatis bila penjagaan dimulai atau dihentikan, jadi tiap sesi dimulai tanpa label.
3. Target 6-8 pengambilan per label, dibagi ke minimal 3 sesi penjagaan (tekan Hentikan Penjagaan lalu Mulai Penjagaan untuk sesi baru, bila bisa dengan pencahayaan berbeda). Sertakan pembukaan tas yang pelan.
4. Sampel tercatat di `sensor_log.csv` (kolom `role` dan `label`), skor dan fitur per detik di `ai_log.csv`. Rekaman lama tanpa label diabaikan.
5. Bandingkan aturan dan AI:

```bash
python ai/compare_real.py --replay          # hitung ulang fitur dari sensor_log.csv, hasil di ai/models/real_comparison.md
python ai/compare_real.py --tune-first     # ambang AI disetel pada kondisi pertama yang memuat tas dibuka
python ai/compare_real.py --selftest       # uji sanity pada window sintetis (tidak menulis ke repo)
```

`compare_real.py` membandingkan lima metode pada window dan segmen yang sama: aturan PPT (ambang absolut, replay `detector.py`), Aturan A (cahaya naik saja), Aturan B (cahaya naik dan getar), AI saja, dan AI + syarat tas dibuka. Window 3 detik pertama setelah label berganti dibuang; alarm segmen butuh 2 window berurutan. Dengan `--tune-first`, ambang AI dipilih dari satu sesi atau kondisi saja lalu diuji di yang lain, sehingga ambang tidak dipilih dan dinilai pada data yang sama. `--kondisi "HH:MM=nama"` menamai kondisi menurut jam mulai (satu kondisi bisa terdiri dari beberapa potongan sesi) dan `--abaikan` mengeluarkan kondisi yang dipakai menyetel simulator dari angka ujian. `--replay` menghitung ulang fitur dari `sensor_log.csv`, sehingga perubahan margin atau model bisa diuji pada rekaman yang sama; tanpa `--replay`, yang dibaca adalah fitur di `ai_log.csv` yang dihitung saat merekam. Model **tidak** dilatih ulang pada rekaman nyata.

Label yang tersedia dan skenario ujinya:

| Label | Kondisi | Seharusnya |
|---|---|---|
| `normal_idle` | Tas diam | tidak alarm |
| `normal_walking` | Tas dibawa berjalan | tidak alarm |
| `normal_handling` | Tas diangkat/diletakkan | tidak alarm |
| `normal_vibration` | Tas digoyang atau terguncang kuat **tanpa dibuka** | tidak alarm |
| `suspicious_open_move` | Tas dibuka, tangan bergerak | alarm |
| `suspicious_open_rummage` | Tas dibuka, isi diaduk | alarm |
| `suspicious_open_pull` | Tas dibuka, barang ditarik | alarm |

Rekaman buatan tim sangat kecil dan hanya indikatif. Jika hasilnya buruk, laporkan apa adanya dan setel `AI_THRESHOLD` / `AI_CONSECUTIVE` di `.env` sebagai langkah awal.

## Keterbatasan

- Data latih sintetis. Besaran sinyalnya dikalibrasi dari satu orang, satu HP, dan satu tas, jadi belum tentu berlaku untuk HP lain (auto-exposure kamera di dalam tas, noise, cara tas dibawa) atau untuk pencuri sungguhan.
- Hanya sumber cahaya kamera yang didukung; satuan `luma / 255` belum dikalibrasi per HP.
- Pencurian yang membawa seluruh tas tanpa membukanya tidak terdeteksi (tanda utamanya tas dibuka). `AI_REQUIRE_LIGHT=false` mengaktifkan deteksi dari gerakan, tetapi model dilatih untuk tidak menganggap gerakan saja mencurigakan.
- Pemilik yang membuka tasnya sendiri juga memicu alarm; belum ada fitur jeda penjagaan.
- Model tidak tahu siapa yang membuka atau memindahkan tas.
- Baseline hanya diturunkan otomatis, tidak dinaikkan. Bila HP B dipindah ke tempat yang lebih terang secara permanen, hentikan lalu mulai ulang penjagaan agar kalibrasi diulang.
- Tas yang dibuka sekejap atau hanya menaikkan cahaya di bawah sekitar 10 luma (margin 0,04) tidak terdeteksi: pada rekaman nyata hanya 55 % segmen tas dibuka yang memicu alarm. Alarm juga butuh 2 evaluasi berurutan.
- Pada konsep "tas dibuka wajib", AI tidak menambah deteksi atas aturan cahaya saja; manfaat terukurnya ada pada baseline relatif dan margin cahaya yang sesuai sensor asli.
