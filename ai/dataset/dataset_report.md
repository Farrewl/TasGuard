# Laporan audit dataset TasGuard

Konfigurasi dibaca dari `dataset_config.json`: 500 sesi per skenario, 5 Hz, 7 detik per sesi, window 3 detik, stride 1 detik, ambang label 0.4, seed 20261105. Semua angka di bawah dihitung oleh `audit_dataset.py` dari file CSV.

## 1. Ukuran

| Item | Nilai |
|---|---|
| Sesi | 4,000 |
| Baris sampel mentah | 140,000 |
| Baris window | 20,000 |
| Sampel per sesi | 35 |
| Window per sesi | 5 |
| Baris log artefak | 7,927 |

## 2. Distribusi kelas, skenario, dan split

| Label window | Window | Porsi |
|---|---|---|
| NORMAL | 13,327 | 66.64 % |
| SUSPICIOUS | 6,673 | 33.36 % |

Window per skenario dan label (tiap skenario punya jumlah sesi yang sama):

| scenario | NORMAL | SUSPICIOUS | share_suspicious_% |
|---|---|---|---|
| normal_handling | 2,500 | 0 | 0.000 |
| normal_idle | 2,500 | 0 | 0.000 |
| normal_vibration | 2,500 | 0 | 0.000 |
| normal_walking | 2,500 | 0 | 0.000 |
| suspicious_combined | 743 | 1,757 | 70.300 |
| suspicious_open_move | 939 | 1,561 | 62.400 |
| suspicious_open_pull | 912 | 1,588 | 63.500 |
| suspicious_open_rummage | 733 | 1,767 | 70.700 |

Distribusi split (sesi dibagi di dalam tiap skenario):

| split | NORMAL | SUSPICIOUS | Sesi | Baris mentah |
|---|---|---|---|---|
| test | 1,986 | 1,014 | 600 | 21,000 |
| train | 9,343 | 4,657 | 2,800 | 98,000 |
| validation | 1,998 | 1,002 | 600 | 21,000 |

Sesi per skenario dan split: train=350, validation=75, test=75 (sama untuk semua 8 skenario: ya).

## 3. Distribusi event_fraction

| event_fraction | Window | Label |
|---|---|---|
| 0.0000 | 11,236 | NORMAL |
| 0.0667 | 293 | NORMAL |
| 0.1333 | 365 | NORMAL |
| 0.2000 | 424 | NORMAL |
| 0.2667 | 455 | NORMAL |
| 0.3333 | 554 | NORMAL |
| 0.4000 | 560 | SUSPICIOUS |
| 0.4667 | 704 | SUSPICIOUS |
| 0.5333 | 807 | SUSPICIOUS |
| 0.6000 | 748 | SUSPICIOUS |
| 0.6667 | 727 | SUSPICIOUS |
| 0.7333 | 661 | SUSPICIOUS |
| 0.8000 | 652 | SUSPICIOUS |
| 0.8667 | 562 | SUSPICIOUS |
| 0.9333 | 501 | SUSPICIOUS |
| 1.0000 | 751 | SUSPICIOUS |

0 dari 2000 sesi skenario mencurigakan (0.0%) tidak punya window SUSPICIOUS, karena kejadiannya singkat atau berada di tepi sesi sehingga tidak pernah mencapai ambang 0.4 dalam satu window. Porsi window SUSPICIOUS menurut posisi window:

| window_id | suspicious_% |
|---|---|
| 0.0 | 12.6 |
| 1.0 | 31.1 |
| 2.0 | 42.9 |
| 3.0 | 45.1 |
| 4.0 | 35.1 |

Window 0 jarang SUSPICIOUS karena setiap kejadian dimulai setelah periode kalibrasi 1 detik. `window_id` bukan fitur model dan tidak boleh diberikan ke model.

## 4. Rentang nilai sensor

| Sensor | Keluarga skenario | Min | 1% | Median | 99% | Maks |
|---|---|---|---|---|---|---|
| light_value | NORMAL | 0.000 | 0.000 | 0.034 | 0.388 | 0.927 |
| light_value | SUSPICIOUS | 0.000 | 0.000 | 0.072 | 0.748 | 1.000 |
| accel_mag | NORMAL | 0.000 | 3.800 | 9.815 | 40.691 | 78.480 |
| accel_mag | SUSPICIOUS | 0.000 | 3.935 | 9.814 | 18.246 | 29.613 |

`light_value` adalah nilai simulasi ternormalisasi yang dipotong ke [0.0, 1.0], bukan lux. `accel_mag` dalam m/s2 dan dipotong ke [0.0, 78.48], dengan asumsi skala penuh 4 g.

Median `accel_std` window per skenario (m/s2), untuk memeriksa urutan tingkat aktivitas masuk akal:

| scenario | median_accel_std |
|---|---|
| normal_handling | 2.021 |
| normal_idle | 0.077 |
| normal_vibration | 7.308 |
| normal_walking | 4.859 |
| suspicious_combined | 0.775 |
| suspicious_open_move | 0.654 |
| suspicious_open_pull | 1.231 |
| suspicious_open_rummage | 1.058 |

