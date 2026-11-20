"""Evidence and lifecycle contracts for the conversation brief; no GUI or API."""
import unittest

from sentinel_briefing import ConversationBrief, validate_briefing


def claim(text, *quotes):
    return {'text': text, 'quotes': list(quotes)}


def brief(situation='付款期限仍在讨论', agreements=None, questions=None):
    return {'situation': situation, 'agreements': agreements or [],
            'open_questions': questions or [], 'background': '', 'background_quotes': [],
            'next_goal': '', 'next_goal_quotes': []}


class BriefValidationTests(unittest.TestCase):
    def test_legacy_briefing_is_accepted_without_inventing_sidebar_context(self):
        result = validate_briefing({'situation': '正在谈付款', 'agreements': [],
                                    'open_questions': []}, '原话')
        self.assertEqual(result['background'], '')
        self.assertEqual(result['next_goal'], '')
        self.assertEqual(result['background_quotes'], [])
        self.assertEqual(result['next_goal_quotes'], [])

    def test_context_requires_its_own_confirmed_quotes(self):
        raw = brief()
        raw.update(background='双方已经决定六十天付款', background_quotes=['同意六十天付款'],
                   next_goal='明确能否延长账期', next_goal_quotes=['希望延长到六十天'])
        result = validate_briefing(raw, '对方希望延长到六十天，我还没有同意。')
        self.assertEqual(result['background'], '')
        self.assertEqual(result['next_goal'], '明确能否延长账期')
        self.assertEqual(result['agreements'], [])

    def test_context_is_bounded_and_survives_observer_then_ui_revalidation(self):
        raw = brief()
        raw.update(background='背景' * 200, background_quotes=['先看样品', '先看样品'],
                   next_goal='目标' * 200, next_goal_quotes=['再谈付款'])
        confirmed = '先看样品，再谈付款。'
        first = validate_briefing(raw, confirmed)
        self.assertEqual(len(first['background']), 180)
        self.assertEqual(len(first['next_goal']), 180)
        self.assertEqual(first['background_quotes'], ['先看样品'])
        self.assertEqual(validate_briefing(first, confirmed), first)

    def test_malformed_optional_context_clears_fields_without_losing_valid_brief(self):
        for text, quotes in ((['无效文字'], ['原话']), ('背景', '原话'),
                             ('背景', []), ('背景', ['原' * 181])):
            with self.subTest(text=text, quotes=quotes):
                raw = brief('当前仍在协商')
                raw.update(background=text, background_quotes=quotes,
                           next_goal=text, next_goal_quotes=quotes)
                result = validate_briefing(raw, '原话' + '原' * 181)
                self.assertEqual(result['situation'], '当前仍在协商')
                self.assertEqual(result['background'], '')
                self.assertEqual(result['next_goal'], '')

    def test_fabricated_or_partial_only_quotes_cannot_support_claims(self):
        raw = brief(agreements=[claim('已达成六十天付款', '我们同意六十天付款')],
                    questions=[claim('核对原付款期限', '原先约定三十天付款')])
        result = validate_briefing(raw, '原先约定三十天付款，但还没有同意延长。')
        self.assertEqual(result['agreements'], [])
        self.assertEqual(result['open_questions'][0]['text'], '核对原付款期限')

    def test_invalid_structure_does_not_clear_existing_state(self):
        for raw in (None, [], {}, {'situation': 5, 'agreements': [], 'open_questions': []},
                    {'situation': '', 'agreements': 'none', 'open_questions': []}):
            with self.subTest(raw=raw):
                self.assertIsNone(validate_briefing(raw, '原话'))

    def test_limits_and_empty_lists(self):
        raw = brief('局势' * 200,
                    agreements=[claim(str(i) * 150, '原话') for i in range(9)],
                    questions=[claim('超长引文不可用', '原' * 181)])
        result = validate_briefing(raw, '原话' + '原' * 181)
        self.assertEqual(len(result['situation']), 220)
        self.assertEqual(len(result['agreements']), 4)
        self.assertTrue(all(len(item['text']) <= 120 for item in result['agreements']))
        self.assertEqual(result['open_questions'], [])
        self.assertEqual(validate_briefing(brief(''), ''), brief(''))


