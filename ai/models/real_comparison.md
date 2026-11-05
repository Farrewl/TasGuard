# Perbandingan aturan dan AI pada rekaman nyata

Rekaman dibuat tim dengan memeragakan sendiri kondisi di depan HP (bukan pencuri sungguhan), jumlahnya kecil, sehingga hasil ini **indikatif**, bukan akurasi deteksi pencurian di dunia nyata. Model dilatih pada data sintetis dan **tidak** dilatih ulang pada rekaman ini.

- Sesi penjagaan dalam rekaman: 4; window dipakai: 235; segmen: 32.
- Window 3 detik pertama setelah label berganti dibuang (zona batas).
- Ambang AI 0.50; alarm segmen = 2 window berurutan.
- Ambang AI dari model (tidak disetel pada rekaman ini). Dikeluarkan dari angka ujian (dipakai menyetel simulator): "0a normal awal (dipakai menyetel simulator)", "1 atas, buka lebar (dipakai menyetel simulator)", "2 atas, buka sedikit (dipakai menyetel simulator)".

## Jumlah data per label

| Label | Segmen | Window |
|---|---|---|
| normal_handling | 8 | 27 |
| normal_vibration | 2 | 14 |
| suspicious_open_move | 8 | 99 |
| suspicious_open_pull | 9 | 63 |
| suspicious_open_rummage | 5 | 32 |

## Hasil keseluruhan

| Metode | F1 window | Presisi | Recall | Salah alarm segmen normal | Terdeteksi segmen mencurigakan |
|---|---|---|---|---|---|
| Aturan PPT minggu lalu (ambang absolut) | 0.00 | 0.00 | 0.00 | 0/10 (0 %) | 0/22 (0 %) |
| Aturan A: cahaya naik saja | 0.45 | 1.00 | 0.29 | 0/10 (0 %) | 12/22 (55 %) |
| Aturan B: cahaya naik DAN getar | 0.40 | 1.00 | 0.25 | 0/10 (0 %) | 12/22 (55 %) |
| AI saja (tanpa syarat tas dibuka) | 0.89 | 0.98 | 0.82 | 2/10 (20 %) | 20/22 (91 %) |
| AI + syarat tas dibuka (dipakai server) | 0.45 | 1.00 | 0.29 | 0/10 (0 %) | 12/22 (55 %) |

Kolom segmen: jumlah segmen yang membunyikan alarm dibanding jumlah segmen pada kelasnya.

## Per label (segmen yang membunyikan alarm)

| Label | Aturan PPT minggu lalu | Aturan A | Aturan B | AI saja | AI + syarat tas dibuka |
|---|---|---|---|---|---|
| normal_handling | 0/8 (0 %) | 0/8 (0 %) | 0/8 (0 %) | 1/8 (12 %) | 0/8 (0 %) |
| normal_vibration | 0/2 (0 %) | 0/2 (0 %) | 0/2 (0 %) | 1/2 (50 %) | 0/2 (0 %) |
| suspicious_open_move | 0/8 (0 %) | 2/8 (25 %) | 2/8 (25 %) | 7/8 (88 %) | 2/8 (25 %) |
| suspicious_open_pull | 0/9 (0 %) | 5/9 (56 %) | 5/9 (56 %) | 8/9 (89 %) | 5/9 (56 %) |
| suspicious_open_rummage | 0/5 (0 %) | 5/5 (100 %) | 5/5 (100 %) | 5/5 (100 %) | 5/5 (100 %) |

## Per kondisi (untuk membandingkan kondisi rekaman)

| Kondisi (mulai) | Segmen normal | Segmen dibuka | Aturan PPT: salah alarm / terdeteksi | Aturan A (cahaya saja): salah alarm / terdeteksi | AI + syarat tas dibuka: salah alarm / terdeteksi |
|---|---|---|---|---|---|
| 0b normal akhir (ujian) (15:50) | 10 | 0 | 0/10 / 0/0 | 0/10 / 0/0 | 0/10 / 0/0 |
| 3 miring, buka lebar (ujian) (16:55) | 0 | 22 | 0/0 / 0/22 | 0/0 / 12/22 | 0/0 / 12/22 |

## Kesimpulan otomatis

AI SETARA aturan terbaik: F1 window 0.45 vs 0.45 (Aturan A: cahaya naik saja); selisih < 0.05. Pada data ini AI tidak terbukti lebih baik dari aturan; nilai sistem ada pada baseline adaptif dan skor yang bisa disetel.

Catatan: perbandingan sederhana pada sampel kecil; selisih beberapa segmen bisa kebetulan. Pernyataan di PPT harus mengikuti angka di atas, termasuk bila AI tidak lebih baik.
