"""Unit test modul riset OSINT pasif crt.sh."""

import sys
import unittest
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from osint.crtsh import (
    OsintError,
    build_query,
    collect_subdomains,
    fetch_subdomains,
    parse_crtsh_payload,
)


class TestBuildQuery(unittest.TestCase):
    def test_domain_menjadi_wildcard_subdomain(self):
        self.assertEqual(build_query("https://www.TokoBudi.id/"), "%.tokobudi.id")

    def test_kata_kunci_menjadi_pencarian_teks(self):
        self.assertEqual(build_query("PROMO", ), "%promo%")

    def test_kosong(self):
        self.assertEqual(build_query("   "), "")


class TestParsingRespons(unittest.TestCase):
    def test_parsing_bersih_wildcard_www_duplikat(self):
        payload = [
            {"name_value": "*.tokobudi.id"},
            {"name_value": "www.TokoBudi.id\nlogin.tokobudi.id"},
            {"name_value": "blog.tokobudi.id"},
            {"name_value": "login.tokobudi.id"},
        ]
        self.assertEqual(
            parse_crtsh_payload(payload),
            ["blog.tokobudi.id", "login.tokobudi.id", "tokobudi.id"],
        )

    def test_path_dipertahankan_dan_ip_dibuang(self):
        payload = [
            {"name_value": "https://Example.COM/PathCase"},
            {"name_value": "192.168.1.1"},
            {"name_value": "localhost"},
        ]
        self.assertEqual(parse_crtsh_payload(payload), ["example.com/PathCase"])

    def test_format_tidak_terduga_tidak_crash(self):
        for payload in (None, {}, "ngawur", 42, {"results": "bukan list"}):
            with self.subTest(payload=payload):
                self.assertEqual(parse_crtsh_payload(payload), [])

    def test_results_dict_diterima(self):
        payload = {"results": [{"name_value": "api.contoh.id"}]}
        self.assertEqual(parse_crtsh_payload(payload), ["api.contoh.id"])


class TestPengambilanData(unittest.TestCase):
    def test_sukses_memakai_fetcher_tiruan(self):
        def fetcher(url, timeout):
            self.assertIn("q=%25.tokobudi.id", url)
            return [{"name_value": "www.tokobudi.id"}, {"name_value": "m.tokobudi.id"}]

        hasil = fetch_subdomains("tokobudi.id", fetcher=fetcher, sleeper=lambda _: None)
        self.assertTrue(hasil["ok"])
        self.assertEqual(hasil["subdomains"], ["m.tokobudi.id", "tokobudi.id"])
        self.assertEqual(hasil["error"], "")

    def test_timeout_dilaporkan_bahasa_indonesia(self):
        def fetcher(url, timeout):
            raise requests.exceptions.Timeout()

        hasil = fetch_subdomains(
            "tokobudi.id", retries=1, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertFalse(hasil["ok"])
        self.assertIn("timed out", hasil["error"])
        self.assertEqual(hasil["subdomains"], [])

    def test_http_gagal_dilaporkan(self):
        def fetcher(url, timeout):
            raise OsintError("crt.sh mengembalikan HTTP 503")

        hasil = fetch_subdomains(
            "tokobudi.id", retries=0, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertFalse(hasil["ok"])
        self.assertIn("HTTP 503", hasil["error"])

    def test_json_rusak_tidak_crash(self):
        def fetcher(url, timeout):
            raise OsintError("crt.sh mengembalikan data yang bukan JSON")

        hasil = fetch_subdomains(
            "tokobudi.id", retries=0, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertFalse(hasil["ok"])
        self.assertIn("bukan JSON", hasil["error"])

    def test_retry_ulpas(self):
        panggilan = []

        def fetcher(url, timeout):
            panggilan.append(url)
            if len(panggilan) < 3:
                raise requests.exceptions.ConnectionError()
            return [{"name_value": "api.contoh.id"}]

        hasil = fetch_subdomains(
            "contoh.id", retries=2, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertTrue(hasil["ok"])
        self.assertEqual(len(panggilan), 3)
        self.assertEqual(hasil["subdomains"], ["api.contoh.id"])


class TestKumpulanBenih(unittest.TestCase):
    def test_gabung_unik_dan_gagal_tidak_menghentikan(self):
        def fetcher(url, timeout):
            if "%25.kosong-xyz.id" in url:
                raise requests.exceptions.ConnectionError()
            return [{"name_value": "www.contoh.id"}, {"name_value": "m.contoh.id"}]

        jeda = []
        hasil = collect_subdomains(
            ["https://www.contoh.id/", "kosong-xyz.id", "contoh.id"],
            retries=0,
            fetcher=fetcher,
            sleeper=lambda d: jeda.append(d),
        )
        self.assertEqual(hasil["candidates"], ["contoh.id", "m.contoh.id"])
        self.assertEqual(hasil["total"], 2)
        self.assertEqual(hasil["error_count"], 1)
        self.assertEqual(len(hasil["results"]), 3)
        self.assertEqual(len(jeda), 2)

    def test_benih_kosong(self):
        hasil = collect_subdomains(["", "  "], sleeper=lambda _: None)
        self.assertTrue(hasil["ok"])
        self.assertEqual(hasil["candidates"], [])
        self.assertEqual(hasil["seeds_count"], 0)


if __name__ == "__main__":
    unittest.main()
