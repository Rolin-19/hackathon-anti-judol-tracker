# Solo Tracker — Hackathon Berantas Web Ilegal di Indo

Pembantu OSINT **mode solo (1 pemain)** untuk lomba `imphnen.dev`: membersihkan URL massal, mengunci duplikat sesuai bentuk catat panitia, menyiapkan URL sah untuk form, dan melacak status submit.

## Menjalankan (Windows)

Klik dua kali **`mulai-tracker.bat`**. Script otomatis memasang dependensi bila belum ada, menyalakan server di `http://localhost:8765`, dan membuka browser.

Manual:

```powershell
cd "C:\agent Portofolio\hackathon-anti-judol-tracker"
python -m pip install -r requirements.txt
python web\app.py
```

Lalu buka `http://localhost:8765`. Data tersimpan lokal di `data/submissions.json`.

## Konsep penting

| Istilah | Arti |
|---|---|
| `submit_url` | URL sah untuk ditempel ke form panitia. Selalu diawali `http://` atau `https://`, tanpa port/user-pass, bukan IP/localhost, maks. 300 karakter. |
| `canonical_key` | Bentuk catat pembanding duplikat: skema dibuang, `www.` dibuang, host lowercase, query/fragment dibuang, trailing slash dibuang, **path tetap case-sensitive**. |

Contoh: `https://www.TokoBudi.id/` → `submit_url = https://www.TokoBudi.id/`, `canonical_key = tokobudi.id`.

## Alur hari-H

1. **Panel 1 — Bulk Cleaner**: tempel ratusan URL/domain → pilah menjadi *Lolos & Unik*, *Duplikat*, dan *Tidak Sah*. Cari subdomain lewat `crt.sh`, lalu masukkan temuan ke antrean.
2. **Panel 2 — Antrean**: klik **Cek Hidup** bila perlu, lalu **Salin URL & Tandai Submitted**. URL yang disalin adalah `submit_url` (berprotokol); clipboard langsung terisi, status berubah ke *Menunggu Penilaian*, dan jam **WIB** tercatat untuk tie-breaker.
3. Setelah hasil panitia: tandai **Valid (+1)** atau **Ditolak** (isi alasannya). Entri ditolak boleh **Ajukan Ulang / Re-queue**. Entri **Duplikat** terkunci permanen, tanpa re-queue.
4. Unduh cadangan **JSON/CSV** dari Panel 2. Restore JSON tersedia untuk pemulihan pribadi.

## Jadwal resmi (WIB / Asia/Jakarta)

- Tutup Pendaftaran: **Jumat, 4 Des 2026, 00.00 WIB**
- Mulai Submit: **Sabtu, 5 Des 2026, 00.00 WIB**
- Tutup Submit: **Kamis, 10 Des 2026, 00.00 WIB**
- Pengumuman: **Senin, 14 Des 2026, 00.00 WIB**

## Unit test

```powershell
python -m unittest discover -s canonicalizer -v
```

## Endpoint lokal

- `GET /api/stats`, `GET /api/entries`
- `POST /api/process` — bulk cleaner
- `POST /api/submit` — salin & tandai submitted
- `POST /api/status` — transisi status
- `POST /api/check-alive` — pre-check HTTP (4 detik, UA browser)
- `GET /api/subdomains?domain=...` — OSINT crt.sh
- `GET /api/export/json`, `GET /api/export/csv`, `POST /api/import/json`
