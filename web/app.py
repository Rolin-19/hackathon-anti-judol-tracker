"""
web/app.py
Backend solo-mode: canonicalizer + mesin status + pre-check HTTP + OSINT crt.sh.
Server lokal: http://localhost:8765
Penyimpanan: data/submissions.json
"""

import csv
import io
import json
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from http.server import HTTPServer, ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import pytz
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from canonicalizer.rules import (
    evaluate,
    is_locked,
    can_transition,
    score_for,
    STATUSES,
    MAX_LEN,
)
from osint.crtsh import (
    collect_subdomains,
    fetch_subdomains as ambil_subdomain,
    siapkan_query,
)
from osint.precheck import klasifikasi_gagal, klasifikasi_precheck
from osint.urlscan import (
    collect_urls as kumpulkan_urlscan,
    siapkan_query_urlscan,
)

DATA_FILE = ROOT / "data" / "submissions.json"
BACKUP_DIR = ROOT / "data" / "backups"
RUTINITAS_LOG = ROOT / "data" / "rutinitas.log"
STATIC_DIR = Path(__file__).resolve().parent
WIB = pytz.timezone("Asia/Jakarta")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Cache singkat supaya pengetikan ulang tidak memukul crt.sh berkali-kali.
CACHE_TTL = 300           # hasil sukses: 5 menit
CACHE_TTL_GAGAL = 30      # hasil gagal: 30 detik
CACHE_MAKS = 50
_CACHE_CRTSH: "OrderedDict[str, tuple]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cache_baca(kunci: str):
    with _CACHE_LOCK:
        item = _CACHE_CRTSH.get(kunci)
        if item is None:
            return None
        if item[0] < time.monotonic():
            _CACHE_CRTSH.pop(kunci, None)
            return None
        _CACHE_CRTSH.move_to_end(kunci)
        return item[1]


def _cache_tulis(kunci: str, nilai: dict, ttl: int) -> None:
    with _CACHE_LOCK:
        _CACHE_CRTSH[kunci] = (time.monotonic() + ttl, nilai)
        _CACHE_CRTSH.move_to_end(kunci)
        while len(_CACHE_CRTSH) > CACHE_MAKS:
            _CACHE_CRTSH.popitem(last=False)


def now_wib(timespec: str = "milliseconds") -> str:
    """Waktu WIB dengan presisi default sampai milidetik."""
    return datetime.now(WIB).isoformat(timespec=timespec)


def peringkat_antrean(entry: dict) -> int:
    """Hidup > belum dicek > parkir/mati."""
    probe = entry.get("probe") or {}
    if probe.get("parking") or probe.get("alive") is False:
        return 2
    if probe.get("alive") is True:
        return 0
    return 1


