"""Unit test aturan resmi panitia + mesin status solo."""

import unittest

from canonicalizer.rules import (
    evaluate,
    is_locked,
    can_transition,
    score_for,
    MAX_LEN,
    STATUSES,
)


class TestOfficialExamples(unittest.TestCase):
    """Contoh resmi panitia wajib lulus."""

    CASES = [
        ("https://www.TokoBudi.id/", "tokobudi.id"),
        ("tokobudi.id", "tokobudi.id"),
        ("http://TOKOBUDI.id/?utm=ig#promo", "tokobudi.id"),
        ("tokobudi.id/produk", "tokobudi.id/produk"),
        ("blog.tokobudi.id", "blog.tokobudi.id"),
        ("https://www.Example.COM/PathCase/?a=1#top", "example.com/PathCase"),
    ]

    def test_official_examples(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertTrue(r.valid, r.reason)
                self.assertEqual(r.canonical_key, expected)

    def test_submit_url_mempunyai_skema(self):
        for raw, _ in self.CASES:
            r = evaluate(raw)
            self.assertTrue(r.submit_url.startswith(("http://", "https://")))

    def test_bare_domain_mendapat_https(self):
        r = evaluate("tokobudi.id")
        self.assertEqual(r.submit_url, "https://tokobudi.id")


class TestSubdomainDipertahankan(unittest.TestCase):
    def test_subdomain_selain_www_tidak_dibuang(self):
        for sub in ("m", "wap", "login", "daftar", "blog"):
            with self.subTest(sub=sub):
                r = evaluate(f"https://{sub}.tokobudi.id/")
                self.assertTrue(r.valid)
                self.assertEqual(r.canonical_key, f"{sub}.tokobudi.id")

    def test_www_dibuang(self):
        r = evaluate("https://www.tokobudi.id/")
        self.assertEqual(r.canonical_key, "tokobudi.id")


class TestPenolakan(unittest.TestCase):
    def test_tolak_port(self):
        for raw in ("https://contoh.id:8080", "http://contoh.id:3000/path"):
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertFalse(r.valid)
                self.assertIn("port", r.reason.lower())

    def test_tolak_userpass(self):
        r = evaluate("https://user:pass@contoh.id/")
        self.assertFalse(r.valid)
        self.assertIn("username", r.reason.lower())

    def test_tolak_ipv4(self):
        for raw in ("http://192.168.1.1/", "https://8.8.8.8/path"):
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertFalse(r.valid)
                self.assertIn("ip", r.reason.lower())

    def test_tolak_ipv6(self):
        r = evaluate("http://[::1]/")
        self.assertFalse(r.valid)
        self.assertIn("ip", r.reason.lower())

    def test_tolak_localhost(self):
        r = evaluate("http://localhost/")
        self.assertFalse(r.valid)
        self.assertIn("localhost", r.reason.lower())

    def test_tolak_domain_tanpa_tld(self):
        for raw in ("http://intranet/", "https://abc", "https://foo."):
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertFalse(r.valid)

    def test_tolak_skema_lain(self):
        for raw in ("ftp://contoh.id", "javascript:alert(1)"):
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertFalse(r.valid)

    def test_tolak_panjang_submit_url(self):
        raw = "https://contoh.id/" + ("a" * (MAX_LEN + 1))
        r = evaluate(raw)
        self.assertFalse(r.valid)
        self.assertIn("300", r.reason)

    def test_tolak_panjang_canonical_key(self):
        raw = "https://contoh.id/" + ("b" * (MAX_LEN + 1)) + "/?x=1"
        r = evaluate(raw)
        self.assertFalse(r.valid)

    def test_tolak_input_kosong(self):
        for raw in ("", "   ", None):
            with self.subTest(raw=raw):
                r = evaluate(raw)
                self.assertFalse(r.valid)


class TestCanonicalisasi(unittest.TestCase):
    def test_query_dan_fragment_dibuang(self):
        r = evaluate("https://contoh.id/halaman?a=1&b=2#bagian")
        self.assertEqual(r.canonical_key, "contoh.id/halaman")

    def test_trailing_slash_dibuang(self):
        r = evaluate("https://contoh.id/halamansatu/")
        self.assertEqual(r.canonical_key, "contoh.id/halamansatu")

    def test_case_path_dipertahankan(self):
        r = evaluate("https://contoh.id/Path/Case")
        self.assertEqual(r.canonical_key, "contoh.id/Path/Case")

    def test_host_diboldkan(self):
        r = evaluate("https://CoNtOh.Id/PATH")
        self.assertEqual(r.canonical_key, "contoh.id/PATH")
        self.assertEqual(r.submit_url, "https://contoh.id/PATH")


class TestMesinStatus(unittest.TestCase):
    def test_lima_status_resmi(self):
        self.assertEqual(
            STATUSES,
            (
                "Siap Submit",
                "Menunggu Penilaian",
                "Valid",
                "Duplikat",
                "Ditolak",
            ),
        )

    def test_poin(self):
        self.assertEqual(score_for("Valid"), 1)
        for s in ("Siap Submit", "Menunggu Penilaian", "Duplikat", "Ditolak"):
            with self.subTest(status=s):
                self.assertEqual(score_for(s), 0)

    def test_kunci_permanen(self):
        self.assertTrue(is_locked("Menunggu Penilaian"))
        self.assertTrue(is_locked("Valid"))
        self.assertTrue(is_locked("Duplikat"))
        self.assertFalse(is_locked("Siap Submit"))
        self.assertFalse(is_locked("Ditolak"))

    def test_transisi_diizinkan(self):
        self.assertTrue(can_transition("Siap Submit", "Menunggu Penilaian"))
        self.assertTrue(can_transition("Menunggu Penilaian", "Valid"))
        self.assertTrue(can_transition("Menunggu Penilaian", "Ditolak"))
        self.assertTrue(can_transition("Siap Submit", "Ditolak"))
        self.assertTrue(can_transition("Siap Submit", "Duplikat"))
        self.assertTrue(can_transition("Menunggu Penilaian", "Duplikat"))
        self.assertTrue(can_transition("Ditolak", "Siap Submit"))

    def test_duplikat_tidak_bisa_requeue(self):
        self.assertFalse(can_transition("Duplikat", "Siap Submit"))
        self.assertFalse(can_transition("Duplikat", "Valid"))
        self.assertFalse(can_transition("Duplikat", "Menunggu Penilaian"))

    def test_valid_final(self):
        self.assertFalse(can_transition("Valid", "Siap Submit"))
        self.assertFalse(can_transition("Valid", "Ditolak"))

    def test_transisi_tidak_sah(self):
        self.assertFalse(can_transition("Siap Submit", "Valid"))
        self.assertFalse(can_transition("Bogus", "Valid"))
        self.assertFalse(can_transition("Siap Submit", "Bogus"))


if __name__ == "__main__":
    unittest.main()
