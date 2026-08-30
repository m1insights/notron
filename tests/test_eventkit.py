import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from juno import eventkit


def test_a_script_result_comes_back_as_parsed_json():
    out = eventkit.run("x", runner=lambda script, timeout: '{"count": 3}')
    assert out == {"count": 3}


def test_an_empty_answer_is_an_error_not_a_silent_none():
    """EventKit returning nothing means the run loop exited before the callback
    fired. Silently treating that as 'no reminders' would be a lie."""
    with pytest.raises(eventkit.EventKitError):
        eventkit.run("x", runner=lambda script, timeout: "")


def test_a_script_error_is_reported_with_what_the_script_said():
    def boom(script, timeout):
        raise RuntimeError("execution error: Can't get x")

    with pytest.raises(eventkit.EventKitError) as e:
        eventkit.run("x", runner=boom)
    assert "Can't get x" in str(e.value)


def test_every_async_read_pumps_a_run_loop_or_it_returns_nothing():
    """JXA has no await. Without the run loop the script exits before EventKit
    calls back, and the read comes home empty every time."""
    assert "runModeBeforeDate" in eventkit.AWAIT
    assert "NSDefaultRunLoopMode" in eventkit.AWAIT
