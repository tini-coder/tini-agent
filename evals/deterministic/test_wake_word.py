"""The wake-word matcher is a pure function — so it gets deterministic evals.
Whisper mangles phrases in predictable ways; these cases pin the fuzziness."""

import pytest

from tini.gateway.voice import matches_wake

SHOULD_WAKE = [
    ("tini tini", "tini tini"),
    ("Tini, tini!", "tini tini"),            # punctuation
    ("tinitini", "tini tini"),               # whisper drops the space
    ("so anyway tini tini schedule it", "tini tini"),  # embedded in speech
    ("tini tuni", "tini tini"),              # one-letter mangle → fuzzy match
    ("Hey Tini", "hey tini"),
    ("hey computer, what's up", "hey computer"),
    # regression from the first live session: whisper wrote the wake word in
    # kana — variants after a comma cover other scripts
    ("わくわく", "tini tini,わくわく"),
    ("わくわくわく", "tini tini,わくわく"),
    ("小助手你好", "tini tini,小助手"),
]

SHOULD_NOT_WAKE = [
    ("what a nice day", "tini tini"),
    ("wake up call at nine", "tini tini"),
    ("", "tini tini"),
    ("tini tini", ""),                        # no wake word configured
    ("walk to work", "tini tini"),
]


@pytest.mark.parametrize("heard,wake", SHOULD_WAKE, ids=[h for h, _ in SHOULD_WAKE])
def test_wakes(heard, wake):
    assert matches_wake(heard, wake)


@pytest.mark.parametrize("heard,wake", SHOULD_NOT_WAKE, ids=[h or "empty" for h, _ in SHOULD_NOT_WAKE])
def test_stays_asleep(heard, wake):
    assert not matches_wake(heard, wake)
