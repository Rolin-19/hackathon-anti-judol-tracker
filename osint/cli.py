"""
osint/cli.py
Skrip terminal untuk riset OSINT pasif berbasis daftar benih.

Contoh:
    python -m osint.cli --input data/seeds.txt
    python -m osint.cli --input data/seeds.txt --no-import --output hasil.json
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from osint.crtsh import collect_subdomains
from web.app import SoloTracker


def read_seeds(path: Path) -> List[str]:
    """Baca benih dari berkas teks; baris kosong dan komentar diabaikan."""
    if not path.exists():
        raise FileNotFoundError(f"Berkas benih tidak ditemukan: {path}")

    hasil = []
    terlihat = set()
    with open(path, "r", encoding="utf-8") as fh:
        for baris in fh:
            value = baris.split("#", 1)[0].strip()
            if not value or value in terlihat:
                continue
            terlihat.add(value)
            hasil.append(value)
    return hasil


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Riset subdomain pasif dari crt.sh untuk tracker mode solo."
    )
    parser.add_argument(
        "--input", default="data/seeds.txt",
        help="Berkas teks berisi domain/kata kunci, satu per baris.",
    )
    parser.add_argument("--timeout", type=int, default=12, help="Timeout request detik.")
    parser.add_argument("--delay", type=float, default=0.4, help="Jeda antar permintaan.")
    parser.add_argument("--retries", type=int, default=2, help="Jumlah percobaan ulang.")
    parser.add_argument("--limit", type=int, default=0, help="Batasi jumlah benih (0 = semua).")
    parser.add_argument("--output", default="", help="Simpan ringkasan JSON ke berkas ini.")
    parser.add_argument(
        "--no-import", action="store_true",
        help="Hanya tampilkan hasil, jangan masukkan ke antrean tracker.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    path = Path(args.input)
    if not path.is_absolute():
        path = ROOT / path

    try:
        seeds = read_seeds(path)
    except (FileNotFoundError, OSError) as exc:
        print(f"Gagal: {exc}", file=sys.stderr)
        return 2

    if not seeds:
        print("Berkas benih kosong. Tambahkan domain atau kata kunci satu per baris.")
        return 0
    if args.limit > 0:
        seeds = seeds[:args.limit]

    print(f"Memindai {len(seeds)} benih dari {path.name} (passive crt.sh)...")
    hasil = collect_subdomains(
        seeds,
        timeout=args.timeout,
        retries=args.retries,
        delay=args.delay,
    )

    for row in hasil["results"]:
        if row["ok"]:
            print(f"  {row['seed']}: {row['count']} kandidat")
        else:
            print(f"  {row['seed']}: GAGAL - {row['error']}")

    print(f"Total kandidat unik: {hasil['total']}")
    if hasil["error_count"]:
        print(f"Benih gagal: {hasil['error_count']}")

    ringkasan = dict(hasil)
    ringkasan["added"] = 0
    ringkasan["skipped_existing"] = 0

    if not args.no_import and hasil["candidates"]:
        tracker = SoloTracker()
        # Entri lama tidak ditimpa, terutama Valid / Menunggu / Duplikat.
        baru = [c for c in hasil["candidates"] if c not in tracker.entries]
        ringkasan["skipped_existing"] = hasil["total"] - len(baru)
        if baru:
            diproses = tracker.process_bulk(baru)
            ringkasan["added"] = diproses["unique_count"]
            print(f"Masuk antrean: {ringkasan['added']}")
        print(f"Dilewati (sudah ada): {ringkasan['skipped_existing']}")

    if args.output:
        out = Path(args.output)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(ringkasan, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Ringkasan disimpan: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
