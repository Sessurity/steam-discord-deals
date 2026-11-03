import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from discord_commands import parse_command, build_reply, build_replies, process_messages, game_card

class CommandTests(unittest.TestCase):
    def setUp(self):
        self.game = {"appid": 123, "name": "Dying Light", "discount": 80,
                     "price": "50 грн", "old_price": "250 грн",
                     "reviews": 500, "positive": 95}

    def test_parse(self):
        self.assertEqual(parse_command("!deals 90"), ("deals", 90))
        self.assertEqual(parse_command("!spore"), ("spore", None))
        self.assertEqual(parse_command("!find Dying Light"), ("find", "Dying Light"))
        self.assertIsNone(parse_command("hello"))
        self.assertEqual(parse_command("!deals 60")[0], "error")

    def test_rub_prices_in_commands(self):
        game = dict(self.game, final=5000)
        pages = build_replies(("deals", 70), discounted=[game],
                              ru_prices={123: "399,00 ₽"})
        self.assertIn("399,00 ₽", pages[0]["embeds"][0]["description"])

    def test_results(self):
        answer = build_reply(("deals", 80), [self.game])
        self.assertIn("Dying Light", answer["description"])
        self.assertIn("50 грн", answer["description"])
        self.assertNotIn("Dying Light", build_reply(("deals", 90), [self.game])["description"])
        self.assertIn("Dying Light", build_reply(("find", "dying"), [self.game])["description"])

    def test_thirty_deals_return_three_messages_with_large_image_cards(self):
        games = [dict(self.game, appid=i + 1000, name=f"Game {i:02d}",
                      discount=80) for i in range(35)]
        pages = build_replies(("deals", 80), discounted=games)
        self.assertEqual(len(pages), 3)
        self.assertEqual([len(page["embeds"]) for page in pages], [10, 10, 10])
        self.assertIn("із 30", pages[-1]["content"])
        cards = [card for page in pages for card in page["embeds"]]
        self.assertIn("Game 29", str(cards))
        self.assertNotIn("Game 30", str(cards))
        self.assertTrue(all("image" in card for card in cards))
        self.assertTrue(all("header.jpg" in card["image"]["url"] for card in cards))
        self.assertTrue(all(len(page["embeds"]) <= 10 for page in pages))
        self.assertTrue(all(sum(len(e["title"]) + len(e["description"])
                                for e in page["embeds"]) <= 6000 for page in pages))

    def test_sorted_by_discount_then_reviews_not_popularity_first(self):
        games = [
            dict(self.game, appid=1, name="Lower", discount=70, reviews=100000),
            dict(self.game, appid=2, name="Highest", discount=95, reviews=1),
            dict(self.game, appid=3, name="Tie fewer", discount=90, reviews=5),
            dict(self.game, appid=4, name="Tie more", discount=90, reviews=10000),
        ]
        cards = build_replies(("deals", 70), discounted=games)[0]["embeds"]
        self.assertEqual([c["url"].split("/app/")[1].split("/")[0] for c in cards],
                         ["2", "4", "3", "1"])

    def test_deals_prioritize_popularity_but_preserve_percent_order(self):
        popular = [dict(self.game, appid=i + 1000, name=f"Popular {i}",
                        reviews=20000+i*1000, discount=75+i % 5)
                   for i in range(30)]
        obscure = [dict(self.game, appid=i + 2000, name=f"Unknown {i}",
                        reviews=5, discount=99) for i in range(35)]
        pages = build_replies(("deals", 70), discounted=obscure + popular)
        cards = [card for page in pages for card in page["embeds"]]
        self.assertEqual(len(cards), 30)
        self.assertTrue(all("Popular" in card["title"] for card in cards))
        percentages = [int(c["description"].split("−", 1)[1].split("%")[0])
                       for c in cards]
        self.assertEqual(percentages, sorted(percentages, reverse=True))

    def test_filtered_deals_90(self):
        games = [dict(self.game, appid=i + 2000, name=f"Game {i}",
                      discount=95 if i < 12 else 80) for i in range(35)]
        pages = build_replies(("deals", 90), discounted=games)
        self.assertEqual(len(pages), 2)
        self.assertEqual([len(p["embeds"]) for p in pages], [10, 2])

    def test_find_uses_same_image_card_design(self):
        pages = build_replies(("find", "dying"), discounted=[self.game])
        self.assertEqual(len(pages), 1)
        self.assertEqual(len(pages[0]["embeds"]), 1)
        self.assertIn("Dying Light", pages[0]["embeds"][0]["title"])
        self.assertIn("header.jpg", pages[0]["embeds"][0]["image"]["url"])

    def test_process_one_command_sends_three_replies(self):
        games = [dict(self.game, appid=i + 1000, name=f"Game {i}") for i in range(30)]
        sent = []
        messages = [{"id": "200", "content": "!deals", "author": {"id": "42"}}]
        with patch("discord_commands.query_steam_sales", return_value=games), patch("discord_commands.get_ru_prices", return_value={}):
            cursor, count = process_messages(messages, "199", "token", "77",
                                             send=lambda mid, answer: sent.append((mid, answer)))
        self.assertEqual((cursor, count), ("200", 1))
        self.assertEqual(len(sent), 3)
        self.assertTrue(all(mid == "200" for mid, _ in sent))
        self.assertTrue(all(len(answer["embeds"]) == 10 for _, answer in sent))

    def test_spore(self):
        with patch("discord_commands.get_spore_price", return_value={
            "available": True, "discount": 0, "price": "699 грн"
        }):
            answer = build_reply(("spore", None))
        self.assertEqual(answer["color"], 0xD6AF61)
        self.assertIn("699 грн", answer["description"])

    def test_poll_ignores_bot_and_old_messages(self):
        now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
        def msg(num, content, minutes=1, bot=False):
            return {"id": str(num), "content": content,
                    "timestamp": (now - timedelta(minutes=minutes)).isoformat(),
                    "author": {"id": "99", "bot": bot}}
        incoming = [msg(100, "!deals", 200), msg(101, "!spore", 1, True), msg(102, "!help")]
        sent = []
        last, count = process_messages(incoming, None, "token", "42", now=now,
                                       send=lambda mid, embed: sent.append(mid))
        self.assertEqual((last, count, sent), ("102", 1, ["102"]))

    def test_cursor_and_allowlist(self):
        incoming = [{"id": "200", "content": "!help", "author": {"id": "12"}},
                    {"id": "201", "content": "!help", "author": {"id": "42"}}]
        sent = []
        last, count = process_messages(incoming, "200", "token", "99", {"42"},
                                       send=lambda mid, embed: sent.append(mid))
        self.assertEqual((last, count, sent), ("201", 1, ["201"]))

if __name__ == "__main__":
    unittest.main()
