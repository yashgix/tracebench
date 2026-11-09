"""The shop app's incident cases (SPEC-4 B2): how each one is produced by running the app, and its truth (trusted side
only). Ids are opaque so that nothing the investigator could see names the fault."""

MINUTE = 60_000
GOOD, LEAK = "92b1aeb", "ee7027c"
LIMITER, PER_PRODUCT = "5e058b3", "268076a"
PER_USER, ORDER_CAP, LOG_WORDING = "e3ae795", "a2a4dc4", "828689b"
QUERY_HELPER, ANY_CASE, LOWER_CASED = "ccc74e0", "0b1e0de", "bfc0fd2"
AT, DURATION = 10 * MINUTE, 20 * MINUTE
ALL_CHECKS = ["check_inventory.py", "check_limits.py", "check_orders.py", "check_lookup.py"]


def _patch(service, fault_type, commit, file, function=None):
    return {"expected": "patch", "service": service, "fault_type": fault_type, "commit": commit, "file": file,
            "function": function}


CASES = {
    "leak": {
        "id": "c1", "baseline": GOOD, "deploys": [{"at_ms": AT, "service": "inventory", "commit": LEAK}],
        "truth": _patch("inventory", "resource_leak", LEAK, "shop/inventory.py", "Inventory.reserve"),
        "protected": ["check_inventory.py"],
    },
    "config": {
        "id": "c2", "baseline": LIMITER, "deploys": [{"at_ms": AT, "service": "web", "commit": PER_PRODUCT}],
        "truth": _patch("web", "config_error", PER_PRODUCT, "shop/config.py"),
        "protected": ["check_inventory.py", "check_limits.py"],
    },
    "decoy": {
        "id": "c3", "baseline": PER_USER,
        "deploys": [{"at_ms": AT, "service": "orders", "commit": ORDER_CAP},
                    {"at_ms": AT + MINUTE + MINUTE // 2, "service": "web", "commit": LOG_WORDING}],
        "truth": _patch("orders", "code_defect", ORDER_CAP, "shop/orders.py", "Orders.place"),
        "protected": ["check_inventory.py", "check_limits.py", "check_orders.py"],
    },
    "slow_query": {
        "id": "c4", "baseline": QUERY_HELPER, "catalogue": 6000,
        "deploys": [{"at_ms": AT, "service": "inventory", "commit": ANY_CASE}],
        "truth": _patch("inventory", "slow_query", ANY_CASE, "shop/inventory.py", "Inventory.stock"),
        "protected": ALL_CHECKS,
    },
    "outside_lock": {
        "id": "c5", "baseline": LOWER_CASED, "deploys": [], "db_lock": {"at_ms": AT},
        "truth": {"expected": "escalate"},
        "protected": ALL_CHECKS,
    },
    "load_test": {
        "id": "c6", "baseline": LOWER_CASED, "deploys": [],
        "load_test": {"user": "perf-loadtest", "sku": "sku-000", "start_ms": AT, "end_ms": AT + 110_000, "per_s": 100},
        "truth": {"expected": "dismiss"},
        "protected": ALL_CHECKS,
    },
}
TRUSTED = ("truth", "protected")


def case(name, seed):
    """The run description of case *name* at *seed*, without its truth or protected checks."""
    return {key: value for key, value in CASES[name].items() if key not in TRUSTED} | {"seed": seed,
                                                                                         "duration_ms": DURATION}
