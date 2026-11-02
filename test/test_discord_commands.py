import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from discord_commands import parse_command, build_reply, process_messages

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

    def test_results(self):
        answer = build_reply(("deals", 80), [self.game])
        self.assertIn("Dying Light", answer["description"])
        self.assertIn("50 грн", answer["description"])
        self.assertNotIn("Dying Light", build_reply(("deals", 90), [self.game])["description"])
        self.assertIn("Dying Light", build_reply(("find", "dying"), [self.game])["description"])

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
