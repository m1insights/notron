from datetime import datetime, timedelta

import pytest

from notron import guard, nodes, when
from notron.state import Action, State


@pytest.mark.parametrize('clear', [False, 'false', None, 0])
def test_unclear_or_invalid_clarity_never_creates_action(clear):
    class Brain:
        def ask_json(self, **kw):
            return {'kind':'reminder', 'op':'create', 'title':'Call', 'clear':clear}
    state = nodes.scheduler(State(request='remind me to call', intent='remind'), brain=Brain())
    assert not state.actions
    assert 'say' in state.answer.lower()


def test_task_description_weekday_does_not_override_explicit_reminder_timing():
    assert when.weekday_named('remind me tonight to book a table for Saturday') is None
    assert when.weekday_named('remind me Thursday to book a table for Saturday') == 3
    friday = datetime.now() + timedelta(days=(4-datetime.now().weekday()) % 7 or 7)
    action = Action(kind='reminder', op='create', title='Call', when=friday.strftime('%Y-%m-%dT14:00'))
    assert not guard.check_action(action, request='remind me Thursday to call')
