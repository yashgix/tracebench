"""Run the shop app as deployed: each service's code from its commit, seeded traffic, deploys at fixed times, and the
monitoring system (per-minute request metrics from spans, and one page rule). Everything here is trusted; the app
code it loads is the code under investigation."""

import importlib.util
import random
import subprocess
import sys
from pathlib import Path

MINUTE = 60_000
SERVICES = ("web", "orders", "inventory")
CALLS = {"web": ["orders", "inventory"], "orders": ["inventory"], "inventory": []}
PAGE_RATE, PAGE_MINUTES = 0.05, 2


def blobs(repo, commit):
    """Yield (path, bytes) for every file of *commit*, read blob by blob (no checkout, no git filters)."""
    git = ["git", "--git-dir", str(Path(repo) / ".git")]
    listing = subprocess.run([*git, "ls-tree", "-r", "-z", commit], capture_output=True, check=True).stdout
    for entry in filter(None, listing.split(b"\0")):
        info, raw = entry.split(b"\t", 1)
        mode, kind, blob = info.decode().split()
        path = Path(raw.decode())
        if kind != "blob" or mode != "100644" or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"cannot export {path} exactly")
        yield path, subprocess.run([*git, "cat-file", "blob", blob], capture_output=True, check=True).stdout


def export(repo, commit, target):
    """Write *commit*'s tree to *target*."""
    for path, data in blobs(repo, commit):
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        (target / path).write_bytes(data)


def load(root, name):
    """Import the `shop` package in *root* under the module name *name*."""
    if name not in sys.modules:
        init = Path(root) / "shop" / "__init__.py"
        spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(init.parent)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return {part: importlib.import_module(f"{name}.{part}")
            for part in ("app", "db", "inventory", "orders", "runtime", "telemetry", "web")}


class Deployment:
    """The running services, each built from the code of the commit deployed for it."""

    def __init__(self, platform, seed, db_name, stock):
        self.runtime = platform["runtime"].Runtime()
        self.telemetry = platform["telemetry"].Telemetry(self.runtime, random.Random(f"{seed}:ids"))
        self._connector, self._opened = platform["db"].connector(db_name), []
        self.keeper = platform["db"].create(self.connect, stock)
        self.services, self.deploys = {}, []

    def connect(self):
        self._opened.append(self._connector())
        return self._opened[-1]

    def hold_lock(self, at_ms):
        """An outside job: from *at_ms* to the end of the run, a connection of its own holds a write transaction on
        `items`."""
        def begin():
            job = self.connect()
            job.execute("BEGIN IMMEDIATE")
            job.execute("UPDATE items SET stock = stock WHERE rowid = 1")
        self.runtime.call_at(at_ms, begin)

    def close(self):
        """Close every database connection, leaked ones included, once the run is recorded."""
        for connection in self._opened:
            connection.close()

    def deploy(self, service, code, commit):
        """Start *service* from *code* (a loaded package) at the current time, replacing the running one."""
        old = self.services.get(service)
        if old is not None and hasattr(old, "running"):
            old.running = False
        module = code[service]
        cls = {"web": "Web", "orders": "Orders", "inventory": "Inventory"}[service]
        args = {"inventory": (self.connect,), "orders": (self.services.get("inventory"),),
                "web": (self.services.get("orders"), self.services.get("inventory"))}[service]
        self.services[service] = instance = getattr(module, cls)(self.runtime, self.telemetry, *args)
        self._rewire()
        if hasattr(instance, "report_pool"):
            self.runtime.spawn(instance.report_pool())
        self.deploys.append({"time_ms": self.runtime.now, "service": service, "commit": commit})

    def _rewire(self):
        orders, inventory = self.services.get("orders"), self.services.get("inventory")
        if orders is not None:
            orders.inventory = inventory
        if "web" in self.services:
            self.services["web"].orders, self.services["web"].inventory = orders, inventory


def catalogue(seed, size=40):
    """Stock and popularity: a long tail of products, a few of them nearly sold out."""
    rng = random.Random(f"{seed}:catalogue")
    skus = [f"sku-{i:03d}" for i in range(size)]
    stock = {sku: rng.randint(800, 2000) for sku in skus}
    for rank in (17, 26, 35):
        stock[skus[rank]] = rng.randint(0, 6)
    return stock, skus, [1 / (rank + 1) for rank in range(len(skus))]


def traffic(deployment, seed, skus, weights, until_ms, mean_gap_ms=500):
    """Schedule seeded requests: 80% checkouts, 20% product views."""
    rng = random.Random(f"{seed}:traffic")
    statuses = []

    async def request(kind, args):
        web = deployment.services["web"]
        statuses.append(await (web.checkout(*args) if kind == "checkout" else web.product(*args)))

    t = 0
    while True:
        t += max(1, int(rng.expovariate(1 / mean_gap_ms)))
        if t >= until_ms:
            return statuses
        sku = rng.choices(skus, weights)[0]
        if rng.random() < 0.8:
            deployment.runtime.spawn(request("checkout", (f"user-{rng.randint(1, 900)}", sku, rng.randint(1, 3))), at=t)
        else:
            deployment.runtime.spawn(request("product", (sku,)), at=t)


def load_test(deployment, spec):
    """An internal client's checkouts at a fixed rate: *spec* has `user`, `sku`, `start_ms`, `end_ms`, `per_s`."""
    async def checkout():
        await deployment.services["web"].checkout(spec["user"], spec["sku"], 1)

    for t in range(spec["start_ms"], spec["end_ms"], 1000 // spec["per_s"]):
        deployment.runtime.spawn(checkout(), at=t)


def request_metrics(spans, until_ms):
    """Per minute and service, from server spans (as a collector's span-metrics step would)."""
    points = []
    for minute in range(until_ms // MINUTE):
        start, end = minute * MINUTE, (minute + 1) * MINUTE
        for service in SERVICES:
            done = [s for s in spans if s["kind"] == "server" and s["service"] == service and start <= s["end_ms"] < end]
            if not done:
                continue
            errors = sum(1 for s in done if s["status"] == "ERROR")
            durations = sorted(s["end_ms"] - s["start_ms"] for s in done)
            p95 = durations[max(0, -(-len(durations) * 95 // 100) - 1)]
            at = end
            points += [{"time_ms": at, "service": service, "name": "http.server.request.count", "value": len(done)},
                       {"time_ms": at, "service": service, "name": "http.server.error_rate",
                        "value": round(errors / len(done), 4)},
                       {"time_ms": at, "service": service, "name": "http.server.duration.p95", "value": p95}]
    return points


def first_page(points):
    """The page rule: web's 5xx rate over 5% for two minutes in a row."""
    run = 0
    for point in points:
        if point["service"] == "web" and point["name"] == "http.server.error_rate":
            run = run + 1 if point["value"] > PAGE_RATE else 0
            if run == PAGE_MINUTES:
                return {"time_ms": point["time_ms"], "service": "web", "rule": "error_rate_high", "severity": "page"}
    return None
