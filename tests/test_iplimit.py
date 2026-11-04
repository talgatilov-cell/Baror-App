import threading
import unittest
import tracemalloc

from baror.auth.iplimit import IpLimiter, client_ip, normalize_ip


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


class IpLimitTests(unittest.TestCase):
    def setUp(self):
        self.c = Clock()
        self.l = IpLimiter(clock=self.c)

    def test_platoon_typos_not_locked(self):
        for i in range(25):
            self.l.record_login_failure("1.2.3.4", "u%d@x.il" % i)
        self.assertEqual(self.l.check_login("1.2.3.4", "new@x.il"), 0.0)

    def test_login_limit_and_exponential_cooldown(self):
        for _ in range(31):
            self.l.record_login_failure("1.2.3.4")
        w1 = self.l.check_login("1.2.3.4")
        self.assertEqual(w1, 60.0)
        self.c.t += 61
        self.assertEqual(self.l.check_login("1.2.3.4"), 0.0)
        for _ in range(31):
            self.l.record_login_failure("1.2.3.4")
        self.assertEqual(self.l.check_login("1.2.3.4"), 120.0)

    def test_strikes_decay_after_a_clean_day(self):
        for _ in range(31):
            self.l.record_login_failure("1.2.3.4")
        self.assertEqual(self.l.check_login("1.2.3.4"), 60.0)
        self.c.t += 61
        for _ in range(31):
            self.l.record_login_failure("1.2.3.4")
        self.assertEqual(self.l.check_login("1.2.3.4"), 120.0)      # second strike doubles
        self.c.t += 121 + 86400 + 1                                 # block over + a clean day
        for _ in range(31):
            self.l.record_login_failure("1.2.3.4")
        self.assertEqual(self.l.check_login("1.2.3.4"), 60.0)      # back to the first-strike cool-down

    def test_pair_limit(self):
        for _ in range(9):
            self.l.record_login_failure("1.2.3.4", "a@x.il")
        self.assertGreater(self.l.check_login("1.2.3.4", "A@x.il"), 0)
        self.assertEqual(self.l.check_login("1.2.3.4", "b@x.il"), 0.0)

    def test_register_limit(self):
        for _ in range(60):
            self.l.record_register_attempt("5.5.5.5")
        self.assertEqual(self.l.check_register("5.5.5.5"), 0.0, "60 per hour are allowed")
        self.l.record_register_attempt("5.5.5.5")
        self.assertEqual(self.l.check_register("5.5.5.5"), 60.0)

    def test_the_app_wide_register_limit_is_one_shared_counter_of_600_per_hour(self):
        for i in range(600):
            self.l.record_register_app()
        self.assertEqual(self.l.check_register_app(), 0.0)
        self.l.record_register_app()
        self.assertEqual(self.l.check_register_app(), 60.0)
        self.assertEqual(self.l.check_register("9.9.9.9"), 0.0, "the per-address counters are separate")
        self.c.t += 61
        self.assertEqual(self.l.check_register_app(), 0.0, "the cool-down ends")

    def test_the_app_wide_counter_is_a_single_bounded_entry(self):
        for _ in range(50):
            self.l.record_register_app()
        self.assertEqual(self.l.size(), 1)

    def test_window_slides(self):
        for _ in range(25):
            self.l.record_login_failure("1.2.3.4")
        self.c.t += 601
        for _ in range(25):
            self.l.record_login_failure("1.2.3.4")
        self.assertEqual(self.l.check_login("1.2.3.4"), 0.0)

    def test_fairness_one_ip_never_limits_another(self):
        for _ in range(100):
            self.l.record_login_failure("1.1.1.1", "v@x.il")
            self.l.record_register_attempt("1.1.1.1")
        self.assertGreater(self.l.check_login("1.1.1.1"), 0)
        self.assertEqual(self.l.check_login("2.2.2.2", "v@x.il"), 0.0)
        self.assertEqual(self.l.check_register("2.2.2.2"), 0.0)

    def test_success_clears_pair_only(self):
        for _ in range(6):
            self.l.record_login_failure("1.2.3.4", "a@x.il")
        self.l.record_login_success("1.2.3.4", "a@x.il")
        for _ in range(6):
            self.l.record_login_failure("1.2.3.4", "a@x.il")
        self.assertEqual(self.l.check_login("1.2.3.4", "a@x.il"), 0.0)  # pair reset: 6+6 never exceeded 8 at once
        # IP-wide counter is NOT reset by success: 12 failures remain, 19 more cross the 30 limit
        for _ in range(19):
            self.l.record_login_failure("1.2.3.4")
        self.assertGreater(self.l.check_login("1.2.3.4"), 0)

    def test_ipv6_64_bucketing(self):
        self.assertEqual(normalize_ip("2001:db8:1:2:aaaa::1"), normalize_ip("2001:db8:1:2:bbbb::9"))
        self.assertNotEqual(normalize_ip("2001:db8:1:2::1"), normalize_ip("2001:db8:1:3::1"))
        for i in range(31):
            self.l.record_login_failure("2001:db8:1:2:%x::1" % i)
        self.assertGreater(self.l.check_login("2001:db8:1:2:ffff::5"), 0)

    def test_bounded_memory_100k_ips(self):
        l = IpLimiter(clock=self.c, max_keys=5000)
        for i in range(100_000):
            l.record_login_failure("%d.%d.%d.%d" % (10, i >> 16 & 255, i >> 8 & 255, i & 255))
        self.assertLessEqual(l.size(), 5000)

    def test_default_cap_100k(self):
        for i in range(100_000):
            self.l.record_login_failure("%d.%d.%d.%d" % (10, i >> 16 & 255, i >> 8 & 255, i & 255))
        self.assertLessEqual(self.l.size(), 50_000)

    def test_thread_safe(self):
        def work():
            for _ in range(500):
                self.l.record_login_failure("9.9.9.9")
                self.l.check_login("9.9.9.9")
        ts = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertGreater(self.l.check_login("9.9.9.9"), 0)

    def test_client_ip_untrusted_peer_ignores_xff(self):
        self.assertEqual(client_ip({"X-Forwarded-For": "6.6.6.6"}, "8.8.8.8", ["10.0.0.1"]), "8.8.8.8")

    def test_client_ip_rightmost_untrusted(self):
        h = {"x-forwarded-for": "6.6.6.6, 3.3.3.3, 10.0.0.2"}
        self.assertEqual(client_ip(h, "10.0.0.1", ["10.0.0.1", "10.0.0.2"]), "3.3.3.3")

    def test_client_ip_no_header_or_garbage(self):
        self.assertEqual(client_ip({}, "10.0.0.1", ["10.0.0.1"]), "10.0.0.1")
        self.assertEqual(client_ip({"X-Forwarded-For": "1.1.1.1, junk"}, "10.0.0.1", ["10.0.0.1"]), "10.0.0.1")
        self.assertEqual(client_ip({}, "nonsense", []), "invalid")


if __name__ == "__main__":
    unittest.main()
