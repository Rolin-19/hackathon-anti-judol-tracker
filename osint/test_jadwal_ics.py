"""Unit test pembuat kalender tenggat resmi imphnen.dev."""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from osint.jadwal_ics import WIB, ke_utc, tulis_ics


class TestJadwalIcs(unittest.TestCase):
    def test_konversi_wib_ke_utc(self):
        pasangan = [
            (datetime(2026, 12, 4, 0, 0, tzinfo=WIB), "20261203T170000Z"),
            (datetime(2026, 12, 5, 0, 0, tzinfo=WIB), "20261204T170000Z"),
            (datetime(2026, 12, 10, 0, 0, tzinfo=WIB), "20261209T170000Z"),
            (datetime(2026, 12, 14, 0, 0, tzinfo=WIB), "20261213T170000Z"),
            (datetime(2026, 12, 27, 0, 0, tzinfo=WIB), "20261226T170000Z"),
        ]
        for lokal, utc in pasangan:
            with self.subTest(utc=utc):
                self.assertEqual(ke_utc(lokal), utc)

    def test_isi_ics_lengkap_dan_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tulis_ics(
                Path(tmp) / "jadwal.ics",
                waktu_pembuatan=datetime(2026, 11, 26, 0, 0, tzinfo=timezone.utc),
            )
            isi = path.read_bytes().decode("utf-8")

            self.assertTrue(isi.startswith("BEGIN:VCALENDAR\r\n"))
            self.assertTrue(isi.endswith("END:VCALENDAR\r\n"))
            self.assertEqual(isi.count("BEGIN:VEVENT"), 5)
            self.assertIn("DTSTAMP:20261126T000000Z", isi)

            for utc in (
                "20261203T170000Z",
                "20261204T170000Z",
                "20261209T170000Z",
                "20261213T170000Z",
                "20261226T170000Z",
            ):
                with self.subTest(utc=utc):
                    self.assertIn(f"DTSTART:{utc}", isi)

            self.assertIn("SUMMARY:Tutup pendaftaran", isi)
            self.assertIn("SUMMARY:Batas isi survei peserta", isi)


if __name__ == "__main__":
    unittest.main()
