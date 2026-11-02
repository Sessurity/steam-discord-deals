import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from steam_deals import parse_game, make_payload, fmt_money, report_snapshot, main


class SteamDealsTests(unittest.TestCase):
    def setUp(self):
        self.game = {
            'type': 0,
            'visible': True,
            'id': 123,
            'name': 'Test Game',
            'best_purchase_option': {
                'discount_pct': 75,
                'final_price_in_cents': '2500',
                'original_price_in_cents': '10000',
                'formatted_final_price': '25 ₴',
                'formatted_original_price': '100 ₴',
                'active_discounts': [],
            },
            'reviews': {'summary_filtered': {'review_count': 8000, 'percent_positive': 90}},
        }

    def test_filters_only_games_from_70(self):
        self.assertEqual(parse_game(self.game)['discount'], 75)
        self.game['type'] = 4
        self.assertIsNone(parse_game(self.game))
        self.game['type'] = 0
        self.game['best_purchase_option']['discount_pct'] = 69
        self.assertIsNone(parse_game(self.game))

    def test_price_ua(self):
        self.assertEqual(fmt_money(2500, '25 ₴'), '25 ₴')
        self.assertEqual(fmt_money(2500, None), '25,00 грн')

    def test_discord_payload_spore_first(self):
        now = datetime(2026, 11, 2, 8, tzinfo=ZoneInfo('Europe/Kyiv'))
        deal = parse_game(self.game)
        spore = {'available': True, 'discount': 0, 'final': 69900, 'price': '699 ₴', 'old_price': '699 ₴'}
        payload = make_payload([deal], spore, {}, now)
        self.assertEqual(payload['embeds'][0]['title'], '⭐ SPORE — головний пріоритет')
        self.assertIn('без знижки', payload['embeds'][0]['description'])
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        self.assertIn('−75%', payload['embeds'][1]['description'])

    def test_expired_discount_disappears_without_replacement(self):
        now = datetime(2026, 11, 2, 11, tzinfo=ZoneInfo('Europe/Kyiv'))
        deal = parse_game(self.game)
        spore = {'available': True, 'discount': 0, 'final': 69900,
                 'price': '699 ₴', 'old_price': '699 ₴'}
        # 456 вже не має знижки: не показується, навіть якщо був у вихідній добірці.
        payload = make_payload([deal], spore, {}, now,
                               selected_ids=[123, 456], flags={'123': '🆕 '},
                               report_date='02.11.2026', total_deals=800)
        description = payload['embeds'][-1]['description']
        self.assertIn('Test Game', description)
        self.assertNotIn('456', description)
        self.assertIn('🆕', description)

    def test_cleanup_drops_expired_and_keeps_spore(self):
        deal = parse_game(self.game)
        other = dict(deal, appid=789, name='New Game')
        spore = {'available': True, 'discount': 0, 'final': 69900,
                 'price': '699 ₴', 'old_price': '699 ₴'}
        prior = {'last_post_date': '2026-11-02', 'spore': spore,
                 'deals': {},
                 'report': {'message_id': '123456789012345678',
                            'date': '02.11.2026',
                            'active_appids': [123, 456],
                            'flags': {},
                            'spore_alert': False,
                            'snapshot': {'games': {'123': [75, 2500, 10000, None],
                                                   '456': [75, 1000, 4000, None]},
                                         'spore': report_snapshot([], spore)['spore']}}}
        with patch.dict('os.environ', {
            'DISCORD_WEBHOOK_URL': 'https://discord.com/api/webhooks/123/secret',
            'RUN_MODE': 'cleanup',
        }), patch('steam_deals.load_state', return_value=prior), \\
             patch('steam_deals.query_steam_sales', return_value=[deal, other]), \\
             patch('steam_deals.checked_spore', return_value=spore), \\
             patch('steam_deals.edit_discord') as edit, \\
             patch('steam_deals.save_state') as save:
            main()
            edit.assert_called_once()
            payload = edit.call_args.args[2]
            desc = payload['embeds'][-1]['description']
            self.assertIn('Test Game', desc)
            self.assertNotIn('New Game', desc)
            self.assertNotIn('456', desc)
            self.assertEqual(save.call_args.args[0]['report']['active_appids'], [123])


if __name__ == '__main__':
    unittest.main()