Variasi antar sesi, memakai median periode kalibrasi tiap sesi:

| Ukuran | Min | 10% | Median | 90% | Maks |
|---|---|---|---|---|---|
| Baseline cahaya | 0.000 | 0.004 | 0.030 | 0.268 | 0.437 |
| Baseline akselerasi (m/s2) | 9.155 | 9.588 | 9.811 | 10.032 | 10.459 |

## 5. Statistik fitur

| Fitur | Min | Maks | Rata-rata NORMAL | Median NORMAL | Rata-rata SUSPICIOUS | Median SUSPICIOUS |
|---|---|---|---|---|---|---|
| light_mean | 0 | 1 | 0.1017 | 0.04848 | 0.1584 | 0.1243 |
| light_max | 0 | 1 | 0.1621 | 0.1032 | 0.349 | 0.3048 |
| light_min | 0 | 1 | 0.07678 | 0.01712 | 0.0773 | 0.01932 |
| light_delta | -0.9913 | 0.9987 | 0.006219 | 0.0002307 | 0.006436 | 0.000724 |
| light_change_rate | 0 | 1.252 | 0.06767 | 0.02819 | 0.1893 | 0.1391 |
| light_elevated_duration | 0 | 3 | 0.07145 | 0 | 0.7493 | 0.6 |
| bright_ratio | 0 | 1 | 0.01161 | 0 | 0.1212 | 0 |
| accel_mean | 7.433 | 41.6 | 10.72 | 9.992 | 9.947 | 9.861 |
| accel_max | 9.285 | 78.48 | 19.06 | 14.31 | 13.38 | 12.27 |
| accel_std | 0.012 | 34.59 | 3.532 | 1.977 | 1.573 | 1.133 |
| accel_change_rate | 0.06034 | 305.2 | 20.05 | 6.947 | 9.24 | 5.419 |
| motion_duration | 0 | 3 | 1.036 | 0.6 | 1.225 | 1 |
| motion_event_count | 0 | 5 | 0.9489 | 1 | 1.529 | 1 |
| light_motion_coincidence | 0 | 1 | 0.01138 | 0 | 0.1355 | 0.06667 |

## 6. Nilai hilang dan tak hingga

| Tabel | Sel kosong | Nilai tak hingga |
|---|---|---|
| Sampel mentah | 0 | 0 |
| Fitur window | 0 | 0 |

Pembacaan yang hilang pada simulasi tidak diekspor sebagai NaN, melainkan diisi dengan pembacaan sebelumnya sehingga kolom sensor tampak seperti nilai yang tertahan. Log artefak mencatatnya sebagai `dropout_locf`, terpisah dari `held_value` (pembacaan macet), meskipun keduanya tampak sama di CSV.

## 7. Artefak sensor dan pencilan

Jumlah sampel terdampak dari `tasguard_artifact_log.csv`. Kedua keluarga mendapat peluang artefak yang sama:

| sensor | artifact_type | NORMAL | SUSPICIOUS | Kejadian | Sesi terdampak | % baris mentah |
|---|---|---|---|---|---|---|
| accel | clip | 110 | 2 | 108 | 77 | 0.080 |
| accel | dropout_locf | 332 | 288 | 308 | 308 | 0.443 |
| accel | held_value | 415 | 408 | 405 | 405 | 0.588 |
| accel | spike | 929 | 958 | 1,887 | 1,252 | 1.348 |
| light | clip | 5,117 | 4,570 | 2,634 | 1,481 | 6.919 |
| light | dropout_locf | 296 | 288 | 296 | 296 | 0.417 |
| light | held_value | 391 | 405 | 395 | 395 | 0.569 |
| light | spike | 970 | 924 | 1,894 | 1,256 | 1.353 |

Porsi sesi dengan minimal satu artefak tercatat: NORMAL 76.4%, SUSPICIOUS 75.7%.

Window dengan minimal satu fitur di luar pagar Tukey (1,5 x IQR atas seluruh window). Ini pencilan statistik, belum tentu gangguan, dan tidak ada yang dibuang:

| label | window | porsi_kelas_% |
|---|---|---|
| NORMAL | 4,085 | 30.650 |
| SUSPICIOUS | 4,035 | 60.470 |

Porsi window yang memuat minimal satu sampel lonjakan hasil suntikan: NORMAL 32.2 %, SUSPICIOUS 32.9 %. Porsi yang ditandai Tukey lebih tinggi pada window SUSPICIOUS karena window itu memuat aktivitas simulasi nyata (sentakan, paparan cahaya), bukan karena artefak terkonsentrasi di sana. Lonjakan tersebar merata di kedua kelas sehingga tidak bisa menjadi jalan pintas.

Istilah yang dipakai di proyek ini: pembacaan valid tetapi tidak biasa adalah kejadian simulasi nyata seperti sentakan atau paparan cahaya kuat. Gangguan sensor adalah lonjakan, nilai macet, atau clipping yang tercatat di log artefak. Nilai tidak valid atau hilang adalah dropout, yang pada dataset ini diwakili pengisian dengan nilai sebelumnya.

