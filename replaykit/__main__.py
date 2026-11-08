"""Command line interface for replaykit."""
import argparse
import json
import os
import sys

from .app.baselines import NAMES as APP_BASELINES
from .app.report import render_app_markdown
from .app.suite import investigate_app, load_app_suite, patches, score_app_report
from .baselines import AGENT_NAMES, make_agent
from .broker import BrokerModel
from .inject import inject
from .model import load_scenario
from .report import agents_table, render_investigation_markdown, render_markdown
from .suite import RecordedModel, load_investigation_suite, run_investigations, run_pinned, run_suite


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _load_faults(path):
    try:
        with open(path, encoding="utf-8") as handle:
            faults = json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        if name not in AGENT_NAMES:
            raise ValueError(f"unknown agent {name!r}")
        if name not in names:
            names.append(name)
    return names


def _agent_factories(names, calls):
    return {
        name: (lambda name=name: make_agent(name, calls))
        for name in names
    }


def _build_incidents(scenario, faults, seeds):
    return [inject(scenario, fault, seed) for fault in faults for seed in seeds]


def _write_report(report, path):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_markdown(text, path):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _run_command(args):
    scenario = load_scenario(args.scenario)
    faults = _load_faults(args.faults)
    seeds = _parse_seeds(args.seeds)
    names = _parse_agents(args.agents)
    incidents = _build_incidents(scenario, faults, seeds)
    report = run_suite(incidents, _agent_factories(names, scenario["calls"]))
    if args.out:
        _write_report(report, args.out)
    print(agents_table(report))
    return 0


def _suite_command(args):
    report = run_pinned(args.path)
    markdown = render_markdown(report)
    if args.out:
        _write_report(report, args.out)
    if args.md:
        _write_markdown(markdown, args.md)
    sys.stdout.write(markdown)
    return 0


def _models(names, args):
    if args.replay and args.broker:
        raise ValueError("--replay and --broker cannot be used together")
    if names and args.replay:
        with open(args.replay, encoding="utf-8") as handle:
            saved = json.load(handle)
        return {name: RecordedModel(saved, name) for name in names}
    if names and args.broker:
        token = os.environ.get("REPLAYKIT_BROKER_TOKEN")
        if not token:
            raise ValueError("REPLAYKIT_BROKER_TOKEN is not set")
        return {name: BrokerModel(name, args.broker, token) for name in names}
    if names:
        raise ValueError("model investigators need --replay or --broker")
    return {}


def _investigate_command(args):
    names = [n for n in load_investigation_suite(args.path)["suite"]["investigators"] if n not in AGENT_NAMES]
    report = run_investigations(args.path, _models(names, args))
    markdown = render_investigation_markdown(report)
    if args.out:
        _write_report(report, args.out)
    if args.md:
        _write_markdown(markdown, args.md)
    sys.stdout.write(markdown)
    return 0


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from None


def _investigate_app_command(args):
    loaded = load_app_suite(args.path, args.repo)
    names = [n for n in loaded["suite"]["investigators"] if n not in APP_BASELINES]
    report = investigate_app(loaded, args.repo, _models(names, args))
    _write_report(report, args.out)
    print(f"{len(report['incidents'])} incidents, {len(patches(report))} patches to validate")
    return 0


def _score_app_command(args):
    report = score_app_report(_read_json(args.path), _read_json(args.validations))
    markdown = render_app_markdown(report)
    _write_report(report, args.out)
    if args.md:
        _write_markdown(markdown, args.md)
    sys.stdout.write(markdown)
    return 0


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
    suite = commands.add_parser("suite", help="run a pinned suite file")
    suite.add_argument("path", metavar="PATH")
    suite.add_argument("--out", metavar="PATH")
    suite.add_argument("--md", metavar="PATH")
    investigate = commands.add_parser("investigate", help="run an investigation suite")
    investigate.add_argument("path", metavar="PATH")
    investigate.add_argument("--replay", metavar="REPORT")
    investigate.add_argument("--broker", metavar="URL")
    investigate.add_argument("--out", metavar="PATH")
    investigate.add_argument("--md", metavar="PATH")
    app = commands.add_parser("investigate-app", help="investigate an app suite (no patch validation)")
    app.add_argument("path", metavar="PATH")
    app.add_argument("--repo", required=True, metavar="SHOP_REPO")
    app.add_argument("--replay", metavar="REPORT")
    app.add_argument("--broker", metavar="URL")
    app.add_argument("--out", required=True, metavar="PATH")
    score = commands.add_parser("score-app", help="score an app investigation with its patch validations")
    score.add_argument("path", metavar="INVESTIGATION")
    score.add_argument("--validations", required=True, metavar="PATH")
    score.add_argument("--out", required=True, metavar="PATH")
    score.add_argument("--md", metavar="PATH")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
        if args.command == "run":
            return _run_command(args)
        if args.command == "investigate":
            return _investigate_command(args)
        if args.command == "investigate-app":
            return _investigate_app_command(args)
        if args.command == "score-app":
            return _score_app_command(args)
        return _suite_command(args)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
