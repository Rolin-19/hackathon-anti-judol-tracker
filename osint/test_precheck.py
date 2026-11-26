"""Unit test deteksi parkir, judul, redirect, dan batas baca pre-check."""

import unittest
from unittest.mock import patch

import requests

from osint.precheck import (
    BATAS_BACA,
    LABEL_HIDUP,
    LABEL_MATI,
    LABEL_PARKIR,
    ambil_title,
    baca_ringkas,
    klasifikasi_gagal,
    klasifikasi_precheck,
    teks_parkir,
)
from web.app import check_alive


class ResponsTiruan:
    def __init__(self, status_code=200, text="", headers=None, potongan=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.potongan = potongan
        self.ditutup = False

    @property
    def raw(self):
        if self.potongan is None:
            return None

        class Raw:
            def __init__(self, data):
                self.data = data
                self.pos = 0
                self.close_count = 0

            def read(self, jumlah):
                bagian = self.data[self.pos:self.pos + jumlah]
                self.pos += len(bagian)
                return bagian

            def close(self):
                self.close_count += 1

        return Raw(self.potongan)

    def close(self):
        self.ditutup = True


class TestPrecheck(unittest.TestCase):
    def test_ambil_title_dan_panjang_dibatasi(self):
        title = ambil_title("<html><head><title>  Slot &amp; Suguhan &lt;promo&gt;  </title></head></html>")
        self.assertEqual(title, "Slot & Suguhan <promo>")
        panjang = ambil_title("<title>" + "a" * 300 + "</title>")
        self.assertEqual(len(panjang), 120)

    def test_pola_parkir(self):
        for teks in (
            "SedoParking - domain for sale",
            "Bodis parked domain",
            "This domain is for sale on Dan.com",
            "Afternic listing",
            "HugeDomains.com",
            "GoDaddy parked domain",
            "Suspended page",
        ):
            with self.subTest(teks=teks):
                self.assertTrue(teks_parkir(teks))
        self.assertFalse(teks_parkir("Slot88 Login — Situs Resmi"))

    def test_hidup_bukan_parkir(self):
        hasil = klasifikasi_precheck(
            "https://hidup.id/slot",
            ResponsTiruan(503, "<title>Slot88 — Halaman Aktif</title>"),
        )
        self.assertTrue(hasil["alive"])
        self.assertFalse(hasil["parking"])
        self.assertEqual(hasil["label"], LABEL_HIDUP)
        self.assertEqual(hasil["title"], "Slot88 — Halaman Aktif")
        self.assertEqual(hasil["http_code"], 503)

    def test_parkir_diturunkan_prioritas(self):
        hasil = klasifikasi_precheck(
            "https://parkir.id",
            ResponsTiruan(200, "<title>SedoParking — domain for sale</title>"),
        )
        self.assertTrue(hasil["alive"])
        self.assertTrue(hasil["parking"])
        self.assertEqual(hasil["label"], LABEL_PARKIR)

    def test_redirect_domain_lain_dicatat(self):
        hasil = klasifikasi_precheck(
            "https://awal.id",
            ResponsTiruan(
                302,
                "",
                headers={"Location": "https://WWW.TujuanBaru.id/Slot?ref=1"},
            ),
        )
        self.assertEqual(hasil["redirect_target"], "https://WWW.TujuanBaru.id/Slot?ref=1")
        self.assertEqual(hasil["redirect_key"], "tujuanbaru.id/Slot")
        self.assertEqual(hasil["label"], LABEL_HIDUP)

    def test_redirect_domain_sama_tidak_dicatat(self):
        hasil = klasifikasi_precheck(
            "https://www.awal.id",
            ResponsTiruan(301, "", headers={"Location": "https://awal.id/slot"}),
        )
        self.assertEqual(hasil["redirect_target"], "")
        self.assertEqual(hasil["redirect_key"], "")

    def test_baca_ringkas_maksimal_32_kb(self):
        data = (b"x" * (BATAS_BACA + 500))
        respons = ResponsTiruan(200, "", potongan=data)
        teks = baca_ringkas(respons)
        self.assertEqual(len(teks), BATAS_BACA)

    def test_gagal_timeout(self):
        hasil = klasifikasi_gagal(
            "https://mati.id", requests.exceptions.Timeout(), "mati.id"
        )
        self.assertFalse(hasil["alive"])
        self.assertEqual(hasil["label"], LABEL_MATI)
        self.assertEqual(hasil["detail"], "Timeout")


class TestCheckAliveApp(unittest.TestCase):
    def test_check_alive_memakai_payload_dan_menyimpan_probe(self):
        panggilan = {}

        def get(url, **kwargs):
            panggilan.update(kwargs)
            panggilan["url"] = url
            return ResponsTiruan(200, "<title>Forum Slot — Aktif</title>")

        with patch("web.app.requests.get", side_effect=get):
            hasil = check_alive(["https://hidup.id/slot"])

        self.assertEqual(hasil[0]["label"], LABEL_HIDUP)
        self.assertEqual(hasil[0]["canonical_key"], "hidup.id/slot")
        self.assertEqual(panggilan["timeout"], 4)
        self.assertTrue(panggilan["stream"])
        self.assertFalse(panggilan["allow_redirects"])
        self.assertIn("Mozilla", panggilan["headers"]["User-Agent"])


if __name__ == "__main__":
    unittest.main()