class SoloTracker:
    """Penyimpanan lokal + mesin status solo (1 pemain)."""

    def __init__(self, path: Path = DATA_FILE):
        self.path = path
        self.entries: Dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            raw_entries = data.get("entries", {})
            if isinstance(raw_entries, dict):
                self.entries = raw_entries
        except (OSError, ValueError, TypeError):
            self.entries = {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "mode": "solo",
            "entries": self.entries,
            "last_updated": now_wib(),
        }
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    # -- helper -------------------------------------------------------------
    @staticmethod
    def _new_entry(raw: str) -> dict:
        r = evaluate(raw)
        return {
            "submit_url": r.submit_url,
            "canonical_key": r.canonical_key,
            "raw": r.raw,
            "status": "Siap Submit",
            "score": 0,
            "reason": r.reason,
            "reject_reason": "",
            "created_at": now_wib(),
            "submitted_at": "",
        }

    def _is_cancelled(self, key: str) -> bool:
        """Ditolak = kunci dilepas, boleh masuk lagi."""
        return self.entries.get(key, {}).get("status") == "Ditolak"

    def process_bulk(self, urls: List[str]) -> dict:
        """Pilah input massal menjadi unik / duplikat / tidak sah."""
        uniques, duplicates, invalids = [], [], []
        seen_batch = set()

        for raw in urls:
            if not isinstance(raw, str) or not raw.strip():
                continue
            r = evaluate(raw)
            if not r.valid:
                invalids.append({"raw": raw, "reason": r.reason})
                continue

            key = r.canonical_key
            if key in self.entries and not self._is_cancelled(key):
                duplicates.append({
                    "raw": raw,
                    "canonical_key": key,
                    "reason": "Sudah ada di antrean / terkunci",
                    "status": self.entries[key].get("status", ""),
                })
                continue
            if key in seen_batch:
                duplicates.append({
                    "raw": raw,
                    "canonical_key": key,
                    "reason": "Duplikat dalam batch yang sama",
                    "status": "Siap Submit",
                })
                continue

            seen_batch.add(key)
            uniques.append(self._new_entry(raw))

        # Masukkan yang unik ke antrean. Jika sebelumnya Ditolak, ganti entri
        # (kunci dilepas -> re-queue lewat Bulk Cleaner juga didukung).
        for entry in uniques:
            self.entries[entry["canonical_key"]] = entry
        if uniques:
            self.save()

        return {
            "total": len(urls),
            "uniques": uniques,
            "duplicates": duplicates,
            "invalids": invalids,
            "unique_count": len(uniques),
            "duplicate_count": len(duplicates),
            "invalid_count": len(invalids),
        }

    def submit(self, key: str) -> Tuple[bool, Optional[dict]]:
        """Salin & tandai submitted -> Menunggu Penilaian, kunci canonical."""
        entry = self.entries.get(key)
        if not entry or entry.get("status") != "Siap Submit":
            return False, None
        if not can_transition("Siap Submit", "Menunggu Penilaian"):
            return False, None

        entry["status"] = "Menunggu Penilaian"
        entry["submitted_at"] = now_wib()
        entry["score"] = 0
        self.save()
        return True, entry

    def entries_urut(self) -> List[dict]:
        """Antrean Siap Submit diurutkan berdasarkan hasil pre-check."""
        return sorted(
            self.entries.values(),
            key=lambda e: (
                peringkat_antrean(e),
                str(e.get("created_at", "")),
                e.get("canonical_key", ""),
            ),
            reverse=False,
        )

    def siap_urut(self) -> List[dict]:
        return [
            e for e in self.entries_urut()
            if e.get("status") == "Siap Submit"
        ]

    def catat_probe(self, hasil: List[dict]) -> int:
        """Simpan hasil pre-check ringan ke entri yang bersesuaian."""
        tersimpan = 0
        for item in hasil or []:
            key = item.get("canonical_key", "")
            if not key and item.get("url"):
                r = evaluate(item["url"])
                key = r.canonical_key if r.valid else ""
            entri = self.entries.get(key)
            if not entri:
                continue
            entri["probe"] = {
                "alive": bool(item.get("alive")),
                "parking": bool(item.get("parking")),
                "label": item.get("label", ""),
                "http_code": item.get("http_code"),
                "title": item.get("title", "")[:120],
                "redirect_target": item.get("redirect_target", ""),
                "redirect_key": item.get("redirect_key", ""),
                "checked_at": now_wib(),
            }
            tersimpan += 1
        if tersimpan:
            self.save()
        return tersimpan

    def set_status(self, key: str, target: str, reason: str = "") -> Tuple[bool, str]:
        """Transisi status dengan aturan kunci panitia."""
        entry = self.entries.get(key)
        if not entry:
            return False, "Entri tidak ditemukan"
        if target not in STATUSES:
            return False, "Status tidak dikenal"

        current = entry.get("status", "Siap Submit")

        # Duplikat & Valid final: tidak bisa diubah apa pun (termasuk re-queue).
        if current in ("Duplikat", "Valid"):
            return False, f"Status {current} terkunci permanen"

        # Mengubah catatan alasan pada entri yang memang sudah Ditolak.
        if current == "Ditolak" and target == "Ditolak":
            entry["reject_reason"] = reason[:500]
            self.save()
            return True, ""

        # Re-queue hanya dari Ditolak -> Siap Submit.
        if current == "Ditolak" and target == "Siap Submit":
            entry["status"] = "Siap Submit"
            entry["score"] = 0
            entry["reject_reason"] = ""
            entry["submitted_at"] = ""
            self.save()
            return True, ""

        if not can_transition(current, target):
            return False, f"Transisi {current} -> {target} tidak diizinkan"

        entry["status"] = target
        entry["score"] = score_for(target)

        if target == "Ditolak":
            # Melepas kunci: canonical_key bisa kembali ke antrean.
            entry["reject_reason"] = reason[:500]
            entry["submitted_at"] = ""
        elif target == "Valid":
            entry["reject_reason"] = ""
        elif target == "Menunggu Penilaian":
            entry["submitted_at"] = now_wib()

        self.save()
        return True, ""

    def summary(self) -> dict:
        counts = {s: 0 for s in STATUSES}
        score = 0
        for e in self.entries.values():
            st = e.get("status", "Siap Submit")
            if st in counts:
                counts[st] += 1
            score += int(e.get("score", 0) or 0)
        result = {
            "total_entries": len(self.entries),
            "total_score": score,
        }
        result.update(counts)
        return result

    def export_json(self) -> str:
        return json.dumps(
            {
                "mode": "solo",
                "entries": self.entries,
                "summary": self.summary(),
                "exported_at": now_wib(),
            },
            indent=2,
            ensure_ascii=False,
        )

    def import_json(self, raw: str) -> int:
        data = json.loads(raw)
        incoming = data.get("entries", {})
        if not isinstance(incoming, dict):
            raise ValueError("Format entries tidak valid")

        count = 0
        for key, item in incoming.items():
            if not isinstance(item, dict):
                continue
            # Validasi ulang kunci agar tidak ada data liar.
            r = evaluate(item.get("submit_url") or key)
            if not r.valid or r.canonical_key != key:
                continue
            if key in self.entries:
                # Restore pribadi: timpa entri lokal dengan data cadangan.
                self.entries[key] = item
            else:
                self.entries[key] = item
            count += 1
        self.save()
        return count

    def export_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "canonical_key", "submit_url", "raw", "status", "score",
            "reason", "reject_reason", "created_at", "submitted_at",
        ])
        for e in self.entries.values():
            writer.writerow([
                e.get("canonical_key", ""),
                e.get("submit_url", ""),
                e.get("raw", ""),
                e.get("status", ""),
                e.get("score", 0),
                e.get("reason", ""),
                e.get("reject_reason", ""),
                e.get("created_at", ""),
                e.get("submitted_at", ""),
            ])
        return buf.getvalue()


