"""
osint/jadwal_ics.py
Pembuat berkas kalender RFC 5545 untuk tenggat resmi imphnen.dev.

WIB = UTC+7. Setiap DTSTART ditulis dalam UTC (akhiran Z) supaya impor ke
Google Calendar tidak bergeser.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WIB = timezone(timedelta(hours=7))
UTC = timezone.utc
OUTPUT = ROOT / "data" / "jadwal-hackathon-imphnen.ics"

TONGRAK = [
    {
        "uid": "tutup-pendaftaran",
        "judul": "Tutup pendaftaran",
        "waktu": datetime(2026, 12, 4, 0, 0, tzinfo=WIB),
        "keterangan": "Pendaftaran peserta ditutup pukul 00.00 WIB.",
    },
    {
        "uid": "mulai-submit",
        "judul": "Mulai submit",
        "waktu": datetime(2026, 12, 5, 0, 0, tzinfo=WIB),
        "keterangan": "Submit link dibuka pukul 00.00 WIB.",
    },
    {
        "uid": "batas-submit",
        "judul": "Batas akhir submit",
        "waktu": datetime(2026, 12, 10, 0, 0, tzinfo=WIB),
        "keterangan": "Batas akhir submit pukul 00.00 WIB.",
    },
    {
        "uid": "pengumuman-pemenang",
        "judul": "Pengumuman pemenang",
        "waktu": datetime(2026, 12, 14, 0, 0, tzinfo=WIB),
        "keterangan": "Pengumuman pemenang pukul 00.00 WIB.",
    },
    {
        "uid": "batas-survei",
        "judul": "Batas isi survei peserta",
        "waktu": datetime(2026, 12, 27, 0, 0, tzinfo=WIB),
        "keterangan": "Survei peserta ditutup pukul 00.00 WIB.",
    },
]


def ke_utc(waktu: datetime) -> str:
    """Format datetime menjadi penanda waktu UTC RFC 5545: YYYYMMDDTHHMMSSZ."""
    return waktu.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def esc(nilai: str) -> str:
    """Escape teks sesuai aturan VALUE-TEXT RFC 5545."""
    return (
        nilai.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def bangun_ics(waktu_pembuatan: Optional[datetime] = None) -> str:
    """Rangkai seluruh isi kalender dengan akhir baris CRLF."""
    stamp = ke_utc(waktu_pembuatan or datetime.now(UTC))
    baris = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Solo Tracker//Hackathon imphnen.dev//ID",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Jadwal Hackathon Berantas Web Ilegal di Indo",
        "X-WR-TIMEZONE:Asia/Jakarta",
    ]

    for item in TONGRAK:
        baris.extend([
            "BEGIN:VEVENT",
            f"UID:{item['uid']}-2026@hackathon-imphnen.local",
            f"DTSTAMP:{stamp}",
            f"DTSTART:{ke_utc(item['waktu'])}",
            f"SUMMARY:{esc(item['judul'])}",
            f"DESCRIPTION:{esc(item['keterangan'])}",
            "END:VEVENT",
        ])

    baris.append("END:VCALENDAR")
    return "\r\n".join(baris) + "\r\n"


def tulis_ics(path: Path, waktu_pembuatan: Optional[datetime] = None) -> Path:
    """Tulis kalender ke path yang diminta dan kembalikan path itu."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(bangun_ics(waktu_pembuatan))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Buat berkas .ics berisi tenggat resmi hackathon imphnen.dev."
    )
    parser.add_argument(
        "--output", default=str(OUTPUT),
        help=f"Lokasi berkas kalender (bawaan: {OUTPUT})",
    )
    args = parser.parse_args(argv)

    path = tulis_ics(Path(args.output))
    print(f"Kalender ditulis: {path}")
    for item in TONGRAK:
        lokal = item["waktu"].strftime("%d %b %Y %H.%M")
        print(f"- {item['judul']}: {lokal} WIB = {ke_utc(item['waktu'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
