import io
import json
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from steam_deals import (
    bot_send,
    clear_pending,
    main,
    parse_game,
    report_snapshot,
    resolve_run_mode,
)


class FakeResponse(io.BytesIO):
    status = 200


class UnifiedBotTests(unittest.TestCase):
    def setUp(self):
        self.raw_game = {
            "type": 0, "id": 123, "name": "Test Game",
            "best_purchase_option": {
                "discount_pct": 80,
                "final_price_in_cents": "3000",
                "original_price_in_cents": "15000",
            },
        }
        self.game = parse_game(self.raw_game)
        self.spore = {
            "available": True, "discount": 0, "final": 69900,
            "price": "699 грн", "old_price": "699 грн",
        }
        self.env = {
            "DISCORD_BOT_TOKEN": "TEST_TOKEN",
            "DISCORD_CHANNEL_ID": "42",
            "RUN_MODE": "daily",
            "FORCE_SEND": "1",
            "SCHEDULED_RUN": "0",
        }

    def test_bot_api_sends_as_bot_and_removes_webhook_username(self):
        payload = {"username": "Legacy Webhook", "avatar_url": "https://example.com/a.png",
                   "embeds": [{"title": "ZHAXO"}],
                   "allowed_mentions": {"parse": ["everyone"]}}
        with patch("steam_deals.urlopen", return_value=FakeResponse(b'{"id":"123456"}')) as call:
            result = bot_send("TOKEN", "42", payload)
        self.assertEqual(result, "123456")
        request = call.call_args.args[0]
        self.assertEqual(request.full_url,
                         "https://discord.com/api/v10/channels/42/messages")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bot TOKEN")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("username", sent)
        self.assertNotIn("avatar_url", sent)
        self.assertEqual(sent["allowed_mentions"], {"parse": []})
        self.assertEqual(sent["embeds"][0]["title"], "ZHAXO")

    def test_no_fallback_to_webhook_when_bot_token_missing(self):
        env = {
            "RUN_MODE": "daily",
            "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/123/secret",
        }
        with patch.dict("os.environ", env, clear=True), \
             patch("steam_deals.load_state") as state, \
             patch("steam_deals.send_discord") as webhook_send:
            with self.assertRaisesRegex(RuntimeError, "DISCORD_BOT_TOKEN"):
                main()
            state.assert_not_called()
            webhook_send.assert_not_called()

    def test_cleanup_edits_bot_owned_report_without_webhook(self):
        snapshot = report_snapshot([self.game], self.spore)
        state = {"last_post_date": "2026-11-03", "report": {
            "transport": "bot", "message_ids": ["1001"], "groups": [[123]],
            "date": "03.11.2026", "initial_count": 1, "snapshot": snapshot,
        }}
        env = dict(self.env, RUN_MODE="cleanup")
        with patch.dict("os.environ", env, clear=True), \
             patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=[self.game]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_edit") as bot_edit, \
             patch("steam_deals.edit_discord") as webhook_edit, \
             patch("steam_deals.save_state") as save:
            main()
        bot_edit.assert_called_once()
        self.assertEqual(bot_edit.call_args.args[:3], ("TEST_TOKEN", "42", "1001"))
        webhook_edit.assert_not_called()
        self.assertIn("last_checked_at", save.call_args.args[0]["report"])

    def test_expired_bot_page_is_deleted_by_bot(self):
        other = dict(self.game, appid=456, name="Expired")
        report = {
            "transport": "bot", "message_ids": ["1001", "1002"],
            "groups": [[123], [456]], "initial_count": 2,
            "date": "03.11.2026",
            "snapshot": report_snapshot([self.game, other], self.spore),
        }
        state = {"report": report, "last_post_date": "2026-11-03"}
        env = dict(self.env, RUN_MODE="cleanup")
        with patch.dict("os.environ", env, clear=True), \
             patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=[self.game]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_edit") as bot_edit, \
             patch("steam_deals.bot_delete") as bot_delete, \
             patch("steam_deals.delete_discord") as webhook_delete, \
             patch("steam_deals.save_state") as save:
            main()
        bot_edit.assert_called_once()
        bot_delete.assert_called_once_with("TEST_TOKEN", "42", "1002",
                                           ignore_missing=True)
        webhook_delete.assert_not_called()
        self.assertEqual(save.call_args.args[0]["report"]["message_ids"], ["1001"])

    def test_scheduled_cleanup_migrates_webhook_even_if_today_reported(self):
        now = datetime(2026, 11, 3, 9, 0, tzinfo=ZoneInfo("Europe/Kyiv"))
        old = {"last_post_date": "2026-11-03", "report": {
            "message_ids": ["1"], "groups": [[123]],
        }}
        self.assertEqual(resolve_run_mode("cleanup", old, now, scheduled=True), "daily")
        self.assertEqual(resolve_run_mode("cleanup", old, now, scheduled=False), "cleanup")
        new = {"last_post_date": "2026-11-03", "report": {
            "transport": "bot", "message_ids": ["2"], "groups": [[123]],
        }}
        self.assertEqual(resolve_run_mode("cleanup", new, now, scheduled=True), "cleanup")

    def test_failed_webhook_delete_keeps_pending_for_retry(self):
        state = {"pending_message_batches": [
            {"transport": "webhook", "ids": ["11", "12"]},
        ]}
        with patch("steam_deals.delete_discord",
                   side_effect=[RuntimeError("Discord HTTP 403"), None]) as delete:
            changed = clear_pending(
                state, "TEST_TOKEN", "42",
                "https://discord.com/api/webhooks/123/secret",
            )
        self.assertTrue(changed)
        self.assertEqual(delete.call_count, 2)
        self.assertEqual(state["pending_message_batches"], [
            {"transport": "webhook", "ids": ["11"]},
        ])
        with patch("steam_deals.delete_discord"):
            self.assertTrue(clear_pending(
                state, "TEST_TOKEN", "42",
                "https://discord.com/api/webhooks/123/secret",
            ))
        self.assertNotIn("pending_message_batches", state)

    def test_daily_rollover_deletes_old_bot_report_not_webhook(self):
        old_state = {"last_post_date": "2026-11-02", "report": {
            "transport": "bot", "message_ids": ["777"], "groups": [[123]],
        }}
        with patch.dict("os.environ", self.env, clear=True), \
             patch("steam_deals.load_state", return_value=old_state), \
             patch("steam_deals.query_steam_sales", return_value=[self.game]), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_send", return_value="888") as send, \
             patch("steam_deals.bot_delete") as delete, \
             patch("steam_deals.delete_discord") as webhook_delete, \
             patch("steam_deals.save_state") as save:
            main()
        send.assert_called_once()
        delete.assert_called_once_with("TEST_TOKEN", "42", "777", ignore_missing=True)
        webhook_delete.assert_not_called()
        self.assertEqual(save.call_args.args[0]["report"]["transport"], "bot")
        self.assertNotIn("pending_message_batches", save.call_args.args[0])

    def test_partial_bot_post_is_rolled_back(self):
        games = [dict(self.game, appid=1000 + i) for i in range(11)]
        state = {"report": {}, "deals": {}, "spore": self.spore}
        with patch.dict("os.environ", self.env, clear=True), \
             patch("steam_deals.load_state", return_value=state), \
             patch("steam_deals.query_steam_sales", return_value=games), \
             patch("steam_deals.checked_spore", return_value=self.spore), \
             patch("steam_deals.bot_send",
                   side_effect=["1234", RuntimeError("Discord HTTP 500")]), \
             patch("steam_deals.bot_delete") as delete, \
             patch("steam_deals.save_state") as save:
            with self.assertRaisesRegex(RuntimeError, "Discord HTTP 500"):
                main()
        delete.assert_called_once_with("TEST_TOKEN", "42", "1234",
                                       ignore_missing=True)
        save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
