"""
osint/precheck.py
Pengecekan hidup pasif ringan untuk workbench dan rutinitas OSINT.

Aturan:
- Satu HTTP GET, timeout 4 detik, User-Agent standar, tanpa follow redirect.
- Potongan respons dibatasi 32 KB. Tidak ada port scan, tidak ada submit.
- Titik-titik parkir memakai pola teks, bukan daftar hitam domain.
"""

import html
import re
from typing import Optional
from urllib.parse import urljoin, urlsplit

LABEL_HIDUP = "Hidup (Siap Tembak)"
LABEL_PARKIR = "Terindikasi Parkir / Kosong"
LABEL_MATI = "Mati / NXDOMAIN"

BATAS_BACA = 32 * 1024

POLA_PARKIR = (
    r"sedo\s*parking",
    r"bodis",
    r"dan\.com",
    r"afternic",
    r"huge\s*domains?",
    r"godaddy.{0,20}park",
    r"domain\s*(?:is\s*)?for\s*sale",
    r"buy\s*this\s*domain",
    r"parked\s*domain",
    r"suspended\s*(?:page|account|domain)",
    r"domain\s*parking",
)

_TAG = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SPOL = re.compile(r"\s+")


def baca_ringkas(respons, batas: int = BATAS_BACA) -> str:
    """Baca maksimal `batas` byte dari stream respons, lalu tutup."""
    potongan = []
    sisa = batas
    raw = getattr(respons, "raw", None)
    if raw is not None and hasattr(raw, "read"):
        try:
            while sisa > 0:
                bagian = raw.read(min(8192, sisa))
                if not bagian:
                    break
                if isinstance(bagian, bytes):
                    bagian = bagian.decode("utf-8", errors="replace")
                potongan.append(bagian)
                sisa -= len(bagian)
        finally:
            tutup = getattr(raw, "close", None)
            if tutup:
                tutup()
        return "".join(potongan)

    teks = getattr(respons, "text", "")
    if isinstance(teks, bytes):
        teks = teks.decode("utf-8", errors="replace")
    return str(teks or "")[:batas]


def ambil_title(teks: str) -> str:
    """Ambil potongan <title> singkat, tanpa tag dan tanpa spasi berlebih."""
    if not teks:
        return ""
    cocok = _TITLE.search(teks)
    if not cocok:
        return ""
    isi = html.unescape(_TAG.sub(" ", cocok.group(1)))
    return _SPOL.sub(" ", isi).strip()[:120]


def teks_parkir(teks: str) -> bool:
    """Deteksi pola halaman parkir pada title atau potongan body."""
    if not teks:
        return False
    rendah = teks.lower()
    return any(re.search(pola, rendah) for pola in POLA_PARKIR)


def redirect_target(respons, url: str) -> str:
    """Kembalikan URL redirect absolut bila Location terbaca."""
    headers = getattr(respons, "headers", None)
    lokasi = ""
    if headers is not None:
        if hasattr(headers, "get"):
            lokasi = headers.get("Location", "") or headers.get("location", "")
        elif isinstance(headers, dict):
            lokasi = headers.get("Location", "") or headers.get("location", "")
    if not isinstance(lokasi, str) or not lokasi.strip():
        return ""
    return urljoin(url, lokasi.strip())


def host_pokok(url: str) -> str:
    """Host tanpa www, port, dan trailing dot."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def klasifikasi_precheck(
    url: str,
    respons,
    *,
    canonical_key: str = "",
    batas: int = BATAS_BACA,
    evaluator=None,
) -> dict:
    """
    Ubah satu respons menjadi klasifikasi pasif.

    `evaluator` opsional (default `canonicalizer.rules.evaluate`) dipakai untuk
    menilai canonical_key redirect target.
    """
    if evaluator is None:
        from canonicalizer.rules import evaluate as evaluator

    kode = getattr(respons, "status_code", None)
    isi = baca_ringkas(respons, batas)
    title = ambil_title(isi)
    parkir = teks_parkir(isi)
    tujuan = ""
    kunci_tujuan = ""

    if isinstance(kode, int) and 300 <= kode < 400:
        calon = redirect_target(respons, url)
        if calon and host_pokok(calon) != host_pokok(url):
            tujuan = calon
            sah = evaluator(calon)
            if sah.valid:
                kunci_tujuan = sah.canonical_key

    if parkir:
        label = LABEL_PARKIR
    else:
        label = LABEL_HIDUP

    hasil = {
        "url": url,
        "canonical_key": canonical_key,
        "alive": True,
        "parking": parkir,
        "label": label,
        "http_code": kode,
        "title": title,
        "redirect_target": tujuan,
        "redirect_key": kunci_tujuan,
        "detail": "",
    }
    return hasil


def klasifikasi_gagal(url: str, exc: BaseException, canonical_key: str = "") -> dict:
    """Klasifikasi kegagalan DNS / koneksi / timeout."""
    return {
        "url": url,
        "canonical_key": canonical_key,
        "alive": False,
        "parking": False,
        "label": LABEL_MATI,
        "http_code": None,
        "title": "",
        "redirect_target": "",
        "redirect_key": "",
        "detail": type(exc).__name__,
    }
