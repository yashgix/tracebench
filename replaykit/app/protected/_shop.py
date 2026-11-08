"""A whole shop for the protected checks, and a way to run requests on it to completion."""

import contextlib
import itertools
import random
import sqlite3

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


@contextlib.contextmanager
def statements():
    """Every statement SQLite runs on a connection opened inside the block, as SQLite expanded it; the virtual
    machine steps each of them took (same order); and a fresh connection of the check's own to the first database
    opened (for explaining the statements)."""
    ran, work, first, connect = [], [], [], sqlite3.connect

    def started(sql):
        ran.append(sql)
        work.append(0)

    def step():
        if work:
            work[-1] += 1  # db.query runs each statement to its end before the next one starts
        return 0

    def traced(*args, **kwargs):
        first.append((args, kwargs))
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(started)
        connection.set_progress_handler(step, 1)
        return connection

    sqlite3.connect = traced
    try:
        yield ran, work, lambda: connect(*first[0][0], **first[0][1])
    finally:
        sqlite3.connect = connect