# -- Pre-check HTTP ---------------------------------------------------------

def check_alive(urls: List[str]) -> List[dict]:
    """
    Pre-check pasif, satu GET ringan per URL.

    Klasifikasi:
      Hidup (Siap Tembak)          -> merespons, bukan halaman parkir.
      Terindikasi Parkir / Kosong  -> pola parkir pada title/body.
      Mati / NXDOMAIN              -> DNS gagal, connection refused, timeout.
    """
    results: List[dict] = []
    seen = set()
    for item in urls or []:
        if isinstance(item, dict):
            item = item.get("submit_url") or item.get("url") or ""
        if not isinstance(item, str) or not item.strip():
            continue
        r = evaluate(item.strip())
        target = r.submit_url if r.valid else item.strip()
        if target in seen:
            continue
        seen.add(target)

        try:
            resp = requests.get(
                target,
                timeout=4,
                headers={"User-Agent": USER_AGENT},
                allow_redirects=False,
                stream=True,
            )
            hasil = klasifikasi_precheck(
                target, resp, canonical_key=r.canonical_key if r.valid else ""
            )
            tutup = getattr(resp, "close", None)
            if tutup:
                tutup()
            results.append(hasil)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as exc:
            results.append(klasifikasi_gagal(
                target, exc, r.canonical_key if r.valid else ""
            ))
    return results


# -- Penemuan pasif (crt.sh + urlscan.io) -----------------------------------

def _gabungkan_kandidat(kumpulan) -> Dict[str, str]:
    """Satukan kandidat lintas sumber memakai canonical_key."""
    gabung: Dict[str, str] = {}
    for kandidat in kumpulan:
        if not isinstance(kandidat, str) or not kandidat.strip():
            continue
        r = evaluate(kandidat.strip())
        if r.valid and r.canonical_key not in gabung:
            gabung[r.canonical_key] = r.submit_url
    return gabung


