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
    select_report_games,
    rub_price,
    select_popular_deals,
    resolve_run_mode,
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

    def test_rub_conversion_is_approximate(self):
        self.assertEqual(rub_price(2500, 2.5), "≈ 62,50 ₽")
        self.assertIsNone(rub_price(2500, None))
        deal = parse_game(self.game)
        pages = make_payload_pages([[123]], [deal], self.spore, {}, self.now, rub_rate=2.5)
        self.assertIn("≈ 62,50 ₽", pages[0]["embeds"][2]["description"])
        self.assertIn("≈ 1 747,50 ₽", pages[0]["embeds"][1]["description"])

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

    def test_thirty_games_fit_discord_limits(self):
        games = [dict(parse_game(self.game), appid=1000 + i, name="Game " + str(i)) for i in range(30)]
        groups = chunk_game_ids(games)
        pages = make_payload_pages(groups, games, self.spore, {}, self.now)
        self.assertEqual(list(map(len, groups)), [8, 10, 10, 2])
        self.assertEqual([len(page["embeds"]) for page in pages], [10, 10, 10, 2])
        self.assertEqual(sum(len(group) for group in groups), 30)
        self.assertTrue(all(len(page["embeds"]) <= 10 for page in pages))
        for page in pages:
            self.assertLessEqual(sum(len(embed.get("title", "")) +
                                    len(embed.get("description", "")) for embed in page["embeds"]), 6000)

    def test_daily_sorted_by_discount_then_popularity(self):
        base = parse_game(self.game)
        games = [
            dict(base, appid=101, name="Low", discount=70, reviews=50000),
            dict(base, appid=102, name="High", discount=95, reviews=10),
            dict(base, appid=103, name="Tie popular", discount=90, reviews=9000),
            dict(base, appid=104, name="Tie less popular", discount=90, reviews=100),
        ]
        picked = select_report_games(games, {})
        self.assertEqual([g["appid"] for g in picked], [102, 103, 104, 101])

    def test_popular_games_beat_obscure_99_percent_deals(self):
        base = parse_game(self.game)
        well_known = [
            dict(base, appid=1000+i, name=f"Known {i:02d}",
                 discount=70+(i % 5), reviews=20000+i*100,
                 positive=90)
            for i in range(30)
        ]
        obscure = [
            dict(base, appid=2000+i, name=f"Unknown {i:02d}",
                 discount=99, reviews=25, positive=99)
            for i in range(60)
        ]
        selected = select_report_games(obscure + well_known, {})
        self.assertEqual(len(selected), 30)
        self.assertTrue(all(game["appid"] < 2000 for game in selected))
        self.assertEqual([game["discount"] for game in selected],
                         sorted([game["discount"] for game in selected], reverse=True))

    def test_less_known_games_fill_shortages_and_popularity_matters(self):
        base = parse_game(self.game)
        popular = [
            dict(base, appid=1, name="Popular One", discount=70,
                 reviews=25000, positive=93),
            dict(base, appid=2, name="Popular Two", discount=80,
                 reviews=6000, positive=85),
        ]
        unknown = [
            dict(base, appid=3, name="Obscure", discount=99,
                 reviews=10, positive=99),
            dict(base, appid=4, name="Less known", discount=90,
                 reviews=1400, positive=90),
        ]
        selected = select_popular_deals(unknown + popular, 3)
        self.assertEqual({game["appid"] for game in selected}, {1, 2, 4})
        self.assertEqual([game["appid"] for game in selected], [4, 2, 1])
        self.assertEqual(select_popular_deals(unknown, 0), [])

    def test_scheduled_cleanup_catches_up_missed_morning_report(self):
        new_day = datetime(2026, 11, 3, 8, 33, tzinfo=ZoneInfo("Europe/Kyiv"))
        old_state = {"last_post_date": "2026-11-02"}
        self.assertEqual(resolve_run_mode("cleanup", old_state, new_day, True), "daily")
        self.assertEqual(resolve_run_mode("cleanup", old_state, new_day, False), "cleanup")
        self.assertEqual(resolve_run_mode("cleanup",
                                          {"last_post_date": "2026-11-03"}, new_day, True), "cleanup")
        before_eight = datetime(2026, 11, 3, 7, 59, tzinfo=ZoneInfo("Europe/Kyiv"))
        self.assertEqual(resolve_run_mode("cleanup", old_state, before_eight, True), "cleanup")

    def test_unchanged_discount_still_refreshes_last_checked(self):
        game = parse_game(self.game)
        snapshot = report_snapshot([game], self.spore)
        previous = {
            "report": {
                "message_ids": ["1001"], "groups": [[123]],
                "date": "02.11.2026", "initial_count": 1,
                "snapshot": snapshot,
            },
            "last_post_date": "2026-11-02",
        }
        with patch.dict("os.environ", {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
            "DISCORD_BOT_TOKEN": "fake_token_for_tests",
            "DISCORD_CHANNEL_ID": "42",
            "RUN_MODE": "cleanup",
            "SCHEDULED_RUN": "0",
        }), patch("steam_deals.get_rub_rate", return_value=2.5), patch("steam_deals.load_state", return_value=previous), \
             patch("steam_deals.query_steam_sales", return_value=[game]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.edit_discord") as edit, \
             patch("steam_deals.delete_discord") as delete, \
             patch("steam_deals.save_state") as save:
            main()
            edit.assert_called_once()
            self.assertEqual(edit.call_args.args[1], "1001")
            self.assertIn("Перевірено:", edit.call_args.args[2]["embeds"][0]["description"])
            delete.assert_not_called()
            stored = save.call_args.args[0]
            self.assertIn("last_checked_at", stored["report"])
            self.assertEqual(stored["report"]["message_ids"], ["1001"])

    def test_scheduled_catchup_publishes_new_report_once(self):
        game = parse_game(self.game)
        old_state = {
            "last_post_date": "2026-11-02",
            "report": {"message_ids": ["999"], "groups": [[123]]},
            "deals": {}, "spore": self.spore,
        }
        today = datetime(2026, 11, 3, 8, 33, tzinfo=ZoneInfo("Europe/Kyiv"))
        with patch.dict("os.environ", {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
            "DISCORD_BOT_TOKEN": "fake_token_for_tests",
            "DISCORD_CHANNEL_ID": "42",
            "RUN_MODE": "cleanup", "SCHEDULED_RUN": "1", "FORCE_SEND": "0",
        }), patch("steam_deals.datetime", wraps=datetime) as clock, \
             patch("steam_deals.get_rub_rate", return_value=2.5), patch("steam_deals.load_state", return_value=old_state), \
             patch("steam_deals.query_steam_sales", return_value=[game]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_send", return_value="101") as send, \
             patch("steam_deals.delete_discord") as delete, \
             patch("steam_deals.save_state") as save:
            clock.now.return_value = today
            main()
            send.assert_called_once()
            self.assertEqual(save.call_args.args[0]["last_post_date"], "2026-11-03")
            delete.assert_called_once_with(
                "https://discord.com/api/webhooks/123/secret", "999", ignore_missing=True
            )

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
            "DISCORD_BOT_TOKEN": "fake_token_for_tests",
            "DISCORD_CHANNEL_ID": "42",
            "RUN_MODE": "cleanup",
        }), patch("steam_deals.get_rub_rate", return_value=2.5), patch("steam_deals.load_state", return_value=state), \
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

    def test_daily_sends_4_pages_and_deletes_legacy_report(self):
        games = [dict(parse_game(self.game), appid=1000 + i) for i in range(30)]
        state = {"report": {"message_id": "99999"}, "deals": {}, "spore": self.spore}
        with patch.dict("os.environ", {
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
            "DISCORD_BOT_TOKEN": "fake_token_for_tests",
            "DISCORD_CHANNEL_ID": "42",
            "RUN_MODE": "daily", "FORCE_SEND": "1",
        }), patch("steam_deals.get_rub_rate", return_value=2.5), patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=games), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_send", side_effect=["101", "102", "103", "104"]) as send, \
             patch("steam_deals.delete_discord") as delete, \
             patch("steam_deals.save_state") as save:
            main()
            self.assertEqual(send.call_count, 4)
            self.assertEqual(save.call_args.args[0]["report"]["transport"], "bot")
            self.assertEqual(save.call_args.args[0]["report"]["message_ids"], ["101", "102", "103", "104"])
            delete.assert_called_once_with(
                "https://discord.com/api/webhooks/123/secret", "99999", ignore_missing=True
            )


if __name__ == "__main__":
    unittest.main()
