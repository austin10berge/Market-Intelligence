import time

from src.screener._yf_timeout import call_with_timeout


def test_returns_result_when_call_finishes_in_time():
    assert call_with_timeout(lambda: 42, timeout=1) == 42


def test_returns_none_when_call_exceeds_timeout():
    def slow():
        time.sleep(5)
        return "too late"

    start = time.monotonic()
    result = call_with_timeout(slow, timeout=0.2, label="slow")
    elapsed = time.monotonic() - start

    assert result is None
    assert elapsed < 1, "call_with_timeout must return promptly, not wait for the hung call"


def test_propagates_exceptions_from_the_wrapped_call():
    def boom():
        raise ValueError("bad ticker")

    try:
        call_with_timeout(boom, timeout=1)
        raise AssertionError("expected ValueError to propagate")
    except ValueError:
        pass
