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
import sys
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
from osint.crtsh import collect_subdomains, fetch_subdomains as ambil_subdomain

DATA_FILE = ROOT / "data" / "submissions.json"
STATIC_DIR = Path(__file__).resolve().parent
WIB = pytz.timezone("Asia/Jakarta")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def now_wib() -> str:
    return datetime.now(WIB).isoformat(timespec="seconds")


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
    Klasifikasi:
      Hidup (HTTP <kode>)   -> DNS resolve & server membalas status apa pun.
      Mati / NXDOMAIN       -> DNS gagal, connection refused, atau timeout.
    """
    results = []
    seen = set()
    for item in urls:
        if not isinstance(item, str) or not item.strip():
            continue
        r = evaluate(item)
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
            )
            results.append({
                "url": target,
                "alive": True,
                "label": f"Hidup (HTTP {resp.status_code})",
                "http_code": resp.status_code,
            })
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.RequestException) as exc:
            name = type(exc).__name__
            results.append({
                "url": target,
                "alive": False,
                "label": "Mati / NXDOMAIN",
                "http_code": None,
                "detail": name,
            })
    return results


# -- OSINT crt.sh -----------------------------------------------------------

def fetch_subdomains(domain: str) -> dict:
    """Bungkus pengambilan crt.sh untuk dipakai endpoint lama."""
    hasil = ambil_subdomain(domain, timeout=12, retries=2, delay=0.4)
    return {
        "domain": (domain or "").strip(),
        "subdomains": hasil["subdomains"],
        "ok": hasil["ok"],
        "error": hasil["error"],
    }


def batas(angka, nilai_default: int, minimum: int, maksimum: int) -> int:
    """Ambil bilangan dari payload dengan batas aman."""
    try:
        return max(minimum, min(maksimum, int(angka)))
    except (TypeError, ValueError):
        return nilai_default


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
            entries = sorted(
                self.tracker.entries.values(),
                key=lambda e: e.get("created_at", ""),
                reverse=True,
            )
            return self._json({"entries": entries})

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
            domain = (qs.get("domain") or [""])[0]
            hasil = fetch_subdomains(domain)
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
                return self._json({"results": check_alive(urls)})

            if path == "/api/osint/scan":
                data = self._json_body()
                seeds = data.get("seeds", [])
                if not isinstance(seeds, list):
                    return self._json({"error": "seeds harus berupa daftar teks"}, 400)
                hasil = collect_subdomains(
                    seeds,
                    timeout=batas(data.get("timeout"), 12, 1, 30),
                    retries=batas(data.get("retries"), 2, 0, 4),
                    delay=max(0.0, min(5.0, float(data.get("delay", 0.4) or 0.4))),
                )
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
