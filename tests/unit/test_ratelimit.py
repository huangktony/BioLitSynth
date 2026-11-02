import asyncio

import pytest

from biolitsynth.pubmed import TokenBucket


class FakeTime:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    async def sleep(self, s):
        self.slept.append(round(s, 6))
        self.now += s


def bucket(rate, t):
    return TokenBucket(rate, clock=t.clock, sleep=t.sleep)


async def test_requests_are_spaced_by_the_interval():
    t = FakeTime()
    b = bucket(10, t)
    for _ in range(4):
        await b.acquire()
    assert t.slept == [0.1, 0.1, 0.1]           # the first goes straight through


async def test_three_per_second_without_a_key():
    t = FakeTime()
    b = bucket(3, t)
    for _ in range(4):
        await b.acquire()
    assert t.now == pytest.approx(3 * (1 / 3))


async def test_idle_time_is_not_banked_into_a_burst():
    t = FakeTime()
    b = bucket(10, t)
    await b.acquire()
    t.now += 5                                   # long idle
    await b.acquire()
    await b.acquire()
    assert t.slept == [0.1]                      # only the second pair is spaced; no burst credit


async def test_concurrent_tasks_share_one_schedule():
    t = FakeTime()
    b = bucket(10, t)
    await asyncio.gather(*(b.acquire() for _ in range(5)))
    # The fake sleep advances one shared clock, so individual waits look shorter than real ones. The total is
    # what matters: five requests need four intervals, however the tasks interleave.
    assert t.now == pytest.approx(0.4)


def test_rate_must_be_positive():
    with pytest.raises(ValueError):
        TokenBucket(0)
