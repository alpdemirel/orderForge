from common.time_utils import now_local_ns


def test_time_utils_returns_positive_timestamp() -> None:
    assert now_local_ns() > 0
