"""The shop app's incident cases: how each one is produced by running the app, and its truth (trusted side only)."""

MINUTE = 60_000
GOOD, LEAK = "92b1aeb", "ee7027c"

CASES = {
    "leak": {
        "id": "leak", "baseline": GOOD, "duration_ms": 20 * MINUTE,
        "deploys": [{"at_ms": 10 * MINUTE, "service": "inventory", "commit": LEAK}],
        "truth": {"expected": "patch", "service": "inventory", "fault_type": "resource_leak", "commit": LEAK,
                  "file": "shop/inventory.py", "function": "Inventory.reserve"},
    },
}


def case(name, seed):
    """The run description of case *name* at *seed*, without its truth."""
    return {key: value for key, value in CASES[name].items() if key != "truth"} | {"seed": seed}
