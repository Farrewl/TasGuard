# Evaluasi pada rekaman nyata

Sumber: `sensor_log.csv`, role B, 48644 sampel kamera (14 sampel non-kamera diabaikan), 7556 evaluasi window.

Rekaman kecil dan dibuat oleh tim: hasil ini indikatif, bukan akurasi deteksi pencurian nyata.

Model: ambang 0.5, 2 window berurutan.

## Skor per label (ambang model)

| Label | Evaluasi | Skor rata-rata | Skor maks | % window >= ambang | Segmen | Segmen alarm |
|---|---|---|---|---|---|---|
| (tanpa label) | 6019 | 0.230 | 0.999 | 18 | 151 | 46 |
| normal_handling | 117 | 0.297 | 0.827 | 20 | 18 | 5 |
| normal_idle | 115 | 0.071 | 0.238 | 0 | 8 | 0 |
| normal_vibration | 133 | 0.113 | 0.814 | 6 | 11 | 1 |
| normal_walking | 74 | 0.337 | 0.828 | 19 | 5 | 4 |
| suspicious_open_move | 534 | 0.673 | 0.999 | 71 | 37 | 36 |
| suspicious_open_pull | 319 | 0.754 | 0.998 | 85 | 33 | 33 |
| suspicious_open_rummage | 245 | 0.689 | 0.996 | 73 | 21 | 20 |

## Sweep ambang pada data nyata

Segmen NORMAL yang alarm = salah alarm; segmen SUSPICIOUS yang alarm = terdeteksi.

| Ambang | Salah alarm (segmen normal) | Terdeteksi (segmen suspicious) |
|---|---|---|
| 0.40 | 16/42 | 90/91 |
| 0.50 | 10/42 | 89/91 |
| 0.60 | 9/42 | 86/91 |
| 0.65 | 9/42 | 86/91 |
| 0.70 | 8/42 | 85/91 |
| 0.80 | 5/42 | 83/91 |
