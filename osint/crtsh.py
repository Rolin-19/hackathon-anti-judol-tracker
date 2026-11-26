"""
osint/crtsh.py
Pengambil subdomain pasif dari catatan sertifikat publik crt.sh.

Prinsip:
- Hanya membaca data sertifikat yang sudah dipublikasikan. Tidak ada port scan,
  tidak ada request ke situs target, tidak ada bypass proteksi apa pun.
- Semua kandidat disaring memakai aturan canonicalizer sebelum dipakai.
- Jaringan lambat atau gagal tidak boleh membuat proses berhenti/macet.
"""

import sys
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from canonicalizer.rules import evaluate

CRT_SH_ENDPOINT = "https://crt.sh/"
USER_AGENT = (
    "Mozilla/5.0 (compatible; SoloResearch/1.0; "
    "+https://crt.sh/ passive certificate lookup)"
)


class OsintError(Exception):
    """Kesalahan jaringan atau format data dari crt.sh."""


def build_query(seed: str) -> str:
    """
    Bentuk query crt.sh dari sebuah benih.

    Benih berupa domain sah dipakai sebagai pencarian subdomain: `%.domain`.
    Benih berupa kata kunci dipakai sebagai pencarian teks: `%kata%`.
    """
    value = (seed or "").strip().lower().rstrip(".")
    if not value:
        return ""

    checked = evaluate(value)
    if checked.valid:
        return "%." + checked.canonical_key
    return "%" + value[:120] + "%"


def parse_crtsh_payload(payload) -> List[str]:
    """
    Ubah respons JSON crt.sh menjadi daftar canonical_key yang sah.

    Menerima list baris, atau objek dengan kunci `results`. Baris boleh dict
    (memakai `name_value` / `name`) maupun string biasa.
    """
    if isinstance(payload, dict):
        rows = payload.get("results", [])
        if not isinstance(rows, list):
            if "name_value" in payload or "name" in payload:
                rows = [payload]
            else:
                return []
    elif isinstance(payload, list):
        rows = payload
    else:
        return []

    found = set()
    for row in rows:
        if isinstance(row, dict):
            name = row.get("name_value") or row.get("name") or ""
        elif isinstance(row, str):
            name = row
        else:
            continue

        for piece in str(name).splitlines():
            # Jangan turunkan seluruh string: canonicalizer yang menurunkan host,
            # sementara path harus tetap peka huruf besar-kecil.
            piece = piece.strip().rstrip(".")
            if not piece or "*" in piece:
                continue
            checked = evaluate(piece)
            if checked.valid:
                found.add(checked.canonical_key)

    return sorted(found)


def default_fetcher(url: str, timeout: int):
    """
    Pengambil data bawaan. Dipisahkan agar unit test bisa memakai fetcher tiruan.
    """
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    if response.status_code != 200:
        raise OsintError(f"crt.sh mengembalikan HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise OsintError("crt.sh mengembalikan data yang bukan JSON") from exc


def fetch_subdomains(
    seed: str,
    *,
    timeout: int = 12,
    retries: int = 2,
    delay: float = 0.4,
    fetcher: Optional[Callable[[str, int], object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
) -> Dict[str, object]:
    """
    Ambil kandidat subdomain untuk satu benih.

    Mengembalikan dictionary, bukan melempar error ke pemanggil:
        {
          "seed": ..., "query": ..., "ok": bool,
          "subdomains": [...], "error": "" | pesan bahasa Indonesia
        }
    """
    query = build_query(seed)
    result: Dict[str, object] = {
        "seed": (seed or "").strip(),
        "query": query,
        "ok": False,
        "subdomains": [],
        "error": "",
    }

    if not query:
        result["error"] = "Benih kosong atau tidak sah"
        return result

    ambil = fetcher or default_fetcher
    jeda = sleeper or time.sleep
    url = CRT_SH_ENDPOINT + "?q=" + quote(query, safe="") + "&output=json"

    last_error = "Permintaan gagal"
    for attempt in range(retries + 1):
        if attempt > 0:
            # Jeda sopan sebelum mencoba lagi, memanjang tiap percobaan.
            jeda(delay * attempt)
        try:
            payload = ambil(url, timeout)
            subdomains = parse_crtsh_payload(payload)
            result["ok"] = True
            result["subdomains"] = subdomains
            result["error"] = ""
            return result
        except OsintError as exc:
            last_error = str(exc)
        except requests.exceptions.Timeout:
            last_error = "Koneksi ke crt.sh timed out"
        except requests.exceptions.ConnectionError:
            last_error = "Koneksi ke crt.sh gagal"
        except requests.exceptions.RequestException:
            last_error = "Permintaan ke crt.sh gagal"
        except (TypeError, ValueError) as exc:
            last_error = f"Format data crt.sh tidak dikenali: {exc}"

    result["error"] = f"{last_error} setelah {retries + 1} percobaan"
    return result


def collect_subdomains(
    seeds: Iterable[str],
    *,
    timeout: int = 12,
    retries: int = 2,
    delay: float = 0.4,
    fetcher: Optional[Callable[[str, int], object]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
) -> Dict[str, object]:
    """
    Jalankan pencarian untuk banyak benih, lalu gabungkan kandidat unik.

    Satu benih yang gagal tidak menghentikan benih lain.
    """
    jeda = sleeper or time.sleep
    results: List[Dict[str, object]] = []
    candidates = set()

    daftar = [str(s).strip() for s in seeds if str(s).strip()]
    for index, seed in enumerate(daftar):
        if index > 0:
            jeda(delay)

        hasil = fetch_subdomains(
            seed,
            timeout=timeout,
            retries=retries,
            delay=delay,
            fetcher=fetcher,
            sleeper=sleeper,
        )
        results.append({
            "seed": hasil["seed"],
            "query": hasil["query"],
            "ok": hasil["ok"],
            "count": len(hasil["subdomains"]),
            "error": hasil["error"],
        })
        candidates.update(hasil["subdomains"])

    return {
        "ok": all(r["ok"] for r in results) if results else True,
        "seeds_count": len(daftar),
        "results": results,
        "candidates": sorted(candidates),
        "total": len(candidates),
        "error_count": sum(1 for r in results if not r["ok"]),
    }
