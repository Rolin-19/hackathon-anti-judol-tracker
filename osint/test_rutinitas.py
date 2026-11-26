"""Unit test siklus otomatisasi OSINT pasif mode solo."""

import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import requests

from canonicalizer.rules import evaluate
from osint.rutinitas import (
    adalah_placeholder,
    baca_benih,
    buang_mati,
    buat_backup,
    impor_aman,
    jalankan_siklus,
    kanon,
    periksa_hidup,
)
from web.app import WIB, SoloTracker


WAKTU = WIB.localize(datetime(2026, 11, 26, 7, 30))


class ResponsTiruan:
    def __init__(self, status_code):
        self.status_code = status_code


def tulis_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def entri(key: str, status: str, score: int = 0) -> dict:
    return {
        "submit_url": f"https://{key}",
        "canonical_key": key,
        "raw": key,
        "status": status,
        "score": score,
        "reason": "",
        "reject_reason": "",
        "created_at": "2026-11-20T07:00:00+07:00",
        "submitted_at": "",
    }


class TestBacaBenih(unittest.TestCase):
    def test_komentar_kosong_dan_duplikat_diabaikan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.txt"
            path.write_text(
                "# komentar\ncontoh.id\n\ncontoh.id  # duplikat\napi.contoh.id\n",
                encoding="utf-8",
            )
            benih, catatan = baca_benih(path)
            self.assertEqual(benih, ["contoh.id", "api.contoh.id"])
            self.assertEqual(catatan, "")

    def test_fallback_ke_contoh_dengan_catatan(self):
        with tempfile.TemporaryDirectory() as tmp:
            utama = Path(tmp) / "seeds.txt"
            contoh = Path(tmp) / "seeds.example.txt"
            contoh.write_text("contoh.id\n", encoding="utf-8")
            benih, catatan = baca_benih(utama, contoh)
            self.assertEqual(benih, ["contoh.id"])
            self.assertIn("belum ada", catatan)
            self.assertIn("seeds.example.txt", catatan)


class TestKanon(unittest.TestCase):
    def test_path_kasus_dipertahankan_dan_submit_url_https(self):
        r = kanon("https://www.API.Contoh.id/NamaKhusus?x=1")
        self.assertIsNotNone(r)
        self.assertEqual(r.canonical_key, "api.contoh.id/NamaKhusus")
        self.assertEqual(r.submit_url, "https://api.contoh.id/NamaKhusus")

    def test_ip_port_localhost_dan_panjang_ditolak(self):
        for nilai in (
            "127.0.0.1",
            "localhost",
            "contoh.id:8080",
            "a" * 301 + ".id",
            "ftp://contoh.id",
        ):
            with self.subTest(nilai=nilai[:30]):
                self.assertIsNone(kanon(nilai))


