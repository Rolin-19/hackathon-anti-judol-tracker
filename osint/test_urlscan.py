"""Unit test penemuan pasif urlscan.io."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

import web.app as app_module
from osint.rutinitas import jalankan_siklus
from osint.urlscan import (
    UrlscanError,
    bangun_url,
    collect_urls,
    fetch_urls,
    parse_payload,
    siapkan_query_urlscan,
)


class TestQueryUrlscan(unittest.TestCase):
    def test_mode_domain(self):
        mode, query, error = siapkan_query_urlscan("  Domain-Nyata.ID. ")
        self.assertEqual(mode, "domain")
        self.assertEqual(query, "domain:domain-nyata.id")
        self.assertEqual(error, "")

    def test_mode_kata_kunci(self):
        mode, query, error = siapkan_query_urlscan("slot88")
        self.assertEqual(mode, "kata_kunci")
        self.assertEqual(query, "page.url:slot88")
        self.assertEqual(error, "")

    def test_kata_kunci_berspasi_dikutip(self):
        mode, query, error = siapkan_query_urlscan("promo judol")
        self.assertEqual(mode, "kata_kunci")
        self.assertEqual(query, 'page.url:"promo judol"')
        self.assertEqual(error, "")

    def test_guardrail_placeholder(self):
        mode, query, error = siapkan_query_urlscan(
            "domain-contoh-01.com", tolak_placeholder=True
        )
        self.assertEqual((mode, query), ("", ""))
        self.assertIn("Benih contoh", error)

    def test_kata_kunci_pendek(self):
        mode, query, error = siapkan_query_urlscan("abc")
        self.assertEqual((mode, query), ("", ""))
        self.assertIn("minimal 4 karakter", error)

    def test_url_memakai_endpoint_dan_ukuran(self):
        url = bangun_url("domain:domain-nyata.id", 50)
        self.assertTrue(url.startswith("https://urlscan.io/api/v1/search/?"))
        self.assertIn("q=domain%3Adomain-nyata.id", url)
        self.assertIn("size=50", url)


class TestParsePayload(unittest.TestCase):
    def test_path_aktif_dipertahankan_dan_duplikat_dibuang(self):
        payload = {
            "results": [
                {"page": {"url": "https://API.Contoh.id/Slot88?utm=1"}},
                {"page": {"domain": "www.mirror.id"}, "task": {"url": ""}},
                {"page": {"url": "https://api.contoh.id/Slot88"}},
                {"page": {"url": "https://contoh.id:8080/port"}},
                {"page": {"url": "javascript:alert(1)"}},
            ]
        }
        self.assertEqual(
            parse_payload(payload),
            ["api.contoh.id/Slot88", "mirror.id"],
        )

    def test_format_tidak_dikenali(self):
        self.assertEqual(parse_payload({"results": None}), [])
        self.assertEqual(parse_payload("salah"), [])


class TestFetchUrlscan(unittest.TestCase):
    def test_fetcher_injeksi(self):
        panggilan = []

        def fetcher(url, timeout):
            panggilan.append((url, timeout))
            return {
                "results": [
                    {"page": {"url": "https://baru.domain-nyata.id/register"}}
                ]
            }

        hasil = fetch_urls(
            "domain-nyata.id", timeout=7, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertTrue(hasil["ok"])
        self.assertEqual(hasil["mode"], "domain")
        self.assertEqual(hasil["candidates"], ["baru.domain-nyata.id/register"])
        self.assertEqual(len(panggilan), 1)
        self.assertEqual(panggilan[0][1], 7)

    def test_timeout_dilaporkan_bukan_melempar(self):
        def fetcher(url, timeout):
            raise requests.exceptions.Timeout()

        hasil = fetch_urls(
            "domain-nyata.id", retries=1, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertFalse(hasil["ok"])
        self.assertEqual(hasil["candidates"], [])
        self.assertIn("timed out", hasil["error"])
        self.assertIn("2 percobaan", hasil["error"])

    def test_rate_limit_dilaporkan_jelas(self):
        def fetcher(url, timeout):
            raise UrlscanError("urlscan.io membatasi permintaan (HTTP 429)")

        hasil = fetch_urls(
            "slot88", retries=0, fetcher=fetcher, sleeper=lambda _: None
        )
        self.assertFalse(hasil["ok"])
        self.assertIn("429", hasil["error"])


class TestCollectUrlscan(unittest.TestCase):
    def test_gabung_unik_dan_hitung_gagal(self):
        jeda = []

        def fetcher(url, timeout):
            if "q=domain%3Apertama.id" in url:
                return {"results": [{"page": {"url": "https://sama.id/slot"}}]}
            if "q=domain%3Akedua.id" in url:
                return {"results": [{"page": {"url": "https://sama.id/slot"}},
                                    {"page": {"url": "https://baru.id/register"}}]}
            raise UrlscanError("HTTP 500")

        hasil = collect_urls(
            ["pertama.id", "kedua.id", "gagal.id"],
            delay=0.25,
            fetcher=fetcher,
            sleeper=jeda.append,
        )
        self.assertEqual(hasil["total"], 2)
        self.assertEqual(hasil["candidates"], ["baru.id/register", "sama.id/slot"])
        self.assertEqual(hasil["error_count"], 1)
        self.assertEqual(jeda, [0.25, 0.25, 0.25])


class TestIntegrasiSiklus(unittest.TestCase):
    def test_crtsh_dan_urlscan_digabung_tanpa_duplikat(self):
        with tempfile.TemporaryDirectory() as tmp:
            akar = Path(tmp)
            seeds = akar / "seeds.txt"
            seeds.write_text("domain-nyata.id\n", encoding="utf-8")
            submissions = akar / "submissions.json"
            submissions.write_text(
                json.dumps({"mode": "solo", "entries": {}}), encoding="utf-8"
            )
            panggilan_urlscan = []

            def crtsh(url, timeout):
                return [{"name_value": "www.domain-nyata.id"}]

            def urlscan(url, timeout):
                panggilan_urlscan.append(url)
                return {
                    "results": [
                        {"page": {"url": "https://domain-nyata.id/slot"}},
                        {"page": {"url": "https://api.domain-nyata.id/register"}},
                    ]
                }

            ringkasan = jalankan_siklus(
                seeds,
                root=akar,
                submissions=submissions,
                log=akar / "rutinitas.log",
                backups=akar / "backups",
                fetcher=crtsh,
                urlscan_fetcher=urlscan,
                sleeper=lambda _: None,
            )

            self.assertEqual(ringkasan["sumber"], ["crtsh", "urlscan"])
            self.assertEqual(ringkasan["kandidat"], 3)
            self.assertEqual(ringkasan["ditambah"], 3)
            self.assertEqual(len(panggilan_urlscan), 1)
            self.assertIn("domain%3Adomain-nyata.id", panggilan_urlscan[0])
            sumber = {r["sumber"] for r in ringkasan["hasil_panen"]}
            self.assertEqual(sumber, {"crt.sh", "urlscan.io"})

            isi = json.loads(submissions.read_text(encoding="utf-8"))["entries"]
            self.assertIn("domain-nyata.id", isi)
            self.assertIn("domain-nyata.id/slot", isi)
            self.assertIn("api.domain-nyata.id/register", isi)


class TestRisetGabungApp(unittest.TestCase):
    def test_satu_sumber_gagal_kandidat_sumber_lain_tetap_tampil(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(app_module, "DATA_FILE", Path(tmp) / "submissions.json"), \
                 patch.object(app_module, "_CACHE_CRTSH", app_module.OrderedDict()), \
                 patch.object(
                     app_module, "ambil_subdomain",
                     return_value={"ok": False, "error": "crt.sh timeout",
                                   "subdomains": []},
                 ), \
                 patch.object(
                     app_module, "kumpulkan_urlscan",
                     return_value={
                         "ok": True,
                         "total": 1,
                         "candidates": ["domain-nyata.id/slot"],
                         "results": [{"seed": "domain-nyata.id", "ok": True,
                                      "count": 1, "error": ""}],
                     },
                 ):
                hasil = app_module.kumpulkan_riset("domain-nyata.id")

        self.assertTrue(hasil["ok"])
        self.assertEqual(hasil["baru"], ["domain-nyata.id/slot"])
        self.assertIn("crt.sh timeout", hasil["error"])


if __name__ == "__main__":
    unittest.main()
