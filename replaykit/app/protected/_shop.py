"""A whole shop for the protected checks, and a way to run requests on it to completion."""

import itertools
import random

from shop.app import Shop
from shop.runtime import Runtime
from shop.telemetry import Telemetry

_names = itertools.count()


def db_name():
    return f"protected-{next(_names)}"


def telemetry(runtime):
    return Telemetry(runtime, random.Random(1))


def shop(stock):
    runtime = Runtime()
    return Shop(runtime, telemetry(runtime), db_name(), stock)


def statuses(app, requests, until=60_000):
    """Run (at_ms, coroutine function) requests and return their results in finishing order."""
    done = []

    async def request(make):
        done.append(await make())

    for at, make in requests:
        app.runtime.spawn(request(make), at=at)
    app.runtime.run(until)
    return done
