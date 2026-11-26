"""
osint/urlscan.py
Penemuan pasif kandidat lewat API publik urlscan.io.

Prinsip:
- Hanya membaca hasil pencarian sertifikat/scan publik. Tidak ada akses ke
  situs target, tidak ada port scan, tidak ada auto-submit ke panitia.
- URL lengkap beserta path aktif ikut disaring canonicalizer, karena panitia
  menghitung path berbeda sebagai entri terpisah.
- Fetcher bisa disuntik supaya unit test tidak menyentuh jaringan.
"""

import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlencode

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonicalizer.rules import evaluate
from osint.crtsh import adalah_placeholder

URLSCAN_ENDPOINT = "https://urlscan.io/api/v1/search/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SoloResearch/1.0; "
    "+https://urlscan.io/ passive search)"
)
UKURAN = 50


class UrlscanError(Exception):
    """Kesalahan jaringan atau format data dari urlscan.io."""


def siapkan_query_urlscan(
    seed: str, *, tolak_placeholder: bool = False
) -> Tuple[str, str, str]:
    """
    Tentukan mode dan query urlscan.io.

    Mengembalikan `(mode, query, pesan_error)`.
    - domain: `domain:nama.domain`
    - kata kunci: `page.url:keyword` (minimal 4 karakter)
    """
    value = (seed or "").strip().lower().rstrip(".")
    if not value:
        return "", "", "Benih kosong atau tidak sah"
    if tolak_placeholder and adalah_placeholder(value):
        return "", "", "Benih contoh tidak diproses; gunakan domain atau kata kunci asli"

    sah = evaluate(value)
    if sah.valid:
        host = sah.canonical_key.split("/", 1)[0]
        return "domain", "domain:" + host, ""

    if len(value) < 4:
        return "", "", "Kata kunci minimal 4 karakter"

    aman = value.replace('"', "")
    if any(ch.isspace() for ch in aman):
        aman = '"' + aman + '"'
    return "kata_kunci", "page.url:" + aman, ""


def bangun_url(query: str, size: int = UKURAN) -> str:
    """Rangkai URL pencarian dengan batas hasil yang sopan."""
    ukuran = max(1, min(100, int(size or UKURAN)))
    return URLSCAN_ENDPOINT + "?" + urlencode({"q": query, "size": ukuran})


def default_fetcher(url: str, timeout: int):
    """Pengambil bawaan, dipisahkan agar mudah diganti di unit test."""
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    if response.status_code == 429:
        raise UrlscanError(
            "urlscan.io membatasi permintaan (HTTP 429); tunggu sebentar"
        )
    if response.status_code != 200:
        raise UrlscanError(f"urlscan.io mengembalikan HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise UrlscanError("urlscan.io mengembalikan data yang bukan JSON") from exc


def parse_payload(payload) -> List[str]:
    """
    Ubah respons urlscan.io menjadi canonical_key unik.

    `page.url` diutamakan karena membawa path aktif. Fallback ke `task.url`,
    `page.domain`, dan `page.hostname` bila URL lengkap tidak tersedia.
    """
    if isinstance(payload, dict):
        rows = payload.get("results", [])
        if not isinstance(rows, list):
            rows = []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    ditemukan = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        page = row.get("page") if isinstance(row.get("page"), dict) else {}
        task = row.get("task") if isinstance(row.get("task"), dict) else {}
        kandidat = (
            page.get("url"),
            task.get("url"),
            page.get("domain"),
            page.get("hostname"),
        )
        for nilai in kandidat:
            if not isinstance(nilai, str) or not nilai.strip():
                continue
            bahan = nilai.strip()
            if "://" not in bahan:
                bahan = "https://" + bahan
            hasil = evaluate(bahan)
            if hasil.valid:
                ditemukan.add(hasil.canonical_key)
    return sorted(ditemukan)


def fetch_urls(
    seed: str,
    *,
    timeout: int = 6,
    retries: int = 1,
    delay: float = 0.4,
    size: int = UKURAN,
    fetcher: Optional[Callable[[str, int], object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    tolak_placeholder: bool = False,
) -> Dict[str, object]:
    """Cari satu benih dan kembalikan dictionary, bukan melempar error."""
    mode, query, pesan_error = siapkan_query_urlscan(
        seed, tolak_placeholder=tolak_placeholder
    )
    hasil: Dict[str, object] = {
        "seed": (seed or "").strip(),
        "mode": mode,
        "query": query,
        "ok": False,
        "candidates": [],
        "error": "",
    }
    if not query:
        hasil["error"] = pesan_error or "Benih kosong atau tidak sah"
        return hasil

    ambil = fetcher or default_fetcher
    jeda = sleeper or time.sleep
    url = bangun_url(query, size)

    galat_terakhir = "Permintaan gagal"
    for attempt in range(max(0, retries) + 1):
        if attempt > 0:
            jeda(delay * attempt)
        try:
            payload = ambil(url, timeout)
            hasil["ok"] = True
            hasil["candidates"] = parse_payload(payload)
            hasil["error"] = ""
            return hasil
        except UrlscanError as exc:
            galat_terakhir = str(exc)
        except requests.exceptions.Timeout:
            galat_terakhir = "Koneksi ke urlscan.io timed out"
        except requests.exceptions.ConnectionError:
            galat_terakhir = "Koneksi ke urlscan.io gagal"
        except requests.exceptions.RequestException:
            galat_terakhir = "Permintaan ke urlscan.io gagal"
        except (TypeError, ValueError) as exc:
            galat_terakhir = f"Format data urlscan.io tidak dikenali: {exc}"

    hasil["error"] = f"{galat_terakhir} setelah {max(0, retries) + 1} percobaan"
    return hasil


def collect_urls(
    seeds: Iterable[str],
    *,
    timeout: int = 6,
    retries: int = 1,
    delay: float = 0.4,
    size: int = UKURAN,
    fetcher: Optional[Callable[[str, int], object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    tolak_placeholder: bool = False,
) -> Dict[str, object]:
    """Jalankan pencarian untuk banyak benih dengan jeda sopan."""
    istirahat = sleeper or time.sleep
    daftar = [str(s).strip() for s in seeds if str(s).strip()]
    hasil_per_benih: List[Dict[str, object]] = []
    kandidat = set()

    for index, seed in enumerate(daftar):
        if index > 0 and delay > 0:
            istirahat(delay)
        hasil = fetch_urls(
            seed,
            timeout=timeout,
            retries=retries,
            delay=delay,
            size=size,
            fetcher=fetcher,
            sleeper=istirahat,
            tolak_placeholder=tolak_placeholder,
        )
        hasil_per_benih.append({
            "seed": hasil["seed"],
            "mode": hasil["mode"],
            "query": hasil["query"],
            "ok": hasil["ok"],
            "count": len(hasil["candidates"]),
            "error": hasil["error"],
        })
        kandidat.update(hasil["candidates"])

    return {
        "ok": all(r["ok"] for r in hasil_per_benih) if hasil_per_benih else True,
        "seeds_count": len(daftar),
        "results": hasil_per_benih,
        "candidates": sorted(kandidat),
        "total": len(kandidat),
        "error_count": sum(1 for r in hasil_per_benih if not r["ok"]),
    }
