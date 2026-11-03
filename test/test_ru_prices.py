import unittest
from unittest.mock import patch

from steam_deals import fmt_rub, parse_ru_price, fetch_ru_prices, ru_line


class RuRegionalPriceTests(unittest.TestCase):
    def test_only_genuine_rub_currency_is_accepted(self):
        item = {
            "success": True,
            "data": {"price_overview": {
                "currency": "RUB", "final": 39900, "initial": 99900,
                "discount_percent": 60, "final_formatted": "399 ₽",
                "initial_formatted": "999 ₽",
            }},
        }
        price = parse_ru_price(item)
        self.assertEqual(price["price"], "399 ₽")
        self.assertEqual(price["old_price"], "999 ₽")
        self.assertIn("−60%", ru_line(price))
        self.assertIsNone(parse_ru_price({"success": True,
                                         "data": {"price_overview": {"currency": "USD"}}}))
        self.assertIsNone(parse_ru_price({"success": False, "data": {}}))
        self.assertIsNone(parse_ru_price(None))

    def test_format_rubles_from_verified_currency_cents(self):
        self.assertEqual(fmt_rub(10500), "105 ₽")
        self.assertEqual(fmt_rub(543299), "5 432,99 ₽")
        self.assertIsNone(fmt_rub(-1))
        self.assertIn("Ціну Steam RU не отримано", ru_line(None))

    def test_fetch_31_prices_in_four_10_appid_batches(self):
        calls = []
        def fake_get(url, params, retries=4):
            calls.append(params)
            return {
                appid: {
                    "success": True,
                    "data": {"price_overview": {
                        "currency": "RUB",
                        "initial": 100000, "final": 25000, "discount_percent": 75,
                    }},
                } for appid in params["appids"].split(",")
            }
        apps = list(range(1000, 1031))
        with patch("steam_deals.get_json", side_effect=fake_get):
            result = fetch_ru_prices(apps)
        self.assertEqual(len(calls), 4)
        self.assertEqual([len(x["appids"].split(",")) for x in calls], [10, 10, 10, 1])
        self.assertEqual(set(result.keys()), {str(x) for x in apps})
        self.assertTrue(all(x["cc"] == "ru" for x in calls))
        self.assertTrue(all(x["filters"] == "price_overview" for x in calls))

    def test_unavailable_region_returns_no_fabricated_price(self):
        payload = {
            "17390": {
                "success": True,
                "data": {"price_overview": {
                    "currency": "EUR", "final": 500, "initial": 2000,
                    "discount_percent": 75,
                }},
            }
        }
        with patch("steam_deals.get_json", return_value=payload):
            result = fetch_ru_prices([17390])
        self.assertEqual(result, {})
        self.assertIn("не отримано", ru_line(result.get("17390")))

    def test_network_failure_does_not_block_ua_report(self):
        with patch("steam_deals.get_json", side_effect=RuntimeError("temporarily unavailable")):
            result = fetch_ru_prices([17390, 239140])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