## 8. Kebocoran sesi dan kebocoran target

| Pemeriksaan | Jumlah |
|---|---|
| Sesi mentah di lebih dari satu split | 0 |
| Sesi window di lebih dari satu split | 0 |
| Ketidakcocokan split mentah/window | 0 |

ID sesi anonim dan diacak (nama skenario ditemukan di ID: tidak). Masukan model hanya 14 kolom fitur. `event_fraction`, `label`, `scenario`, `session_id`, `window_id`, timestamp, dan `split` tidak boleh menjadi masukan model.

## 9. Tumpang tindih kelas dan tinjauan jalan pintas

Keterpisahan satu fitur pada split train (AUC 0,5 = tanpa informasi, 1,0 = sempurna). Irisan adalah luas bersama dua histogram ternormalisasi 50 bin:

| Fitur | AUC (train) | Kekuatan max(AUC,1-AUC) | Irisan histogram |
|---|---|---|---|
| light_mean | 0.658 | 0.658 | 0.743 |
| light_max | 0.756 | 0.756 | 0.642 |
| light_min | 0.513 | 0.513 | 0.959 |
| light_delta | 0.506 | 0.506 | 0.737 |
| light_change_rate | 0.801 | 0.801 | 0.505 |
| light_elevated_duration | 0.871 | 0.871 | 0.356 |
| bright_ratio | 0.709 | 0.709 | 0.631 |
| accel_mean | 0.382 | 0.618 | 0.802 |
| accel_max | 0.451 | 0.549 | 0.593 |
| accel_std | 0.440 | 0.560 | 0.673 |
| accel_change_rate | 0.475 | 0.525 | 0.762 |
| motion_duration | 0.602 | 0.602 | 0.627 |
| motion_event_count | 0.675 | 0.675 | 0.740 |
| light_motion_coincidence | 0.787 | 0.787 | 0.467 |

Fitur tunggal terkuat adalah `light_elevated_duration` dengan AUC 0.871. Tidak ada fitur yang memisahkan kelas sendirian (irisan terkecil 0.36).

| Contoh tumpang tindih | Nilai |
|---|---|
| Window NORMAL dengan light_max sama atau di atas median SUSPICIOUS | 17.8% |
| Window NORMAL dengan accel_max sama atau di atas median SUSPICIOUS | 56.8% |
| Window SUSPICIOUS yang tidak lebih aktif dari persentil ke-75 window NORMAL (nilai mutlak light_delta dan accel_std) | 50.2% |
| Window skenario normal dengan accel_max di atas 15 m/s2 | 56.3% |
| Window NORMAL dari skenario mencurigakan (kejadian di luar window atau di bawah ambang) | 3,327 |

Uji kewajaran dengan Random Forest tanpa penyetelan (300 pohon, class_weight balanced), dilatih di train dan dinilai di validation. Split test tidak dipakai. Ini pemeriksaan tingkat kesulitan, bukan hasil model:

| Metrik | Validation |
|---|---|
| Accuracy | 0.896 |
| Balanced accuracy | 0.888 |
| F1 (SUSPICIOUS) | 0.847 |
| ROC AUC | 0.951 |

Lima fitur terpenting: light_elevated_duration 0.208, light_motion_coincidence 0.100, accel_std 0.095, light_change_rate 0.095, accel_max 0.077.

Tinjauan jalan pintas, berdasarkan angka di atas:

1. Tidak ada fitur tunggal yang menentukan (AUC terbaik 0.871). Fitur cahaya dan akselerometer sama-sama membawa sinyal.
2. Keluarga skenario terhubung ke label secara konstruksi: skenario NORMAL tidak pernah memuat kejadian. Kolom `scenario` tidak boleh menjadi masukan model. Karena berjalan biasa muncul di sebagian sesi mencurigakan, aktivitas akselerometer saja tidak cukup untuk menentukan kelas.
3. Posisi kejadian berpengaruh: hanya 12.6 % window 0 yang SUSPICIOUS dibanding 45.1 % pada posisi tersibuk. Model yang melihat indeks window akan memanfaatkannya, sehingga `window_id` tidak dipakai sebagai masukan.
4. Lonjakan dan nilai macet tercatat dengan laju sama di kedua keluarga, sehingga bukan jalan pintas kelas.
5. Label bergantung pada waktu kejadian, bukan kekuatan sensor. Sebagian window SUSPICIOUS memuat sinyal lemah dan sebagian window NORMAL memuat sinyal kuat. Sebagian galat model apa pun pada data ini karenanya tidak dapat dihindari.

## 10. Batas audit ini

Audit ini memeriksa konsistensi internal dan tingkat kesulitan sebuah simulasi. Audit tidak dapat menunjukkan bahwa perilaku sensor simulasi cocok dengan HP, tas, atau pencurian yang sebenarnya. Untuk itu diperlukan rekaman nyata.
