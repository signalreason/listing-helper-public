import pytest

from app.limits import GenerationLimits, LimitReached, LoginLimits


def test_generation_hourly_limit_allows_24_starts() -> None:
    def clock() -> int:
        return 100_000

    limits = GenerationLimits(clock)

    for _ in range(24):
        with limits.start(1):
            pass

    with pytest.raises(LimitReached) as caught, limits.start(1):
        pass

    assert caught.value.retry_after == 3_600


def test_generation_limit_releases_in_flight_slot_after_failure() -> None:
    limits = GenerationLimits(lambda: 100_000)

    with pytest.raises(RuntimeError), limits.start(1):
        raise RuntimeError("generation failed")

    with limits.start(1):
        pass


def test_generation_daily_limit_allows_30_starts_spread_across_day() -> None:
    now = [100_000.0]
    limits = GenerationLimits(lambda: now[0])

    for index in range(30):
        now[0] = 100_000 + index * 2_900
        with limits.start(1):
            pass

    with pytest.raises(LimitReached) as caught, limits.start(1):
        pass

    assert caught.value.retry_after == 86_400


def test_generation_rejects_second_in_flight_request_for_same_user() -> None:
    limits = GenerationLimits(lambda: 100_000)

    with limits.start(1), pytest.raises(LimitReached) as caught, limits.start(1):
        pass

    assert caught.value.retry_after == 10


def test_generation_allows_two_users_but_not_a_third_concurrently() -> None:
    limits = GenerationLimits(lambda: 100_000)

    with (
        limits.start(1),
        limits.start(2),
        pytest.raises(LimitReached) as caught,
        limits.start(3),
    ):
        pass

    assert caught.value.retry_after == 10


def test_login_pair_limit_expires_after_15_minutes() -> None:
    now = [100_000.0]
    limits = LoginLimits(lambda: now[0])
    for _ in range(10):
        limits.failure("user@example.com", "127.0.0.1")

    with pytest.raises(LimitReached):
        limits.check("user@example.com", "127.0.0.1")

    now[0] += 901
    limits.check("user@example.com", "127.0.0.1")


def test_login_ip_limit_covers_multiple_emails() -> None:
    limits = LoginLimits(lambda: 100_000)
    for index in range(30):
        limits.failure(f"user-{index}@example.com", "127.0.0.1")

    with pytest.raises(LimitReached) as caught:
        limits.check("another@example.com", "127.0.0.1")

    assert caught.value.retry_after == 900