class ConversationBriefTests(unittest.TestCase):
    def setUp(self):
        self.model = ConversationBrief()

    def final(self, text, ident=1):
        return self.model.observe({'kind': 'final', 'turn_id': ident, 'text': text})

    def update(self, payload, revision=1, **fields):
        return self.model.observe(dict(kind='briefing', briefing=payload,
                                       revision=revision, **fields))

    def test_denial_or_proposal_does_not_automatically_become_agreement(self):
        self.final('对方提出六十天付款，但我们还没有同意。')
        self.model.observe({'kind': 'alert', 'summary': '对方提出延期，尚未同意', 'action': '核对期限'})
        state = self.model.snapshot()
        self.assertEqual(state['agreements'], [])
        self.assertEqual(state['open_questions'], [])
        self.assertEqual(state['situation'], '对方提出延期，尚未同意')
        self.assertEqual(state['action'], '核对期限')

    def test_partials_never_enter_evidence(self):
        self.final('付款条件下午再谈。')
        self.model.observe({'kind': 'partial', 'text': '同意预付款三成'})
        payload = brief(agreements=[claim('已同意预付款三成', '同意预付款三成')])
        payload.update(background='已同意预付款', background_quotes=['同意预付款三成'],
                       next_goal='安排付款', next_goal_quotes=['同意预付款三成'])
        self.update(payload)
        self.assertEqual(self.model.snapshot()['agreements'], [])
        self.assertEqual(self.model.snapshot()['background'], '')
        self.assertEqual(self.model.snapshot()['next_goal'], '')

    def test_verified_items_clear_after_role_or_text_corrections(self):
        for event in ({'kind': 'speaker_roles', 'speakers': {'A': '对方'}},
                      {'kind': 'attribution_update', 'turn_id': 1, 'role': '对方'},
                      {'kind': 'final', 'turn_id': 1, 'text': '并未确认先看样品。'}):
            with self.subTest(event=event):
                self.model.reset()
                self.final('双方确认先看样品。')
                payload = brief(agreements=[claim('先看样品', '双方确认先看样品')],
                                questions=[claim('核对样品安排', '先看样品')])
                payload.update(background='双方先检查样品', background_quotes=['先看样品'],
                               next_goal='明确样品检查安排', next_goal_quotes=['先看样品'])
                self.update(payload)
                self.assertEqual(len(self.model.snapshot()['agreements']), 1)
                self.assertTrue(self.model.snapshot()['background'])
                self.assertTrue(self.model.snapshot()['next_goal'])
                self.assertTrue(self.model.observe(event))
                state = self.model.snapshot()
                self.assertEqual(state['agreements'], [])
                self.assertEqual(state['open_questions'], [])
                self.assertEqual(state['background'], '')
                self.assertEqual(state['background_quotes'], [])
                self.assertEqual(state['next_goal'], '')
                self.assertEqual(state['next_goal_quotes'], [])
                self.assertTrue(state['provisional'])

    def test_newer_empty_sidebar_context_removes_old_fields_and_sources(self):
        self.final('先看样品，再谈付款。')
        payload = brief()
        payload.update(background='先讨论样品', background_quotes=['先看样品'],
                       next_goal='明确付款条件', next_goal_quotes=['再谈付款'])
        self.update(payload, revision=1)
        self.assertTrue(self.model.snapshot()['next_goal'])
        self.update(brief(), revision=2)
        state = self.model.snapshot()
        self.assertEqual(state['background'], '')
        self.assertEqual(state['next_goal'], '')
        self.assertFalse(self.update(payload, revision=1))
        self.assertEqual(self.model.snapshot()['next_goal'], '')

    def test_old_result_does_not_replace_newer_brief(self):
        self.update(brief('最新局势'), revision=3)
        self.assertFalse(self.update(brief('旧局势'), revision=2))
        self.assertFalse(self.model.observe({'kind': 'alert', 'revision': 1, 'summary': '旧提醒'}))
        self.assertEqual(self.model.snapshot()['situation'], '最新局势')
        self.assertEqual(self.model.snapshot()['revision'], 3)

    def test_corrected_claim_is_not_restored_by_the_same_analysis_revision(self):
        self.final('双方确认先看样品。')
        payload = brief(agreements=[claim('先看样品', '先看样品')])
        self.update(payload, revision=3)
        self.model.observe({'kind': 'speaker_roles', 'speakers': {'A': '对方'}})
        self.assertFalse(self.update(payload, revision=3))
        self.assertFalse(self.model.observe({'kind': 'alert', 'revision': 3,
                                            'summary': '旧归属', 'action': '旧建议'}))
        self.assertEqual(self.model.snapshot()['agreements'], [])
        self.assertTrue(self.update(payload, revision=4))

    def test_same_revision_alert_keeps_structured_situation_but_updates_action(self):
        self.update(brief('付款仍在谈，样品检查已确认'), revision=3)
        self.model.observe({'kind': 'alert', 'revision': 3, 'summary': '付款有变化',
                            'action': '请明确回复', 'provisional': True})
        state = self.model.snapshot()
        self.assertEqual(state['situation'], '付款仍在谈，样品检查已确认')
        self.assertEqual(state['action'], '请明确回复')
        self.assertTrue(state['provisional'])

    def test_valid_empty_lists_remove_old_claims_but_malformed_data_does_not(self):
        self.final('双方确认先看样品。')
        self.update(brief(agreements=[claim('先看样品', '先看样品')]))
        self.assertFalse(self.update({'situation': '错误结构'}, revision=2))
        self.assertEqual(len(self.model.snapshot()['agreements']), 1)
        self.assertTrue(self.update(brief('暂无已核实结论'), revision=2))
        self.assertEqual(self.model.snapshot()['agreements'], [])

    def test_reset_removes_old_session_claims_and_evidence(self):
        self.final('双方确认先看样品。')
        payload = brief(agreements=[claim('先看样品', '先看样品')])
        payload.update(background='双方先检查样品', background_quotes=['先看样品'],
                       next_goal='明确样品检查安排', next_goal_quotes=['先看样品'])
        self.update(payload, revision=8)
        self.model.reset()
        state = self.model.snapshot()
        self.assertEqual(state['revision'], 0)
        self.assertEqual(state['situation'], '')
        self.assertEqual(state['agreements'], [])
        self.assertEqual(state['background'], '')
        self.assertEqual(state['next_goal'], '')
        self.update(payload)
        self.assertEqual(self.model.snapshot()['agreements'], [])
        self.assertEqual(self.model.snapshot()['background'], '')
        self.assertEqual(self.model.snapshot()['next_goal'], '')

    def test_snapshot_cannot_mutate_the_model(self):
        self.final('双方确认先看样品。')
        payload = brief(agreements=[claim('先看样品', '先看样品')])
        payload.update(background='先检查样品', background_quotes=['先看样品'],
                       next_goal='明确检查安排', next_goal_quotes=['先看样品'])
        self.update(payload)
        snapshot = self.model.snapshot()
        snapshot['agreements'][0]['quotes'].append('假的原话')
        snapshot['background_quotes'].append('假的背景')
        snapshot['next_goal_quotes'].append('假的目标依据')
        self.assertEqual(self.model.snapshot()['agreements'][0]['quotes'], ['先看样品'])
        self.assertEqual(self.model.snapshot()['background_quotes'], ['先看样品'])
        self.assertEqual(self.model.snapshot()['next_goal_quotes'], ['先看样品'])


if __name__ == '__main__':
    unittest.main()
