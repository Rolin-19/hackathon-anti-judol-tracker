"""
osint/rutinitas.py
Satu siklus otomatisasi OSINT pasif mode solo.

Alur:
  baca data/seeds.txt -> panen crt.sh -> saring canonicalizer
  -> impor aman ke Siap Submit -> cek hidup/mati -> backup JSON bertanggal
  -> tulis data/rutinitas.log

Prinsip keras:
- Hanya membaca catatan sertifikat publik crt.sh dan melakukan pre-check HTTP
  ringan. Tidak ada port scan atau akses agresif ke situs target.
- Entri yang sudah ada tidak pernah ditimpa atau diubah, termasuk status
  Valid, Menunggu Penilaian, Duplikat, dan Ditolak.
- Semua jaringan memakai timeout dan jeda sopan. Penghapusan entri mati hanya
  terjadi bila --prune-dead dipakai, dan hanya pada entri Siap Submit.
- Benih placeholder contoh dilewati sebelum query crt.sh, dan dihitung di log.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple, Union

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonicalizer.rules import evaluate
from osint.crtsh import collect_subdomains
from web.app import USER_AGENT, WIB, SoloTracker


def _resolusi(path: Union[str, Path], root: Path) -> Path:
    """Ubah path relatif menjadi absolut berdasarkan akar proyek."""
    p = Path(path)
    return p if p.is_absolute() else root / p


def baca_benih(path: Path, contoh: Optional[Path] = None) -> Tuple[List[str], str]:
    """
    Baca benih: baris kosong dan komentar diawali # diabaikan.

    Bila berkas utama belum ada, pakai berkas contoh dan kembalikan catatan
    supaya diprint serta ditulis ke log.
    """
    sumber = path
    catatan = ""

    if not path.exists():
        if contoh and contoh.exists():
            sumber = contoh
            catatan = f"{path.name} belum ada; memakai {contoh.name}"
        else:
            return [], f"{path.name} belum ada dan berkas contoh tidak ditemukan"

    try:
        baris = sumber.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], f"Gagal membaca {sumber.name}: {exc}"

    hasil: List[str] = []
    terlihat = set()
    for item in baris:
        value = item.split("#", 1)[0].strip()
        if not value or value in terlihat:
            continue
        terlihat.add(value)
        hasil.append(value)
    return hasil, catatan


# Domain contoh yang tidak boleh dibuang ke crt.sh.
PLACEHOLDER_AWALAN = (
    "contoh-",
    "domain-contoh-",
    "mirror-utama-",
    "mirror-pivot-",
    "portal-cadangan-",
)
PLACEHOLDER_PERSIS = {"example.com", "tokobudi.id"}


def adalah_placeholder(benih: str) -> bool:
    """Kenali domain contoh supaya tidak membuang request ke crt.sh."""
    nilai = str(benih).strip().lower()
    if not nilai:
        return True
    host = nilai.split("/", 1)[0].split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if host in PLACEHOLDER_PERSIS:
        return True
    return any(host.startswith(awalan) for awalan in PLACEHOLDER_AWALAN)


def pilih_benih(benih: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Pisahkan benih asli dari placeholder contoh."""
    dipakai: List[str] = []
    dilewati: List[str] = []
    for item in benih:
        if adalah_placeholder(item):
            dilewati.append(item)
        else:
            dipakai.append(item)
    return dipakai, dilewati


def kanon(kandidat) -> Optional[object]:
    """
    Saring kandidat dua kali lewat canonicalizer.

    Evaluasi kedua memakai canonical_key supaya submit_url akhir pasti https,
    sementara huruf besar-kecil path tetap dipertahankan.
    """
    if not isinstance(kandidat, str):
        return None
    awal = evaluate(kandidat)
    if not awal.valid:
        return None
    akhir = evaluate(awal.canonical_key)
    if not akhir.valid or not akhir.submit_url.startswith("https://"):
        return None
    return akhir


def entri_baru(r, raw: str, waktu: datetime) -> dict:
    """Bangun entri Siap Submit sesuai format penyimpanan solo."""
    return {
        "submit_url": r.submit_url,
        "canonical_key": r.canonical_key,
        "raw": raw,
        "status": "Siap Submit",
        "score": 0,
        "reason": r.reason,
        "reject_reason": "",
        "created_at": waktu.isoformat(timespec="seconds"),
        "submitted_at": "",
    }


def impor_aman(tracker: SoloTracker, kandidat: Iterable[str], waktu: datetime) -> dict:
    """
    Tambahkan kandidat baru tanpa menyentuh entri lama.

    Beda dengan process_bulk(): kunci Ditolak ikut dilewati, bukan di-replace.
    """
    ditambah: Dict[str, dict] = {}
    dilewati = 0
    tidak_sah = 0

    for item in kandidat:
        r = kanon(item)
        if r is None:
            tidak_sah += 1
            continue

        key = r.canonical_key
        if key in tracker.entries or key in ditambah:
            dilewati += 1
            continue

        ditambah[key] = entri_baru(r, item, waktu)

    if ditambah:
        tracker.entries.update(ditambah)
        tracker.save()

    return {
        "added": len(ditambah),
        "skipped_existing": dilewati,
        "invalid": tidak_sah,
        "keys": sorted(ditambah),
    }


