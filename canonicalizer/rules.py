"""
canonicalizer/rules.py
Aturan resmi panitia Hackathon Berantas Web Ilegal di Indo (imphnen.dev).

Pemisahan tegas:
  submit_url   -> URL sah untuk ditempel ke form panitia (wajib http/https).
  canonical_key-> bentuk catat pembanding duplikat panitia (tanpa skema).

Mode Solo: 1 pemain, tanpa konsep tim / merge / multi-anggota.
"""

import ipaddress
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

MAX_LEN = 300
_WWW = re.compile(r"^www\.", re.IGNORECASE)
_IPV4_LIKE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?")


@dataclass(frozen=True)
class RuleResult:
    """Hasil evaluasi satu entri mentah."""
    valid: bool
    submit_url: str
    canonical_key: str
    reason: str
    raw: str


def _reject(raw: str, reason: str) -> RuleResult:
    return RuleResult(False, "", "", reason, raw)


def _is_ip(host: str) -> bool:
    """Deteksi IPv4 / IPv6 literal, termasuk bentuk bracket pada URL."""
    h = host.strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if _IPV4_LIKE.match(h):
        try:
            ipaddress.IPv4Address(h)
            return True
        except ValueError:
            return False
    try:
        ipaddress.IPv6Address(h)
        return True
    except ValueError:
        return False


def _valid_domain(host: str) -> bool:
    """Host harus memiliki titik domain dengan TLD yang wajar."""
    if not host or "." not in host:
        return False
    if host.startswith(".") or host.endswith("."):
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    if any(not _HOST_LABEL.fullmatch(label) for label in labels):
        return False
    tld = labels[-1]
    if len(tld) < 2 or not tld.isalpha():
        return False
    return True


def evaluate(raw: str) -> RuleResult:
    """
    Validasi + kanonisasi satu entri sesuai aturan panitia.

    Contoh:
        evaluate("https://www.TokoBudi.id/")
        -> RuleResult(valid=True,
                      submit_url="https://www.TokoBudi.id/",
                      canonical_key="tokobudi.id", ...)
    """
    if not isinstance(raw, str) or not raw.strip():
        return _reject("" if not isinstance(raw, str) else raw, "Input kosong")

    original = raw.strip()

    # Panjang input awal (sebelum normalisasi)
    if len(original) > MAX_LEN:
        return _reject(original, f"Panjang URL melebihi {MAX_LEN} karakter")

    candidate = original

    # Tolak skema non-http/https lebih awal (termasuk "javascript:", "ftp:", dll)
    if "://" in candidate:
        declared = candidate.split("://", 1)[0].lower()
        if declared not in ("http", "https"):
            return _reject(original, "Skema hanya boleh http atau https")
    else:
        # Tidak ada "//": domain polos atau skema aneh seperti "javascript:..."
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", candidate):
            return _reject(original, "Skema hanya boleh http atau https")
        candidate = "https://" + candidate

    parts = urlsplit(candidate)

    # Skema final
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        return _reject(original, "Skema hanya boleh http atau https")

    # Userinfo / username:password
    if "@" in parts.netloc:
        return _reject(original, "URL mengandung username/password")

    # Port (urlsplit.port bisa melempar ValueError untuk port non-numerik)
    try:
        port = parts.port
    except ValueError:
        return _reject(original, "URL mengandung port tidak valid")
    if port is not None:
        return _reject(original, f"URL mengandung port (:{port})")

    host_raw = parts.hostname
    if not host_raw:
        return _reject(original, "Host tidak ditemukan")

    host = host_raw.strip("[]").lower()
    if host == "localhost":
        return _reject(original, "Host adalah localhost")
    if _is_ip(host):
        return _reject(original, "Host adalah alamat IP")
    if not _valid_domain(host):
        return _reject(original, "Domain tidak valid (TLD tidak ditemukan)")

    # Path dipertahankan case-sensitive; query & fragment dibuang
    path = parts.path
    if path == "/":
        path = ""
    elif path.endswith("/") and len(path) > 1:
        path = path[:-1]

    canonical_host = _WWW.sub("", host)
    canonical_key = canonical_host + path

    # submit_url memakai host asli (www dipertahankan), tanpa query/fragment,
    # tanpa port/userinfo, dengan skema sah.
    submit_host = host if host else host_raw
    submit_url = f"{scheme}://{submit_host}{path}"

    if len(submit_url) > MAX_LEN:
        return _reject(original, f"Panjang submit_url melebihi {MAX_LEN} karakter")
    if len(canonical_key) > MAX_LEN:
        return _reject(original, f"Panjang canonical_key melebihi {MAX_LEN} karakter")

    return RuleResult(True, submit_url, canonical_key, "", original)


# --- Mesin status solo -----------------------------------------------------

STATUSES = (
    "Siap Submit",
    "Menunggu Penilaian",
    "Valid",
    "Duplikat",
    "Ditolak",
)

# Kunci terkunci permanen pada status berikut.
LOCKED_PERMANENT = frozenset({"Menunggu Penilaian", "Valid", "Duplikat"})
# Status yang melepas kunci dan boleh re-queue.
RELEASED = frozenset({"Ditolak"})


def is_locked(status: str) -> bool:
    """Apakah canonical_key masih dikunci untuk status ini."""
    return status in LOCKED_PERMANENT


def can_transition(current: str, target: str) -> bool:
    """
    Transisi status yang diizinkan.

    - Siap Submit -> Menunggu Penilaian / Ditolak
    - Menunggu Penilaian -> Valid / Ditolak
    - Valid -> (final, terkunci permanen)
    - Duplikat -> (final, mati permanen, TANPA re-queue)
    - Ditolak -> Siap Submit (re-queue)
    """
    if current not in STATUSES or target not in STATUSES:
        return False
    if current == "Valid" or current == "Duplikat":
        return False
    allowed = {
        "Siap Submit": {"Menunggu Penilaian", "Ditolak", "Duplikat"},
        "Menunggu Penilaian": {"Valid", "Ditolak", "Duplikat"},
        "Ditolak": {"Siap Submit"},
    }
    return target in allowed.get(current, set())


def score_for(status: str) -> int:
    """Poin sesuai aturan panitia."""
    return 1 if status == "Valid" else 0
