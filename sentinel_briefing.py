"""Small, evidence-bound conversation brief for the desktop presentation layer.

This module imports no UI, audio, SDK or storage code. Quotes establish that a
claim has a source, not that the model interpreted agreement correctly. Views
must continue to identify these items as AI summaries that need checking.
"""
from __future__ import annotations

import copy


def _validated_quotes(raw, confirmed_text):
    """Only the current confirmed transcript can support visible context."""
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        return []
    if any(not isinstance(quote, str) or not quote.strip() or len(quote) > 180
           or quote not in confirmed_text for quote in raw):
        return []
    return list(dict.fromkeys(raw))


def _evidenced_items(raw, confirmed_text):
    items = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        text, quotes = entry.get('text'), entry.get('quotes')
        if not isinstance(text, str) or not text.strip():
            continue
        quotes = _validated_quotes(quotes, confirmed_text)
        if not quotes:
            continue
        item = {'text': text.strip()[:120], 'quotes': quotes}
        if item not in items:
            items.append(item)
        if len(items) == 4:
            break
    return items


def _context_field(raw, name, confirmed_text):
    text = raw.get(name)
    quotes = _validated_quotes(raw.get(name + '_quotes'), confirmed_text)
    if not isinstance(text, str) or not text.strip() or not quotes:
        return '', []
    return text.strip()[:180], quotes


def validate_briefing(raw, confirmed_text):
    """Return a bounded brief or None for an invalid top-level structure.

    The original three fields remain required. Optional background / next_goal
    fields need their own confirmed quotes; old providers can omit them safely.
    next_goal is a suggested outcome to pursue, never a spoken or achieved result.
    Quotes establish source availability, not semantic correctness. Invalid items
    are discarded; empty lists intentionally clear old claims. Only the supplied
    confirmed text is evidence: partials, model state and private notes are not.
"""
    if not isinstance(raw, dict) or not isinstance(confirmed_text, str):
        return None
    if not isinstance(raw.get('situation'), str):
        return None
    if any(not isinstance(raw.get(field), list) for field in ('agreements', 'open_questions')):
        return None
    background, background_quotes = _context_field(raw, 'background', confirmed_text)
    next_goal, next_goal_quotes = _context_field(raw, 'next_goal', confirmed_text)
    return {
        'background': background,
        'background_quotes': background_quotes,
        'situation': raw['situation'].strip()[:220],
        'agreements': _evidenced_items(raw['agreements'], confirmed_text),
        'open_questions': _evidenced_items(raw['open_questions'], confirmed_text),
        'next_goal': next_goal,
        'next_goal_quotes': next_goal_quotes,
    }


class ConversationBrief:
    """Consume session events on the UI thread; keep UI-independent state.

``observe`` returns whether the visible brief changed. Confirmed turns are kept
for evidence validation, and are removed by ``reset`` on every new session.
Request revisions order analysis results; they are not a transcript-version or
session identifier, so callers must still isolate events by session lifetime.
"""

    def __init__(self):
        self.reset()

    def reset(self):
        self._turns = {}
        self._structured_revision = -1
        self._invalidated_through_revision = -1
        self._state = {
            'background': '', 'background_quotes': [],
            'situation': '', 'agreements': [], 'open_questions': [],
            'next_goal': '', 'next_goal_quotes': [],
            'action': '', 'provisional': False, 'revision': 0,
        }

    def _invalidate(self):
        self._invalidated_through_revision = self._state['revision']
        self._state.update(situation='原文或角色已更新，等待重新归纳。',
                           background='', background_quotes=[], next_goal='', next_goal_quotes=[],
                           agreements=[], open_questions=[], action='', provisional=True)
        self._structured_revision = -1

    def observe(self, event):
        if not isinstance(event, dict):
            return False
        before = self.snapshot()
        kind = event.get('kind')
        if kind == 'final':
            ident, text = event.get('turn_id'), event.get('text')
            if type(ident) is not int or ident < 1 or not isinstance(text, str) or not text.strip():
                return False
            if ident in self._turns and self._turns[ident] != text:
                self._invalidate()
            self._turns[ident] = text
        elif kind in ('attribution_update', 'speaker_roles'):
            if kind == 'attribution_update':
                ident, text = event.get('turn_id'), event.get('text')
                if type(ident) is int and ident in self._turns and isinstance(text, str) and text.strip():
                    self._turns[ident] = text
            self._invalidate()
        elif kind in ('briefing', 'alert'):
            revision = event.get('revision', self._state['revision'])
            if type(revision) is not int or revision < self._state['revision']:
                return False
            if revision <= self._invalidated_through_revision:
                return False
            if kind == 'briefing':
                confirmed = '\n'.join(self._turns.values())
                brief = validate_briefing(event.get('briefing'), confirmed)
                if brief is None:
                    return False
                self._state.update(brief)
                self._structured_revision = revision
            else:
                # The alert for the same request must not replace its fuller
                # structured situation with the alert's shorter evidence text.
                summary = event.get('summary')
                if (self._structured_revision != revision and isinstance(summary, str)
                        and summary.strip()):
                    self._state['situation'] = summary.strip()[:220]
                action = event.get('action')
                if isinstance(action, str):
                    self._state['action'] = action.strip()[:160]
            self._state['revision'] = revision
            self._state['provisional'] = event.get('provisional') is True
        return before != self._state

    def snapshot(self):
        """Return detached view state; ``next_goal`` always denotes a suggestion."""
        return copy.deepcopy(self._state)