def _ambil_hidup_default(url: str, timeout: int):
    """Pre-check HTTP ringan: ikuti aturan endpoint /api/check-alive."""
    return requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        allow_redirects=False,
    )


def periksa_hidup(
    entris: Iterable[Tuple[str, str]],
    *,
    timeout: int = 4,
    delay: float = 0.2,
    ambil: Optional[Callable[[str, int], object]] = None,
    jeda: Optional[Callable[[float], None]] = None,
) -> List[dict]:
    """
    Klasifikasi hidup/mati.

    Status HTTP apa pun dihitung hidup. Hanya timeout, NXDOMAIN, dan kegagalan
    koneksi yang dihitung mati.
    """
    ambil_data = ambil or _ambil_hidup_default
    istirahat = jeda or time.sleep
    hasil: List[dict] = []
    terlihat = set()

    for index, (key, url) in enumerate(entris):
        if index > 0 and delay > 0:
            istirahat(delay)

        r = evaluate(url)
        target = r.submit_url if r.valid else str(url).strip()
        if target in terlihat:
            continue
        terlihat.add(target)

        try:
            respons = ambil_data(target, timeout)
            kode = getattr(respons, "status_code", None)
            hasil.append({
                "canonical_key": key,
                "url": target,
                "alive": True,
                "label": f"Hidup (HTTP {kode})",
                "http_code": kode,
            })
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as exc:
            hasil.append({
                "canonical_key": key,
                "url": target,
                "alive": False,
                "label": "Mati / NXDOMAIN",
                "http_code": None,
                "detail": type(exc).__name__,
            })

    return hasil


def buang_mati(tracker: SoloTracker, hasil_hidup: List[dict]) -> List[str]:
    """
    Hapus hanya entri Siap Submit yang mati.

    Entri Valid, Menunggu Penilaian, Duplikat, dan Ditolak tetap aman.
    """
    kunci_mati = {h["canonical_key"] for h in hasil_hidup if not h["alive"]}
    terhapus: List[str] = []

    for key in sorted(kunci_mati):
        entri = tracker.entries.get(key)
        if entri and entri.get("status") == "Siap Submit":
            del tracker.entries[key]
            terhapus.append(key)

    if terhapus:
        tracker.save()
    return terhapus


