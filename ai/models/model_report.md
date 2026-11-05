# Laporan model TasGuard

Dilatih pada data SINTETIS. Angka ini mengukur kecocokan dengan definisi pola di generator, bukan akurasi deteksi pencurian nyata.

Ambang keputusan dipilih di validation: skor >= 0.5 pada 2 window berurutan (stride 1 detik). Split test dievaluasi sekali.

Server hanya membunyikan alarm bila skor tinggi DAN tas terbuka (cahaya naik >= 0.4 detik dalam window). Getaran tanpa tas dibuka tidak memicu alarm.

## Tingkat window (test)

| Metode | Accuracy | Precision | Recall | F1 | FP | FN |
|---|---|---|---|---|---|---|
| Aturan lama (aproksimasi) | 0.818 | 0.874 | 0.539 | 0.667 | 79 | 467 |
| Random Forest saja, ambang 0,5 | 0.880 | 0.783 | 0.892 | 0.835 | 250 | 109 |
| Random Forest saja, ambang 0.5 | 0.880 | 0.783 | 0.892 | 0.835 | 250 | 109 |
| Random Forest + syarat tas dibuka (dipakai server) | 0.839 | 0.830 | 0.660 | 0.735 | 137 | 345 |

ROC AUC Random Forest: 0.9347

## Tingkat sesi (test): persentase sesi yang memicu alarm

| Metode | Sesi NORMAL alarm (salah alarm) | Sesi SUSPICIOUS terdeteksi |
|---|---|---|
| Aturan lama | 4.3 % | 65.7 % |
| Random Forest saja | 9.0 % | 95.7 % |
| Random Forest + syarat tas dibuka (dipakai server) | 5.0 % | 77.7 % |

Per skenario (Random Forest + syarat tas dibuka, % sesi alarm):

| Skenario | % |
|---|---|
| normal_handling | 2.7 |
| normal_idle | 6.7 |
| normal_vibration | 0.0 |
| normal_walking | 10.7 |
| suspicious_combined | 90.7 |
| suspicious_open_move | 73.3 |
| suspicious_open_pull | 70.7 |
| suspicious_open_rummage | 76.0 |

## Sweep ambang di validation

| Ambang | Sesi NORMAL alarm % | Sesi SUSPICIOUS terdeteksi % |
|---|---|---|
| 0.30 | 4.3 | 76.3 |
| 0.35 | 4.3 | 76.3 |
| 0.40 | 4.3 | 76.3 |
| 0.45 | 4.3 | 76.3 |
| 0.50 | 3.7 | 76.3 |
| 0.55 | 3.3 | 76.3 |
| 0.60 | 2.3 | 76.3 |
| 0.65 | 2.3 | 76.3 |
| 0.70 | 2.0 | 75.7 |
| 0.75 | 1.0 | 74.3 |
| 0.80 | 1.0 | 72.7 |
| 0.85 | 0.7 | 69.7 |
| 0.90 | 0.7 | 66.0 |

## Kepentingan fitur

| Fitur | Importance |
|---|---|
| light_elevated_duration | 0.338 |
| light_motion_coincidence | 0.1757 |
| accel_std | 0.1037 |
| light_change_rate | 0.0818 |
| accel_max | 0.0701 |
| bright_ratio | 0.0666 |
| motion_duration | 0.0586 |
| accel_change_rate | 0.0562 |
| accel_mean | 0.0247 |
| light_delta | 0.0127 |
| motion_event_count | 0.0119 |
