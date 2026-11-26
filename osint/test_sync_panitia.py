"""Unit test sinkronisasi massal status dari dashboard panitia."""

import json
import tempfile
import unittest
from pathlib import Path

from canonicalizer.rules import evaluate
from web.app import SoloTracker, deteksi_status_panitia, ekstrak_url_panitia
from web.app import sinkron_panitia


def entri(url: str, status: str) -> dict:
    r = evaluate(url)
    return {
        "submit_url": r.submit_url,
        "canonical_key": r.canonical_key,
        "raw": url,
        "status": status,
        "score": 1 if status == "Valid" else 0,
        "reason": "",
        "reject_reason": "",
        "created_at": "2026-11-26T07:00:00+07:00",
        "submitted_at": "2026-11-26T07:01:00+07:00",
    }


class TestEkstraksiPanitia(unittest.TestCase):
    def test_ekstrak_url_lengkap_dan_domain_polos(self):
        baris = "Baris 9: https://www.Contoh.id/Slot?x=2 — Valid (imphnen.dev)"
        hasil = ekstrak_url_panitia(baris)
        self.assertIn("https://www.Contoh.id/Slot?x=2", hasil)
        self.assertIn("imphnen.dev", hasil)

    def test_deteksi_status(self):
        self.assertEqual(deteksi_status_panitia("https://a.id — Valid"), "Valid")
        self.assertEqual(deteksi_status_panitia("https://a.id — Duplikat"), "Duplikat")
        self.assertEqual(deteksi_status_panitia("https://a.id — Ditolak"), "Ditolak")
        self.assertEqual(deteksi_status_panitia("https://a.id — tidak valid"), "Ditolak")
        self.assertEqual(deteksi_status_panitia("https://a.id — diproses"), "")


class TestSinkronPanitia(unittest.TestCase):
    def test_update_massal_dan_kunci_final(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submissions.json"
            lama = {
                "valid.id/slot": entri("https://valid.id/slot", "Menunggu Penilaian"),
                "duplikat.id": entri("https://duplikat.id", "Menunggu Penilaian"),
                "ditolak.id": entri("https://ditolak.id", "Menunggu Penilaian"),
                "terkunci.id": entri("https://terkunci.id", "Valid"),
            }
            path.write_text(
                json.dumps({"mode": "solo", "entries": lama}, ensure_ascii=False),
                encoding="utf-8",
            )
            tracker = SoloTracker(path)

            teks = "\n".join([
                "https://valid.id/slot — Valid",
                "https://duplikat.id — Duplikat",
                "https://ditolak.id — Ditolak, alasan: bukan situs judol",
                "https://terkunci.id — Ditolak",
                "https://tidak-ada.id — Valid",
                "baris tanpa status",
            ])
            hasil = sinkron_panitia(tracker, teks)

            self.assertEqual(hasil["baris_dikenali"], 5)
            self.assertEqual(hasil["terupdate"], 3)
            self.assertEqual(hasil["tidak_cocok"], 1)
            self.assertTrue(hasil["gagal"] >= 1)
            self.assertEqual(tracker.entries["valid.id/slot"]["status"], "Valid")
            self.assertEqual(tracker.entries["valid.id/slot"]["score"], 1)
            self.assertEqual(tracker.entries["duplikat.id"]["status"], "Duplikat")
            self.assertEqual(tracker.entries["ditolak.id"]["status"], "Ditolak")
            self.assertIn("bukan situs judol", tracker.entries["ditolak.id"]["reject_reason"])
            self.assertEqual(tracker.entries["terkunci.id"]["status"], "Valid")
            self.assertEqual(hasil["summary"]["total_score"], 2)

            tersimpan = json.loads(path.read_text(encoding="utf-8"))["entries"]
            self.assertEqual(tersimpan["ditolak.id"]["status"], "Ditolak")

    def test_baris_kosong_tidak_mengubah(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submissions.json"
            path.write_text(
                json.dumps({"mode": "solo", "entries": {}}), encoding="utf-8"
            )
            hasil = sinkron_panitia(SoloTracker(path), "   \n\n")
            self.assertEqual(hasil["total_baris"], 0)
            self.assertEqual(hasil["terupdate"], 0)


if __name__ == "__main__":
    unittest.main()