def kumpulkan_riset(query: str, *, sources=None) -> dict:
    """
    Cari satu input live di crt.sh dan urlscan.io, lalu satukan kandidat unik.

    Path aktif dari urlscan.io ikut dipertahankan canonicalizer. Cache singkat
    menyimpan jawaban mentah; status Baru/Sudah Ada tetap dihitung ulang.
    """
    teks = (query or "").strip()
    daftar_sumber = tuple(sources) if sources else ("crtsh", "urlscan")
    mode, query_crtsh, err_crtsh = siapkan_query(teks, tolak_placeholder=True)
    mode_urlscan, query_urlscan, err_urlscan = siapkan_query_urlscan(
        teks, tolak_placeholder=True
    )
    dasar = {
        "ok": False,
        "error": err_crtsh or err_urlscan,
        "domain": teks,
        "query": teks,
        "mode": mode,
        "query_crtsh": query_crtsh,
        "query_urlscan": query_urlscan,
        "sumber": list(daftar_sumber),
        "detail_sumber": [],
        "subdomains": [],
        "baru": [],
        "sudah_ada": [],
        "detail": [],
        "dari_cache": False,
        "cache_ttl_detik": CACHE_TTL,
    }
    if not query_crtsh and not query_urlscan:
        return dasar

    kunci = teks.lower().rstrip(".") + "|" + ",".join(sorted(daftar_sumber))
    temuan = _cache_baca(kunci)
    dari_cache = temuan is not None
    if dari_cache:
        hasil_mentah = temuan
    else:
        gabung: Dict[str, str] = {}
        detail_sumber: List[dict] = []
        gagal: List[str] = []
        berhasil = 0

        if "crtsh" in daftar_sumber and query_crtsh:
            h = ambil_subdomain(
                teks, timeout=8, retries=1, delay=0.2, tolak_placeholder=True
            )
            gabung.update(_gabungkan_kandidat(h["subdomains"]))
            detail_sumber.append({
                "sumber": "crt.sh",
                "ok": h["ok"],
                "total": len(h["subdomains"]),
                "error": h["error"],
            })
            if h["ok"]:
                berhasil += 1
            else:
                gagal.append(f"crt.sh: {h['error']}")

        if "urlscan" in daftar_sumber and query_urlscan:
            h = kumpulkan_urlscan(
                [teks],
                timeout=6,
                retries=1,
                delay=0.2,
                size=50,
                tolak_placeholder=True,
            )
            gabung.update(_gabungkan_kandidat(h["candidates"]))
            pesan_urlscan = "; ".join(
                r["error"] for r in h["results"] if r.get("error")
            )
            detail_sumber.append({
                "sumber": "urlscan.io",
                "ok": h["ok"],
                "total": h["total"],
                "error": pesan_urlscan,
            })
            if h["ok"]:
                berhasil += 1
            else:
                gagal.append("urlscan.io: " + pesan_urlscan)

        hasil_mentah = {
            "ok": berhasil > 0,
            "error": " | ".join(gagal),
            "subdomains": sorted(gabung),
            "detail_sumber": detail_sumber,
        }
        ttl = CACHE_TTL if hasil_mentah["ok"] else CACHE_TTL_GAGAL
        _cache_tulis(kunci, hasil_mentah, ttl)

    tracker = SoloTracker(DATA_FILE)
    detail: List[dict] = []
    baru: List[str] = []
    sudah: List[str] = []
    for key in hasil_mentah["subdomains"]:
        r = evaluate(key)
        ada = key in tracker.entries
        detail.append({
            "canonical_key": key,
            "submit_url": r.submit_url if r.valid else "https://" + key,
            "status": "Sudah Ada di Tracker" if ada else "Baru (Siap Masuk Antrean)",
        })
        (sudah if ada else baru).append(key)

    ttl = CACHE_TTL if hasil_mentah["ok"] else CACHE_TTL_GAGAL
    return {
        "ok": hasil_mentah["ok"],
        "error": hasil_mentah["error"],
        "domain": teks,
        "query": teks,
        "mode": mode or mode_urlscan,
        "query_crtsh": query_crtsh,
        "query_urlscan": query_urlscan,
        "sumber": list(daftar_sumber),
        "detail_sumber": hasil_mentah.get("detail_sumber", []),
        "subdomains": hasil_mentah["subdomains"],
        "baru": baru,
        "sudah_ada": sudah,
        "detail": detail,
        "dari_cache": dari_cache,
        "cache_ttl_detik": ttl,
    }


def fetch_subdomains(query: str) -> dict:
    """Kompatibel dengan pemanggil lama: riset dua sumber."""
    return kumpulkan_riset(query)