def buat_backup(tracker: SoloTracker, folder: Path, waktu: datetime) -> Path:
    """Simpan snapshot JSON dengan nama submissions-YYYY-MM-DD_HHMM.json."""
    folder.mkdir(parents=True, exist_ok=True)
    nama = "submissions-" + waktu.strftime("%Y-%m-%d_%H%M") + ".json"
    path = folder / nama
    payload = {
        "mode": "solo",
        "entries": tracker.entries,
        "summary": tracker.summary(),
        "exported_at": waktu.isoformat(timespec="seconds"),
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def tulis_log(path: Path, baris: str, waktu: datetime) -> None:
    """Tambah satu baris log WIB ke data/rutinitas.log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(f"[{waktu.isoformat(timespec='seconds')}] {baris}\n")


def jalankan_siklus(
    seeds: Union[str, Path] = "data/seeds.txt",
    *,
    root: Optional[Path] = None,
    submissions: Union[str, Path] = "data/submissions.json",
    log: Union[str, Path] = "data/rutinitas.log",
    backups: Union[str, Path] = "data/backups",
    timeout: int = 12,
    retries: int = 2,
    delay: float = 0.4,
    check_alive: bool = False,
    prune_dead: bool = False,
    alive_timeout: int = 4,
    alive_delay: float = 0.2,
    fetcher: Optional[Callable[[str, int], object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    alive_fetcher: Optional[Callable[[str, int], object]] = None,
    waktu: Optional[datetime] = None,
) -> Dict[str, object]:
    """Jalankan satu siklus penuh dan kembalikan ringkasan terstruktur."""
    akar = Path(root) if root else ROOT
    seeds_path = _resolusi(seeds, akar)
    contoh_path = akar / "data" / "seeds.example.txt"
    penyimpanan_path = _resolusi(submissions, akar)
    log_path = _resolusi(log, akar)
    backup_dir = _resolusi(backups, akar)
    waktu = waktu or datetime.now(WIB)

    benih, catatan_benih = baca_benih(seeds_path, contoh_path)
    benih_dipakai, benih_placeholder = pilih_benih(benih)
    panen = collect_subdomains(
        benih_dipakai,
        timeout=timeout,
        retries=retries,
        delay=delay,
        fetcher=fetcher,
        sleeper=sleeper,
    )

    tracker = SoloTracker(penyimpanan_path)
    impor = impor_aman(tracker, panen["candidates"], waktu)

    hasil_hidup: List[dict] = []
    terhapus: List[str] = []
    if check_alive:
        siap = sorted(
            [
                (key, entri.get("submit_url", ""))
                for key, entri in tracker.entries.items()
                if entri.get("status") == "Siap Submit"
            ],
            key=lambda item: item[0],
        )
        hasil_hidup = periksa_hidup(
            siap,
            timeout=alive_timeout,
            delay=alive_delay,
            ambil=alive_fetcher,
            jeda=sleeper,
        )
        if prune_dead:
            terhapus = buang_mati(tracker, hasil_hidup)

    backup_path = buat_backup(tracker, backup_dir, waktu)

    hidup = sum(1 for h in hasil_hidup if h["alive"])
    mati = len(hasil_hidup) - hidup

    baris_log = " | ".join([
        f"benih={len(benih)} dipakai={len(benih_dipakai)} "
        f"placeholder={len(benih_placeholder)} sumber={seeds_path.name} "
        f"kandidat={panen['total']} gagal_crtsh={panen['error_count']}",
        f"masuk_siap_submit={impor['added']} "
        f"dilewati={impor['skipped_existing']} tidak_sah={impor['invalid']}",
        f"hidup={hidup} mati={mati} dihapus={len(terhapus)}",
        f"backup={backup_path.name}",
    ])
    if catatan_benih:
        baris_log = f"{catatan_benih} | {baris_log}"
    tulis_log(log_path, baris_log, waktu)

    print("Ringkasan rutinitas solo")
    if catatan_benih:
        print(f"- Catatan: {catatan_benih}")
    print(f"- Benih diproses: {len(benih_dipakai)} dari {len(benih)}")
    print(f"- Placeholder dilewati: {len(benih_placeholder)} (tanpa query crt.sh)")
    if benih_placeholder:
        print(f"- Daftar placeholder: {', '.join(benih_placeholder)}")
    print(f"- Kandidat crt.sh: {panen['total']} (gagal: {panen['error_count']})")
    print(f"- Masuk Siap Submit: {impor['added']}")
    print(f"- Dilewati (sudah ada): {impor['skipped_existing']}")
    print(f"- Tidak sah: {impor['invalid']}")
    print(f"- Hidup / Mati: {hidup} / {mati}")
    print(f"- Dihapus (prune): {len(terhapus)}")
    print(f"- Backup: {backup_path}")
    print(f"- Log: {log_path}")

    return {
        "ok": True,
        "waktu": waktu.isoformat(timespec="seconds"),
        "sumber_benih": str(seeds_path),
        "catatan_benih": catatan_benih,
        "benih": len(benih),
        "benih_dipakai": len(benih_dipakai),
        "placeholder": len(benih_placeholder),
        "daftar_placeholder": benih_placeholder,
        "kandidat": panen["total"],
        "gagal_crtsh": panen["error_count"],
        "hasil_panen": panen["results"],
        "ditambah": impor["added"],
        "kunci_baru": impor["keys"],
        "dilewati": impor["skipped_existing"],
        "tidak_sah": impor["invalid"],
        "hidup": hidup,
        "mati": mati,
        "dihapus": len(terhapus),
        "kunci_dihapus": terhapus,
        "hasil_hidup": hasil_hidup,
        "backup": str(backup_path),
        "log": str(log_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Siklus otomatis OSINT pasif mode solo (crt.sh -> antrean -> backup)."
    )
    parser.add_argument("--seeds", default="data/seeds.txt", help="Berkas daftar benih.")
    parser.add_argument("--submissions", default="data/submissions.json", help="Berkas antrean.")
    parser.add_argument("--log", default="data/rutinitas.log", help="Berkas log ringkas.")
    parser.add_argument("--backups", default="data/backups", help="Folder backup JSON.")
    parser.add_argument("--timeout", type=int, default=12, help="Timeout crt.sh (detik).")
    parser.add_argument("--retries", type=int, default=2, help="Percobaan ulang crt.sh.")
    parser.add_argument("--delay", type=float, default=0.4, help="Jeda antar permintaan.")
    parser.add_argument(
        "--check-alive", action="store_true",
        help="Periksa hidup/mati entri Siap Submit dengan pre-check HTTP ringan.",
    )
    parser.add_argument(
        "--prune-dead", action="store_true",
        help="Hapus entri Siap Submit yang mati (menyalakan --check-alive).",
    )
    parser.add_argument(
        "--alive-timeout", type=int, default=4,
        help="Timeout pre-check HTTP per entri (detik).",
    )
    parser.add_argument(
        "--alive-delay", type=float, default=0.2,
        help="Jeda antar pre-check HTTP (detik).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.prune_dead:
        args.check_alive = True

    jalankan_siklus(
        args.seeds,
        submissions=args.submissions,
        log=args.log,
        backups=args.backups,
        timeout=max(1, args.timeout),
        retries=max(0, args.retries),
        delay=max(0.0, args.delay),
        check_alive=args.check_alive,
        prune_dead=args.prune_dead,
        alive_timeout=max(1, args.alive_timeout),
        alive_delay=max(0.0, args.alive_delay),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