class TestImporAman(unittest.TestCase):
    def test_tidak_menimpa_status_terkunci_termasuk_ditolak(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submissions.json"
            lama = {
                "valid.id": entri("valid.id", "Valid", 1),
                "waiting.id": entri("waiting.id", "Menunggu Penilaian"),
                "duplicate.id": entri("duplicate.id", "Duplikat"),
                "rejected.id": entri("rejected.id", "Ditolak"),
            }
            tulis_json(path, {"mode": "solo", "entries": lama})
            sebelum = copy.deepcopy(lama)

            tracker = SoloTracker(path)
            hasil = impor_aman(
                tracker,
                ["valid.id", "waiting.id", "duplicate.id", "rejected.id",
                 "https://baru.id", "bukan-domain"],
                WAKTU,
            )

            self.assertEqual(hasil["added"], 1)
            self.assertEqual(hasil["skipped_existing"], 4)
            self.assertEqual(hasil["invalid"], 1)
            self.assertEqual(hasil["keys"], ["baru.id"])

            sesudah = json.loads(path.read_text(encoding="utf-8"))["entries"]
            for key in sebelum:
                self.assertEqual(sesudah[key], sebelum[key], f"{key} berubah")
            self.assertEqual(sesudah["baru.id"]["status"], "Siap Submit")
            self.assertEqual(sesudah["baru.id"]["submit_url"], "https://baru.id")


class TestSiklusPenuh(unittest.TestCase):
    def test_siklus_dengan_fetcher_tiruan(self):
        def fetcher(url, timeout):
            self.assertIn("%25.contoh.id", url)
            return [
                {"name_value": "*.contoh.id"},
                {"name_value": "www.valid.id"},
                {"name_value": "rejected.id"},
                {"name_value": "API.Contoh.id/NamaKhusus"},
                {"name_value": "baru.contoh.id\nbaru.contoh.id"},
                {"name_value": "127.0.0.1"},
                {"name_value": "localhost"},
                {"name_value": "port.contoh.id:8080"},
            ]

        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            seeds = akar / "seeds.txt"
            seeds.write_text("# daftar\ncontoh.id\n\ncontoh.id\n", encoding="utf-8")

            submissions = akar / "submissions.json"
            lama = {
                "valid.id": entri("valid.id", "Valid", 1),
                "rejected.id": entri("rejected.id", "Ditolak"),
            }
            tulis_json(submissions, {"mode": "solo", "entries": lama})
            sebelum = copy.deepcopy(lama)

            log = akar / "rutinitas.log"
            backups = akar / "backups"

            ringkasan = jalankan_siklus(
                seeds,
                root=akar,
                submissions=submissions,
                log=log,
                backups=backups,
                fetcher=fetcher,
                sleeper=lambda _: None,
                sources=("crtsh",),
                waktu=WAKTU,
            )

            self.assertEqual(ringkasan["benih"], 1)
            self.assertEqual(ringkasan["kandidat"], 4)
            self.assertEqual(ringkasan["ditambah"], 2)
            self.assertEqual(ringkasan["dilewati"], 2)
            self.assertEqual(ringkasan["kunci_baru"],
                             ["api.contoh.id/NamaKhusus", "baru.contoh.id"])

            sesudah = json.loads(submissions.read_text(encoding="utf-8"))["entries"]
            for key in sebelum:
                self.assertEqual(sesudah[key], sebelum[key])
            self.assertEqual(
                sesudah["api.contoh.id/NamaKhusus"]["submit_url"],
                "https://api.contoh.id/NamaKhusus",
            )

            backup = backups / "submissions-2026-11-26_0730.json"
            self.assertTrue(backup.exists())
            isi_backup = json.loads(backup.read_text(encoding="utf-8"))
            self.assertIn("baru.contoh.id", isi_backup["entries"])
            self.assertEqual(isi_backup["mode"], "solo")

            isi_log = log.read_text(encoding="utf-8")
            self.assertIn("benih=1", isi_log)
            self.assertIn("masuk_siap_submit=2", isi_log)
            self.assertIn("backup=submissions-2026-11-26_0730.json", isi_log)


class TestCekHidup(unittest.TestCase):
    def test_status_503_hidup_timeout_mati(self):
        def ambil(url, timeout):
            if "siap-mati" in url or "valid-mati" in url:
                raise requests.exceptions.Timeout()
            return ResponsTiruan(503)

        hasil = periksa_hidup(
            [
                ("siap-hidup.id", "https://siap-hidup.id"),
                ("siap-mati.id", "https://siap-mati.id"),
                ("valid-mati.id", "https://valid-mati.id"),
            ],
            timeout=2,
            delay=0,
            ambil=ambil,
        )
        self.assertTrue(hasil[0]["alive"])
        self.assertEqual(hasil[0]["label"], "Hidup (Siap Tembak)")
        self.assertFalse(hasil[1]["alive"])
        self.assertEqual(hasil[1]["label"], "Mati / NXDOMAIN")
        self.assertFalse(hasil[2]["alive"])

    def test_prune_hanya_siap_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submissions.json"
            tulis_json(path, {
                "mode": "solo",
                "entries": {
                    "siap-mati.id": entri("siap-mati.id", "Siap Submit"),
                    "valid-mati.id": entri("valid-mati.id", "Valid", 1),
                },
            })
            tracker = SoloTracker(path)
            hasil = [
                {"canonical_key": "siap-mati.id", "alive": False},
                {"canonical_key": "valid-mati.id", "alive": False},
            ]
            self.assertEqual(buang_mati(tracker, hasil), ["siap-mati.id"])
            sisa = json.loads(path.read_text(encoding="utf-8"))["entries"]
            self.assertNotIn("siap-mati.id", sisa)
            self.assertIn("valid-mati.id", sisa)

    def test_prune_dead_default_tidak_menghapus(self):
        def ambil(url, timeout):
            raise requests.exceptions.ConnectionError()

        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            seeds = akar / "seeds.txt"
            seeds.write_text("", encoding="utf-8")
            submissions = akar / "submissions.json"
            tulis_json(submissions, {
                "mode": "solo",
                "entries": {"siap-mati.id": entri("siap-mati.id", "Siap Submit")},
            })

            ringkasan = jalankan_siklus(
                seeds,
                root=akar,
                submissions=submissions,
                log=akar / "rutinitas.log",
                backups=akar / "backups",
                fetcher=lambda url, timeout: [],
                sleeper=lambda _: None,
                sources=("crtsh",),
                alive_fetcher=ambil,
                check_alive=True,
                alive_delay=0,
                waktu=WAKTU,
            )
            self.assertEqual(ringkasan["mati"], 1)
            self.assertEqual(ringkasan["dihapus"], 0)
            self.assertIn(
                "siap-mati.id",
                json.loads(submissions.read_text(encoding="utf-8"))["entries"],
            )


class TestBackup(unittest.TestCase):
    def test_backup_memakai_nama_wib_dan_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            tracker_path = akar / "submissions.json"
            tulis_json(tracker_path, {
                "mode": "solo",
                "entries": {"contoh.id": entri("contoh.id", "Siap Submit")},
            })
            path = buat_backup(SoloTracker(tracker_path), akar / "backups", WAKTU)
            self.assertEqual(path.name, "submissions-2026-11-26_0730.json")
            isi = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(isi["summary"]["total_entries"], 1)
            self.assertEqual(isi["exported_at"], "2026-11-26T07:30:00+07:00")


class TestGuardrailPlaceholder(unittest.TestCase):
    def test_placeholder_dilewati_tanpa_query_crtsh(self):
        placeholder = [
            "contoh-target-utama.id",
            "domain-contoh-01.com",
            "mirror-utama-01.org",
            "mirror-pivot-02.net",
            "portal-cadangan-03.biz",
            "example.com",
            "tokobudi.id",
        ]
        for item in placeholder:
            with self.subTest(item=item):
                self.assertTrue(adalah_placeholder(item))
        self.assertFalse(adalah_placeholder("domain-nyata.id"))

        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            seeds = akar / "seeds.txt"
            seeds.write_text("\n".join(placeholder + ["domain-nyata.id"]), encoding="utf-8")
            submissions = akar / "submissions.json"
            tulis_json(submissions, {"mode": "solo", "entries": {}})
            log = akar / "rutinitas.log"
            panggilan = []

            def fetcher(url, timeout):
                panggilan.append(url)
                return [{"name_value": "api.domain-nyata.id"}]

            ringkasan = jalankan_siklus(
                seeds,
                root=akar,
                submissions=submissions,
                log=log,
                backups=akar / "backups",
                fetcher=fetcher,
                sleeper=lambda _: None,
                sources=("crtsh",),
                waktu=WAKTU,
            )

            self.assertEqual(len(panggilan), 1)
            self.assertIn("domain-nyata.id", panggilan[0])
            self.assertEqual(ringkasan["benih"], 8)
            self.assertEqual(ringkasan["benih_dipakai"], 1)
            self.assertEqual(ringkasan["placeholder"], 7)
            self.assertEqual(ringkasan["ditambah"], 1)

            isi_log = log.read_text(encoding="utf-8")
            self.assertIn("dipakai=1", isi_log)
            self.assertIn("placeholder=7", isi_log)
            for item in placeholder:
                self.assertNotIn(item, isi_log)

    def test_semua_placeholder_tidak_menyentuh_jaringan(self):
        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            seeds = akar / "seeds.txt"
            seeds.write_text(
                "domain-contoh-01.com\nmirror-pivot-01.org\nexample.com\ntokobudi.id\n",
                encoding="utf-8",
            )
            submissions = akar / "submissions.json"
            tulis_json(submissions, {"mode": "solo", "entries": {}})
            log = akar / "rutinitas.log"

            def fetcher(url, timeout):
                self.fail(f"Placeholder tidak boleh dikirim ke crt.sh: {url}")

            ringkasan = jalankan_siklus(
                seeds,
                root=akar,
                submissions=submissions,
                log=log,
                backups=akar / "backups",
                fetcher=fetcher,
                sleeper=lambda _: None,
                sources=("crtsh",),
                waktu=WAKTU,
            )

            self.assertEqual(ringkasan["benih_dipakai"], 0)
            self.assertEqual(ringkasan["placeholder"], 4)
            self.assertEqual(ringkasan["kandidat"], 0)
            self.assertEqual(ringkasan["gagal_crtsh"], 0)
            self.assertIn("placeholder=4", log.read_text(encoding="utf-8"))
            self.assertEqual(
                json.loads(submissions.read_text(encoding="utf-8"))["entries"], {}
            )


if __name__ == "__main__":
    unittest.main()
