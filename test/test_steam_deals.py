import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from steam_deals import (
    chunk_game_ids,
    fmt_money,
    main,
    make_payload_pages,
    parse_game,
    report_snapshot,
)


class SteamDealsTests(unittest.TestCase):
    def setUp(self):
        self.game = {
            "type": 0, "visible": True, "id": 123, "name": "Test Game",
            "best_purchase_option": {
                "discount_pct": 75,
                "final_price_in_cents": "2500",
                "original_price_in_cents": "10000",
                "formatted_final_price": "25 ₴",
                "formatted_original_price": "100 ₴",
                "active_discounts": [],
            },
            "reviews": {"summary_filtered": {"review_count": 8000, "percent_positive": 90}},
        }
        self.spore = {
            "available": True, "discount": 0, "final": 69900,
            "price": "699 ₴", "old_price": "699 ₴",
        }
        self.now = datetime(2026, 11, 2, 8, tzinfo=ZoneInfo("Europe/Kyiv"))

    def test_filters_only_games_from_70(self):
        self.assertEqual(parse_game(self.game)["discount"], 75)
        self.game["type"] = 4
        self.assertIsNone(parse_game(self.game))
        self.game["type"] = 0
        self.game["best_purchase_option"]["discount_pct"] = 69
        self.assertIsNone(parse_game(self.game))

    def test_price_ua(self):
        self.assertEqual(fmt_money(2500, "25 ₴"), "25 ₴")
        self.assertEqual(fmt_money(2500, None), "25,00 грн")

    def test_hybrid_design_spore_header_and_individual_cover(self):
        deal = parse_game(self.game)
        pages = make_payload_pages([[123]], [deal], self.spore, {}, self.now)
        self.assertEqual(len(pages), 1)
        header, priority, card = pages[0]["embeds"]
        self.assertIn("ZHAXO", header["title"])
        self.assertEqual(header["color"], 0x39CFC6)
        self.assertIn("SPORE", priority["title"])
        self.assertEqual(priority["color"], 0xD6AF61)
        self.assertIn("17390/header.jpg", priority["thumbnail"]["url"])
        self.assertIn("123/header.jpg", card["thumbnail"]["url"])
        self.assertIn("−75%", card["description"])
        self.assertIn("25 ₴", card["description"])
        self.assertEqual(pages[0]["allowed_mentions"], {"parse": []})

    def test_twenty_games_fit_discord_limits(self):
        games = [dict(parse_game(self.game), appid=1000 + i, name="Game " + str(i)) for i in range(20)]
        groups = chunk_game_ids(games)
        pages = make_payload_pages(groups, games, self.spore, {}, self.now)
        self.assertEqual(list(map(len, groups)), [8, 10, 2])
        self.assertEqual([len(page["embeds"]) for page in pages], [10, 10, 2])
        self.assertEqual(sum(len(group) for group in groups), 20)
        self.assertTrue(all(len(page["embeds"]) <= 10 for page in pages))
        for page in pages:
            self.assertLessEqual(sum(len(embed.get("title", "")) +
                                    len(embed.get("description", "")) for embed in page["embeds"]), 6000)

    def test_expired_game_removed_from_its_original_page(self):
        deal = parse_game(self.game)
        groups = [[123, 456], [789]]
        pages = make_payload_pages(
            groups, [deal], self.spore, {}, self.now,
            initial_count=3, flags={"123": "🆕 "}
        )
        self.assertEqual(len(pages[0]["embeds"]), 3)
        self.assertIn("Test Game", pages[0]["embeds"][2]["title"])
        self.assertIn("🆕", pages[0]["embeds"][2]["title"])
        self.assertIsNone(pages[1])
        self.assertIn("1/3", pages[0]["embeds"][0]["description"])

    def test_cleanup_edits_and_deletes_empty_page(self):
        game = parse_game(self.game)
        irrelevant = dict(game, appid=900, name="Other current sale")
        old_groups = [[123, 456], [789]]
        report = {
            "message_ids": ["1111", "2222"], "groups": old_groups,
            "date": "02.11.2026", "initial_count": 3,
            "flags": {}, "spore_alert": False,
            "snapshot": {
                "games": {
                    "123": [75, 2500, 10000, None],
                    "456": [75, 2500, 10000, None],
                    "789": [75, 2500, 10000, None],
                },
                "spore": report_snapshot([], self.spore)["spore"],
            },
        }
        state = {"report": report, "deals": {}, "spore": self.spore}
        with patch.dict("os.environ", {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
            "RUN_MODE": "cleanup",
        }), patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=[game, irrelevant]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.edit_discord") as edit, \
             patch("steam_deals.delete_discord") as delete, \
             patch("steam_deals.save_state") as save:
            main()
            edit.assert_called_once()
            self.assertEqual(edit.call_args.args[1], "1111")
            card_text = str(edit.call_args.args[2]["embeds"])
            self.assertIn("Test Game", card_text)
            self.assertNotIn("Other current sale", card_text)
            delete.assert_called_once_with(
                "https://discord.com/api/webhooks/123/secret", "2222", ignore_missing=True
            )
            resulting = save.call_args.args[0]["report"]
            self.assertEqual(resulting["groups"], [[123]])
            self.assertEqual(resulting["message_ids"], ["1111"])

    def test_daily_sends_3_pages_and_deletes_legacy_report(self):
        games = [dict(parse_game(self.game), appid=1000 + i) for i in range(20)]
        state = {"report": {"message_id": "99999"}, "deals": {}, "spore": self.spore}
        with patch.dict("os.environ", {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
            "RUN_MODE": "daily", "FORCE_SEND": "1",
        }), patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=games), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.send_discord", side_effect=["101", "102", "103"]) as send, \
             patch("steam_deals.delete_discord") as delete, \
             patch("steam_deals.save_state") as save:
            main()
            self.assertEqual(send.call_count, 3)
            self.assertEqual(save.call_args.args[0]["report"]["message_ids"], ["101", "102", "103"])
            delete.assert_called_once_with(
                "https://discord.com/api/webhooks/123/secret", "99999", ignore_missing=True
            )


if __name__ == "__main__":
    unittest.main()
