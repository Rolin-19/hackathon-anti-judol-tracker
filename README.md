# Solo Tracker — Hackathon Berantas Web Ilegal di Indo

Pembantu OSINT **mode solo (1 pemain)** untuk lomba `imphnen.dev`: membersihkan URL massal, mengunci duplikat sesuai bentuk catat panitia, menyiapkan URL sah untuk form, dan melacak status submit.

Alat ini membaca catatan sertifikat publik `crt.sh` (tanpa port scan dan tanpa request langsung ke situs target), menyaring tiap temuan lewat `canonicalizer` sesuai aturan bentuk catat panitia, lalu menampilkannya di workbench lokal untuk diproses satu per satu. Kode dipublikasikan di `https://github.com/Rolin-19/hackathon-anti-judol-tracker` dengan lisensi MIT.

## Menjalankan (Windows)

Klik dua kali **`mulai-tracker.bat`**. Script otomatis memasang dependensi bila belum ada, menyalakan server di `http://localhost:8765`, dan membuka browser.

Manual:

```powershell
# Dari folder proyek:
python -m pip install -r requirements.txt
python web\app.py
```

Lalu buka `http://localhost:8765`. Data tersimpan lokal di `data/submissions.json`.

`data/seeds.txt`, `data/submissions.json`, `data/backups/`, `data/rutinitas.log`, dan `data/osint-*.json` hanya ada di mesin pemakai dan terabaikan Git. Yang ikut ke repository hanya kode dan berkas contoh.

## Riset OSINT pasif (crt.sh)

Riset hanya membaca catatan sertifikat publik di `crt.sh`. Tidak ada port scan, tidak ada request langsung ke situs target, dan semua temuan disaring memakai aturan canonicalizer sebelum masuk antrean.

Siapkan daftar benih pribadi di `data/seeds.txt` (diabaikan git). Satu baris berisi domain induk atau kata kunci; baris diawali `#` diabaikan. Contoh tersedia di `data/seeds.example.txt`.

```powershell
# Salin contoh, lalu isi benih sesuai kebutuhan
Copy-Item data\seeds.example.txt data\seeds.txt

# Jalankan riset sekaligus masukkan kandidat unik ke antrean
python -m osint.cli --input data/seeds.txt

# Hanya melihat hasil, tanpa mengubah antrean
python -m osint.cli --input data/seeds.txt --no-import --output data\osint-hasil.json

# Batasi benih dan atur jeda sopan antar permintaan
python -m osint.cli --input data/seeds.txt --limit 5 --delay 1.0 --timeout 15
```

Opsi lain: `--retries` (jumlah percobaan ulang, bawaan 2). Jika `crt.sh` lambat atau gagal, pesan kesalahan ditampilkan dalam bahasa Indonesia dan proses tetap lanjut ke benih berikutnya. Entri yang sudah ada tidak ditimpa, termasuk `Valid`, `Menunggu Penilaian`, dan `Duplikat`.

Tombol **Contoh Benih** di dashboard memuat `data/seeds.example.txt` lewat `GET /api/seeds/example`.

## Otomatisasi rutinitas harian

Satu perintah menjalankan seluruh rutinitas: baca benih → panen `crt.sh` → saring `canonicalizer` → impor aman → cek hidup/mati → backup JSON bertanggal → tulis log.

```powershell
python -m osint.rutinitas --seeds data/seeds.txt --check-alive
```

Mode aman lain:

```powershell
# Tanpa cek hidup/mati (paling cepat)
python -m osint.rutinitas --seeds data/seeds.txt

# Hapus hanya entri Siap Submit yang mati (default: tidak menghapus)
python -m osint.rutinitas --seeds data/seeds.txt --check-alive --prune-dead
```

Aturan penting:

- `data/seeds.txt` belum ada → otomatis memakai `data/seeds.example.txt` dan catatan itu ditulis ke log.
- Benih placeholder contoh (`contoh-*`, `domain-contoh-*`, `mirror-utama-*`, `mirror-pivot-*`, `portal-cadangan-*`, `example.com`, `tokobudi.id`) dilewati sebelum query `crt.sh`; jumlahnya tercatat di log.
- Entri lama tidak pernah ditimpa atau diubah, termasuk `Valid`, `Menunggu Penilaian`, `Duplikat`, dan `Ditolak`.
- Cek hidup memakai pre-check HTTP ringan: status apa pun (`200`/`301`/`403`/`503`) dihitung hidup; timeout/NXDOMAIN/gagal koneksi dihitung mati. Jeda dan timeout bisa diatur lewat `--alive-delay` dan `--alive-timeout`.
- Backup: `data/backups/submissions-YYYY-MM-DD_HHMM.json`.
- Log ringkas: `data/rutinitas.log`. Keduanya diabaikan git.

