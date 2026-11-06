"""Command line interface for replaykit."""
import argparse
import json
import sys

from .baselines import LastDeployAgent, ThresholdAgent
from .inject import inject
from .model import load_scenario
from .suite import run_suite

AGENTS = {"last_deploy": LastDeployAgent, "threshold": ThresholdAgent}


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _load_faults(path):
    try:
        with open(path, encoding="utf-8") as handle:
            faults = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from None
    if not isinstance(faults, list):
        raise ValueError(f"{path}: faults must be a JSON list")
    return faults


def _parse_seeds(text):
    seeds = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            raise ValueError("seeds must be a comma-separated list of ints")
        try:
            seeds.append(int(part))
        except ValueError:
            raise ValueError(f"invalid seed {part!r}") from None
    return seeds


def _parse_agents(text):
    names = []
    for part in text.split(","):
        name = part.strip()
        if not name:
            raise ValueError("agents must be a comma-separated list of names")
        if name not in AGENTS:
            raise ValueError(f"unknown agent {name!r}")
        if name not in names:
            names.append(name)
    return names


def _build_incidents(scenario, faults, seeds):
    return [inject(scenario, fault, seed) for fault in faults for seed in seeds]


def _markdown_table(report):
    lines = [
        "| agent | runs | root cause accuracy | mean points | brier |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name in sorted(report["agents"]):
        summary = report["agents"][name]
        brier = "-" if summary["brier"] is None else summary["brier"]
        lines.append(
            f"| {name} | {summary['runs']} | {summary['root_cause_accuracy']} | "
            f"{summary['mean_points']} | {brier} |"
        )
    return "\n".join(lines)


def _write_report(report, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _parser():
    parser = _ArgumentParser(
        prog="replaykit", description="Replay incidents against AI SRE agents."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run agents on a scenario")
    run.add_argument("--scenario", required=True, metavar="PATH")
    run.add_argument("--faults", required=True, metavar="PATH")
    run.add_argument("--seeds", required=True, metavar="SEEDS")
    run.add_argument("--agents", required=True, metavar="AGENTS")
    run.add_argument("--out", metavar="PATH")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        scenario = load_scenario(args.scenario)
        faults = _load_faults(args.faults)
        seeds = _parse_seeds(args.seeds)
        names = _parse_agents(args.agents)
        incidents = _build_incidents(scenario, faults, seeds)
        report = run_suite(incidents, {name: AGENTS[name] for name in names})
        if args.out:
            _write_report(report, args.out)
        print(_markdown_table(report))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
