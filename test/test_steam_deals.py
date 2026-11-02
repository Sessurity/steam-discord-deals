import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from steam_deals import parse_game, make_payload, fmt_money


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


if __name__ == '__main__':
    unittest.main()