Jalankan sekali klik lewat **`jalankan-rutinitas-sekali.bat`**. Daftarkan jadwal harian pagi (07.00) dan malam (19.00) lewat PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File daftar-jadwal-otomatis.ps1
```

Hapus jadwal:

```powershell
powershell -ExecutionPolicy Bypass -File daftar-jadwal-otomatis.ps1 -Hapus
```

Tugas Windows memakai waktu lokal komputer; pastikan komputer menggunakan WIB. Workbench menampilkan backup terakhir dan baris log terakhir lewat `GET /api/rutinitas/status`.

## Konsep penting

| Istilah | Arti |
|---|---|
| `submit_url` | URL sah untuk ditempel ke form panitia. Selalu diawali `http://` atau `https://`, tanpa port/user-pass, bukan IP/localhost, maks. 300 karakter. |
| `canonical_key` | Bentuk catat pembanding duplikat: skema dibuang, `www.` dibuang, host lowercase, query/fragment dibuang, trailing slash dibuang, **path tetap case-sensitive**. |

Contoh: `https://www.TokoBudi.id/` → `submit_url = https://www.TokoBudi.id/`, `canonical_key = tokobudi.id`.

## Alur hari-H

1. **Tab Riset & Input**: tempel ratusan URL/domain pada **Tempel Massal** → pilah menjadi *Lolos & Unik*, *Duplikat*, dan *Tidak Sah*. Pada panel **Riset Subdomain Pasif**, cari satu domain/kata kunci atau scan banyak benih dari `crt.sh`, lalu klik **Masukkan Kandidat ke Antrean**.
2. **Tab Antrean Submit**: klik **Cek Hidup** bila perlu, lalu **Salin URL & Tandai Submitted**. URL yang disalin adalah `submit_url` (berprotokol); clipboard langsung terisi, status berubah ke *Menunggu Penilaian*, dan jam **WIB** tercatat untuk tie-breaker.
3. Setelah hasil panitia: tandai **Valid (+1)** atau **Ditolak** (isi alasannya). Entri ditolak boleh **Ajukan Ulang / Re-queue**. Entri **Duplikat** terkunci permanen, tanpa re-queue.
4. Unduh cadangan **JSON/CSV** dari tab Riset & Input. Restore JSON tersedia untuk pemulihan pribadi.

## Jadwal resmi (WIB / Asia/Jakarta)

- Tutup Pendaftaran: **Jumat, 4 Des 2026, 00.00 WIB**
- Mulai Submit: **Sabtu, 5 Des 2026, 00.00 WIB**
- Tutup Submit: **Kamis, 10 Des 2026, 00.00 WIB**
- Pengumuman: **Senin, 14 Des 2026, 00.00 WIB**

## Unit test

```powershell
python -m unittest discover -s canonicalizer -v
python -m unittest discover -s osint -v
```

## Endpoint lokal

- `GET /api/stats`, `GET /api/entries`
- `POST /api/process` — bulk cleaner
- `POST /api/submit` — salin & tandai submitted
- `POST /api/status` — transisi status
- `POST /api/check-alive` — pre-check HTTP (4 detik, UA browser)
- `GET /api/seeds/example` — contoh daftar benih
- `GET /api/rutinitas/status` — backup terakhir dan baris log rutinitas
- `GET /api/subdomains?domain=...` — satu pencarian pasif ke crt.sh
- `POST /api/osint/scan` — scan banyak benih (`seeds`, opsional `timeout`, `retries`, `delay`)
- `POST /api/osint/import` — masukkan kandidat unik ke antrean tanpa menimpa entri lama
- `GET /api/export/json`, `GET /api/export/csv`, `POST /api/import/json`

Ketika `crt.sh` gagal, endpoint mengembalikan HTTP 200 dengan `ok: false` dan pesan bahasa Indonesia pada field `error`, supaya antarmuka tidak putus.

## Lisensi

MIT. Lihat [LICENSE](LICENSE). Copyright 2026 Rolin Stallin Sitorus.