def kumpulkan_riset_batch(seeds, *, sources=None) -> dict:
    """Pemindaian banyak benih dengan kandidat unik lintas sumber."""
    daftar_sumber = tuple(sources) if sources else ("crtsh", "urlscan")
    gabung: Dict[str, str] = {}
    hasil: List[dict] = []
    gagal = 0

    if "crtsh" in daftar_sumber:
        h = collect_subdomains(
            seeds, timeout=12, retries=2, delay=0.4, tolak_placeholder=True
        )
        gabung.update(_gabungkan_kandidat(h["candidates"]))
        hasil.extend({**r, "sumber": "crt.sh"} for r in h["results"])
        gagal += h["error_count"]

    if "urlscan" in daftar_sumber:
        h = kumpulkan_urlscan(
            seeds, timeout=6, retries=1, delay=0.4, size=50, tolak_placeholder=True
        )
        gabung.update(_gabungkan_kandidat(h["candidates"]))
        hasil.extend({**r, "sumber": "urlscan.io"} for r in h["results"])
        gagal += h["error_count"]

    return {
        "ok": True,
        "seeds_count": len([s for s in seeds if str(s).strip()]),
        "results": hasil,
        "candidates": sorted(gabung),
        "total": len(gabung),
        "error_count": gagal,
        "sumber": list(daftar_sumber),
    }


def batas(angka, nilai_default: int, minimum: int, maksimum: int) -> int:
    """Ambil bilangan dari payload dengan batas aman."""
    try:
        return max(minimum, min(maksimum, int(angka)))
    except (TypeError, ValueError):
        return nilai_default


def info_rutinitas() -> dict:
    """Ringkasan backup terakhir dan baris log terakhir untuk workbench."""
    backup_terakhir = None
    if BACKUP_DIR.exists():
        berkas = sorted(
            BACKUP_DIR.glob("submissions-*.json"),
            key=lambda p: p.name,
            reverse=True,
        )
        if berkas:
            backup_terakhir = berkas[0]

    log_terakhir = ""
    if RUTINITAS_LOG.exists():
        try:
            baris = [
                b.strip() for b in RUTINITAS_LOG.read_text(encoding="utf-8").splitlines()
                if b.strip()
            ]
            log_terakhir = baris[-1] if baris else ""
        except OSError:
            log_terakhir = ""

    return {
        "ok": True,
        "backup_terakhir": backup_terakhir.name if backup_terakhir else None,
        "backup_path": str(backup_terakhir) if backup_terakhir else None,
        "log_terakhir": log_terakhir,
    }


# -- Sinkronisasi massal hasil panitia --------------------------------------

_URL_PANITIA = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
_DOMAIN_PANITIA = re.compile(
    r'(?<![\w@.-])(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s,;)\]]*)?',
    re.IGNORECASE,
)
_STATUS_PANITIA = (
    (re.compile(r'\b(ditolak|rejected|reject|ditolak\s*admin|tidak\s*valid|non.?valid|tolak)\b', re.I), "Ditolak"),
    (re.compile(r'\b(duplikat|duplicate|already\s*submitted)\b', re.I), "Duplikat"),
    (re.compile(r'\b(valid|diterima|accepted|lolos|disetujui|\+1)\b', re.I), "Valid"),
)


def bersih_token(token: str) -> str:
    """Bersihkan tanda baca ekor dari hasil ekstraksi baris dashboard."""
    return token.strip().rstrip(".,;:!?)]}>\"'")


def ekstrak_url_panitia(baris: str) -> List[str]:
    """Ambil URL lengkap dan domain polos dari satu baris riwayat panitia."""
    hasil: List[str] = []
    terlihat = set()
    for pola in (_URL_PANITIA, _DOMAIN_PANITIA):
        for token in pola.findall(baris or ""):
            value = bersih_token(token)
            if not value or value in terlihat:
                continue
            terlihat.add(value)
            hasil.append(value)
    return hasil


def deteksi_status_panitia(baris: str) -> str:
    """Petakan kata kunci status pada baris riwayat ke status tracker."""
    for pola, status in _STATUS_PANITIA:
        if pola.search(baris or ""):
            return status
    return ""


