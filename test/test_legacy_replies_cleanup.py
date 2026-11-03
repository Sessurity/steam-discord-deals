import unittest
from worker.cleanup_legacy_replies import legacy_slash_reply


class LegacyRepliesCleanupTests(unittest.TestCase):
    def setUp(self):
        self.bot_id = "444444444444444444"
        self.base = {
            "id": "100",
            "author": {"id": self.bot_id},
            "interaction_metadata": {"type": 2, "name": "deals"},
        }

    def test_old_deals_and_find_responses_are_selected(self):
        for heading in (
            "⬡ **ZHAXO / ЗНИЖКИ ВІД −70%**",
            "⬡ **ZHAXO / ПОШУК**",
        ):
            message = dict(self.base, content=heading)
            self.assertTrue(legacy_slash_reply(message, self.bot_id, set()))

    def test_spore_and_help_only_when_slash_interaction(self):
        for name in ("★ SPORE · головний пріоритет", "⬡ ZHAXO · Команди"):
            message = dict(self.base, embeds=[{"title": name}])
            self.assertTrue(legacy_slash_reply(message, self.bot_id, set()))
            self.assertFalse(legacy_slash_reply(
                dict(message, interaction_metadata=None),
                self.bot_id,
                set()
            ))

    def test_never_delete_daily_report_other_bot_or_protected_ids(self):
        report = dict(self.base, embeds=[{"title": "⬡ ZHAXO  //  STEAM DEALS"}])
        self.assertFalse(legacy_slash_reply(report, self.bot_id, set()))
        old_command = dict(self.base, content="⬡ **ZHAXO / ЗНИЖКИ ВІД −90%**")
        self.assertFalse(legacy_slash_reply(old_command, self.bot_id, {"100"}))
        self.assertFalse(legacy_slash_reply(
            dict(old_command, author={"id": "222222222222222222"}),
            self.bot_id,
            set()
        ))


if __name__ == "__main__":
    unittest.main()