def alasan_panitia(baris: str) -> str:
    """Ambil alasan singkat; jangan ikut menyimpan URL mentah bila bisa."""
    cocok = re.search(
        r'(?:alasan|reason|catatan|keterangan)\s*[:=]\s*(.+)',
        baris or "",
        re.IGNORECASE,
    )
    teks = cocok.group(1) if cocok else baris
    teks = _URL_PANITIA.sub(" ", teks)
    teks = _DOMAIN_PANITIA.sub(" ", teks)
    return re.sub(r"\s+", " ", teks).strip(" -|·")[:500]


def sinkron_panitia(tracker: SoloTracker, teks: str) -> dict:
    """
    Cocokkan baris riwayat panitia ke canonical_key lokal, lalu update massal.

    Hanya transisi yang diizinkan mesin status yang dijalankan. Entri Valid dan
    Duplikat tetap terkunci permanen.
    """
    baris_list = [b.strip() for b in str(teks or "").splitlines() if b.strip()]
    rincian: List[dict] = []
    dikenali = 0
    cocok = 0
    terupdate = 0
    dilewati = 0
    tidak_cocok = 0
    gagal = 0

    for baris in baris_list:
        status = deteksi_status_panitia(baris)
        if not status:
            continue
        dikenali += 1
        alasan = alasan_panitia(baris)
        kunci_baris = set()

        for mentah in ekstrak_url_panitia(baris):
            r = evaluate(mentah)
            if not r.valid:
                continue
            key = r.canonical_key
            if key in kunci_baris:
                continue
            kunci_baris.add(key)

            entri = tracker.entries.get(key)
            if not entri:
                tidak_cocok += 1
                rincian.append({
                    "canonical_key": key, "status": status,
                    "hasil": "Tidak cocok di tracker lokal",
                })
                continue
            cocok += 1
            if entri.get("status") == status:
                dilewati += 1
                rincian.append({
                    "canonical_key": key, "status": status,
                    "hasil": "Sudah sesuai",
                })
                continue

            ok, err = tracker.set_status(key, status, alasan)
            if ok:
                terupdate += 1
                rincian.append({
                    "canonical_key": key, "status": status,
                    "hasil": "Diperbarui",
                })
            else:
                gagal += 1
                rincian.append({
                    "canonical_key": key, "status": status,
                    "hasil": err or "Transisi ditolak",
                })

    return {
        "ok": True,
        "total_baris": len(baris_list),
        "baris_dikenali": dikenali,
        "cocok": cocok,
        "terupdate": terupdate,
        "dilewati": dilewati,
        "tidak_cocok": tidak_cocok,
        "gagal": gagal,
        "rincian": rincian,
        "summary": tracker.summary(),
    }


# -- HTTP Handler -----------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    tracker = SoloTracker()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, fmt, *args):
        # Saring noise, tapi error tetap tercatat.
        msg = fmt % args
        if " 500 " in msg or " 404 " in msg:
            sys.stderr.write(msg + "\n")

    # -- response helper ----------------------------------------------------
    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _download(self, text: str, filename: str, mime: str) -> None:
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        return self.rfile.read(length) if length else b""

    def _json_body(self):
        raw = self._body().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}

    # -- GET ----------------------------------------------------------------
    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self.path = "/static/index.html"
            return super().do_GET()

        if path == "/api/stats":
            return self._json(self.tracker.summary())
        if path == "/api/entries":
            return self._json({"entries": self.tracker.entries_urut()})

        if path == "/api/export/json":
            return self._download(
                self.tracker.export_json(),
                "solo-tracker-backup.json",
                "application/json",
            )
        if path == "/api/export/csv":
            return self._download(
                self.tracker.export_csv(),
                "solo-tracker.csv",
                "text/csv",
            )

        if path == "/api/rutinitas/status":
            return self._json(info_rutinitas())

        if path == "/api/seeds/example":
            # Contoh benih disajikan lewat API karena folder data tidak ikut
            # disajikan sebagai file statis oleh server.
            contoh = ROOT / "data" / "seeds.example.txt"
            try:
                teks = contoh.read_text(encoding="utf-8")
                return self._json({"ok": True, "text": teks, "error": ""})
            except OSError as exc:
                return self._json({
                    "ok": False,
                    "text": "tokobudi.id\ncontoh.id\npromo judol",
                    "error": f"Berkas contoh tidak dapat dibaca: {exc}",
                })

        if path == "/api/subdomains":
            qs = parse_qs(parsed.query)
            domain = (qs.get("q") or qs.get("domain") or [""])[0]
            sumber = [
                s.strip() for s in (qs.get("sources") or ["crtsh,urlscan"])[0].split(",")
                if s.strip() in ("crtsh", "urlscan")
            ] or ["crtsh", "urlscan"]
            hasil = fetch_subdomains(domain) if len(sumber) == 2 else kumpulkan_riset(domain, sources=sumber)
            # Error disampaikan sebagai field, bukan HTTP 500, supaya antarmuka
            # bisa menampilkan pesan yang jelas tanpa memutus alur kerja.
            return self._json(hasil)

        return super().do_GET()

    # -- POST ---------------------------------------------------------------
    def do_POST(self):
        path = urlsplit(self.path).path

        try:
            if path == "/api/process":
                data = self._json_body()
                urls = data.get("urls", [])
                if not isinstance(urls, list):
                    return self._json({"error": "urls harus array"}, 400)
                return self._json(self.tracker.process_bulk(urls))

            if path == "/api/submit":
                data = self._json_body()
                key = str(data.get("canonical_key", ""))
                ok, entry = self.tracker.submit(key)
                if not ok:
                    return self._json({"success": False, "error": "Entri tidak siap"}, 400)
                return self._json({"success": True, "entry": entry, "summary": self.tracker.summary()})

            if path == "/api/status":
                data = self._json_body()
                key = str(data.get("canonical_key", ""))
                target = str(data.get("status", ""))
                reason = str(data.get("reason", ""))
                ok, err = self.tracker.set_status(key, target, reason)
                if not ok:
                    return self._json({"success": False, "error": err}, 400)
                return self._json({"success": True, "summary": self.tracker.summary()})

            if path == "/api/check-alive":
                data = self._json_body()
                urls = data.get("urls", [])
                if not isinstance(urls, list):
                    urls = [u for u in data.get("canonical_keys", [])]
                hasil = check_alive(urls)
                self.tracker.catat_probe(hasil)
                return self._json({"results": hasil})

            if path == "/api/sync-panitia":
                data = self._json_body()
                return self._json(sinkron_panitia(self.tracker, data.get("text", "")))

            if path == "/api/osint/scan":
                data = self._json_body()
                seeds = data.get("seeds", [])
                if not isinstance(seeds, list):
                    return self._json({"error": "seeds harus berupa daftar teks"}, 400)
                sumber = data.get("sources") or ("crtsh", "urlscan")
                if not isinstance(sumber, list):
                    return self._json({"error": "sources harus berupa daftar teks"}, 400)
                hasil = kumpulkan_riset_batch(seeds, sources=sumber)
                return self._json(hasil)

            if path == "/api/osint/import":
                data = self._json_body()
                urls = data.get("urls", [])
                if not isinstance(urls, list):
                    return self._json({"error": "urls harus berupa daftar teks"}, 400)
                baru = []
                skipped = 0
                for u in urls:
                    if not isinstance(u, str) or not u.strip():
                        skipped += 1
                        continue
                    r = evaluate(u.strip())
                    if r.valid and r.canonical_key in self.tracker.entries:
                        skipped += 1
                    else:
                        baru.append(u.strip())
                diproses = self.tracker.process_bulk(baru)
                return self._json({
                    "success": True,
                    "added": diproses["unique_count"],
                    "skipped_existing": skipped,
                    "invalid": diproses["invalid_count"],
                    "duplicates": diproses["duplicate_count"],
                    "summary": self.tracker.summary(),
                })

            if path == "/api/import/json":
                raw = self._body().decode("utf-8", errors="replace")
                count = self.tracker.import_json(raw)
                return self._json({"success": True, "imported": count, "summary": self.tracker.summary()})

            if path == "/api/reset":
                self.tracker.entries = {}
                self.tracker.save()
                return self._json({"success": True, "summary": self.tracker.summary()})

        except (ValueError, TypeError) as exc:
            return self._json({"error": f"Payload tidak valid: {exc}"}, 400)
        except Exception as exc:  # pragma: no cover - guard final
            return self._json({"error": f"Kesalahan server: {exc}"}, 500)

        return self._json({"error": "Endpoint tidak ditemukan"}, 404)


def run_server(port: int = 8765) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("localhost", port), Handler)
    print(f"Solo tracker siap: http://localhost:{port}")
    print("Tekan Ctrl+C untuk menghentikan.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer dihentikan.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
