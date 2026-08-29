"""Command line entrypoint.

`audit` lands in T17 and `trace render` in T24. What is here now is the corpus
build, which `make corpus` and `make corpus-check` call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from materia import __version__

DEFAULT_CORPUS = Path("corpus")


def _config(section: str, key: str, fallback):
    """Read a run budget from config.yaml.

    The caps live there because docs/EVALUATION.md section 4 requires both
    systems to get the same room, and a number hardcoded in two places drifts.
    """
    try:
        import yaml

        data = yaml.safe_load(Path("config.yaml").read_text())
        return data[section][key]
    except Exception:  # noqa: BLE001 - a missing config falls back, not crashes
        return fallback


def _build_corpus(arguments: argparse.Namespace) -> int:
    from materia.corpus.build import build_corpus

    manifest = build_corpus(arguments.directory)
    workbooks = manifest["workbooks"]
    print(f"{len(workbooks)} workbooks written to {arguments.directory}")
    for entry in workbooks:
        breaks = entry["legitimate_breaks"]
        note = f", {len(breaks)} legitimate pattern breaks" if breaks else ""
        print(f"  {entry['id']}  {entry['role']:14} {entry['formula_count']} formulas{note}")
    print(f"manifest and checksums written to {arguments.directory}")
    return 0


def _check_corpus(arguments: argparse.Namespace) -> int:
    from materia.corpus.build import check_corpus

    try:
        result = check_corpus(arguments.directory)
    except FileNotFoundError as missing:
        print(f"no checksums at {missing}. Run make corpus first.", file=sys.stderr)
        return 1

    if result.ok:
        print(f"{len(result.matched)} workbooks match the committed checksums")
        return 0

    for name in result.missing:
        print(f"missing: {name}", file=sys.stderr)
    for name in result.mismatched:
        print(f"differs from the committed checksum: {name}", file=sys.stderr)
    print(
        "\nA mismatch usually means a different openpyxl version. "
        "See docs/REPRODUCTION.md section 10.",
        file=sys.stderr,
    )
    return 1


def _evaluate(arguments: argparse.Namespace) -> int:
    import json

    from materia.evaluate import (
        detector_results,
        score,
        update_changelog,
        write_results,
    )

    from materia.evaluate import baseline_results

    manifest = json.loads((arguments.corpus / "manifest.json").read_text())
    scores = [
        score(
            "Detectors only",
            detector_results(arguments.corpus, manifest),
            manifest,
        )
    ]

    # The baseline column appears once there is a run to fill it, and not
    # before. docs/EVALUATION.md section 5 shows it as [TBD] until then rather
    # than as a zero, because a system that has not run has no score.
    from materia.evaluate import solution_results

    expected = {entry["id"] for entry in manifest["workbooks"]}
    baseline_directory = arguments.results / "baseline"
    for directory, system, load in (
        (baseline_directory, "Baseline agent", baseline_results),
        (arguments.results / "solution", "Materia", solution_results),
    ):
        if not directory.is_dir():
            continue
        found = load(directory)
        if not found:
            continue
        scores.append(score(system, found, manifest))
        missing = sorted(expected - set(found))
        if missing:
            print(
                f"{system}: no result for " + ", ".join(missing)
                + ". Scored as reporting nothing on those.",
                file=sys.stderr,
            )

    written = write_results(scores, arguments.results)
    for name, path in written.items():
        print(f"{name}: {path}")

    # Rule 7: no number that belongs in results/ is typed into a doc by hand.
    if arguments.document and Path(arguments.document).exists():
        from materia.evaluate import run_cost, update_results_table

        columns = {
            "Detectors only": "Detectors only",
            "Baseline agent": "Baseline",
            "Materia": "Materia",
        }
        for item in scores:
            column = columns.get(item.system)
            if column is None:
                continue
            sources = {
                "Baseline agent": baseline_directory,
                "Materia": arguments.results / "solution",
            }
            extras = (
                run_cost(sources[item.system]) if item.system in sources
                else {"cost": "none, no model involved"}
            )
            filled = update_results_table(arguments.document, column, item, extras)
            if filled:
                print(f"{arguments.document}: filled the {column} column, "
                      f"{len(filled)} rows")

    if arguments.changelog:
        # Each system fills its own row. Looping every score into one stage
        # wrote the baseline's numbers over the detectors' in Iteration 1.
        stages = {
            "Detectors only": "Iteration 1",
            "Baseline agent": "Baseline",
            "Materia": "Iteration 2",
        }
        for item in scores:
            stage = stages.get(item.system)
            if stage is None:
                continue
            if update_changelog(arguments.changelog, stage, item):
                print(f"changelog: {stage} filled in {arguments.changelog}")
            else:
                print(
                    f"changelog: no {stage} row in {arguments.changelog}",
                    file=sys.stderr,
                )
                return 1

    print()
    print((arguments.results / "headline.md").read_text())
    return 0


def _llm_check(arguments: argparse.Namespace) -> int:
    """One trivial call, to confirm the configured model is real.

    Worth its own command because the alternative is discovering a wrong model
    id part way through a scored run.
    """
    from materia.llm import (
        Message,
        ModelNotAvailable,
        ProviderError,
        ToolDefinition,
        get_client,
    )

    try:
        client = get_client(arguments.provider)
    except ProviderError as error:
        print(error, file=sys.stderr)
        return 1

    tool = ToolDefinition(
        name="add_numbers",
        description="Add two numbers together and return the sum.",
        parameters={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
        },
    )
    try:
        response = client.complete(
            system="You are a calculator. Use the add_numbers tool for any arithmetic.",
            messages=[Message(role="user", content="What is 17 plus 25?")],
            tools=[tool],
        )
    except ModelNotAvailable as error:
        print(error, file=sys.stderr)
        return 2
    except ProviderError as error:
        print(error, file=sys.stderr)
        return 1

    print(f"provider: {client.provider}")
    print(f"model:    {response.model}")
    print(f"tools:    {'called' if response.tool_calls else 'NOT CALLED'}")
    print(f"tokens:   {response.usage.input_tokens} in, {response.usage.output_tokens} out")
    if not response.tool_calls:
        print("the model did not call the tool", file=sys.stderr)
        return 1
    return 0


def _audit_one(workbook, outputs, client, arguments):
    """Audit one workbook and write its result. Returns the result, or None."""
    from materia.audit import audit, write_result
    from materia.preflight import PreflightRejected

    try:
        result = audit(
            workbook,
            outputs=outputs,
            client=client,
            trace_directory=arguments.traces,
            max_candidates=arguments.max_candidates,
        )
    except PreflightRejected as rejection:
        print(f"{workbook} was not audited.", file=sys.stderr)
        print(f"  {rejection.message}", file=sys.stderr)
        return None

    print(result.render())

    if arguments.repair:
        from materia.repair import repair

        outcome = repair(
            workbook,
            result.result.findings,
            target=arguments.repair_to,
            trace_directory=arguments.traces,
        )
        print()
        print(outcome.render())

    if arguments.results:
        # Written per workbook as it finishes, so a sweep that dies halfway
        # keeps the verdicts it paid for.
        written = write_result(result, arguments.results)
        print(f"result written to {written}")
    if arguments.explain:
        print(_explain(result))
    return result


def _audit(arguments: argparse.Namespace) -> int:
    import json

    from materia.audit import outputs_for
    from materia.llm import ProviderError, get_client

    try:
        client = get_client(arguments.provider)
    except ProviderError as error:
        print(error, file=sys.stderr)
        return 1

    declared = (
        [item.strip() for item in arguments.outputs.split(",")]
        if arguments.outputs
        else None
    )

    if arguments.workbook.is_dir():
        manifest = json.loads((arguments.workbook / "manifest.json").read_text())
        workbooks = [arguments.workbook / entry["file"] for entry in manifest["workbooks"]]
    else:
        workbooks = [arguments.workbook]

    totals = {"in": 0, "out": 0}
    findings = rejected = 0
    for index, workbook in enumerate(workbooks, start=1):
        outputs = declared
        if outputs is None and workbook.is_file():
            try:
                outputs = outputs_for(workbook)
            except ValueError as error:
                print(error, file=sys.stderr)
                return 1

        if len(workbooks) > 1:
            print(f"\n{'=' * 74}\n[{index}/{len(workbooks)}] {workbook.name}\n{'=' * 74}")
        result = _audit_one(workbook, outputs, client, arguments)
        if result is None:
            rejected += 1
            continue

        findings += len(result.result.findings)
        for verdict in result.verdicts:
            totals["in"] += verdict.tokens.get("in", 0)
            totals["out"] += verdict.tokens.get("out", 0)

        if len(workbooks) > 1:
            spent = _cost(client.model, totals)
            running = f"  running total: {totals['in'] + totals['out']:,} tokens"
            if spent is not None:
                running += (f", ${spent:.2f} spent, "
                            f"${spent / index * len(workbooks):.2f} projected")
            print(running)

    if len(workbooks) > 1:
        print(f"\n{len(workbooks)} workbooks, {findings} findings, "
              f"{totals['in'] + totals['out']:,} tokens")
        spent = _cost(client.model, totals)
        if spent is not None:
            print(f"cost at published {client.model} rates: ${spent:.2f}")
        if rejected:
            print(f"{rejected} were rejected at preflight", file=sys.stderr)
    return 2 if rejected else 0


def _explain(result) -> str:
    """Where every figure came from, for a reader checking the work."""
    lines = ["HOW TO CHECK THIS", ""]
    lines.append(f"  provider {result.provider}, model {result.model}")
    lines.append("")
    for verdict in result.verdicts:
        lines.append(
            f"  {verdict.address:18} {verdict.verdict:13} "
            f"{verdict.turns} turns, {verdict.tool_calls} tool calls"
        )
        lines.append(f"  {'':18} {verdict.trace_path}")
    return "\n".join(lines)


def _report(arguments: argparse.Namespace) -> int:
    """Re-render a report from trajectories already on disk. No model calls."""
    from materia.audit import from_trajectories
    from materia.preflight import PreflightRejected

    outputs = (
        [item.strip() for item in arguments.outputs.split(",")]
        if arguments.outputs
        else None
    )
    try:
        result = from_trajectories(arguments.workbook, arguments.traces, outputs)
    except PreflightRejected as rejection:
        print(rejection.message, file=sys.stderr)
        return 2
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    if not result.verdicts:
        print(f"no verdicts in {arguments.traces}", file=sys.stderr)
        return 1

    print(result.render())

    if arguments.repair:
        from materia.repair import repair

        outcome = repair(
            arguments.workbook,
            result.result.findings,
            target=arguments.repair_to,
            trace_directory=arguments.traces,
        )
        print()
        print(outcome.render())

    if arguments.explain:
        print(_explain(result))
    return 0


def _trace_render(arguments: argparse.Namespace) -> int:
    from materia.trace_render import render

    try:
        print(render(arguments.trajectory), end="")
    except (FileNotFoundError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


def _trace_index(arguments: argparse.Namespace) -> int:
    from materia.trace_render import FEATURED, write_featured, write_index

    written = write_featured(arguments.directory)
    index = write_index(arguments.directory)

    for path in written:
        print(f"rendered {path}")
    missing = [item for item in FEATURED if not item.available]
    for item in missing:
        print(f"not present: {item.number}. {item.title}", file=sys.stderr)
    print(f"index written to {index}")
    return 0


# Published rates for the scored provider, so a run can say what it cost
# rather than leaving it to be worked out later. docs/EVALUATION.md section 4.
RATES_USD_PER_MILLION = {"gpt-5.6-terra": (2.00, 12.00)}


def _cost(model: str, tokens: dict[str, int]) -> float | None:
    rate = RATES_USD_PER_MILLION.get(model)
    if rate is None:
        return None
    return tokens["in"] * rate[0] / 1e6 + tokens["out"] * rate[1] / 1e6


def _baseline_one(workbook, outputs, client, arguments):
    """Run one workbook and write its result. Returns the result and an exit code."""
    import json

    from materia.baseline import run_baseline

    result = run_baseline(
        workbook, outputs, client,
        trace_directory=arguments.traces,
        max_turns=arguments.max_turns,
        max_tokens=arguments.max_tokens,
    )

    print(f"{result.workbook}: {len(result.findings)} findings reported")
    print(f"  {result.turns} turns, {result.tool_calls} tool calls, "
          f"{result.tokens['in'] + result.tokens['out']:,} tokens")
    if result.stopped:
        where = "the provider stopped it" if result.failed else "it used its budget"
        print(f"  stopped early, {where}: {result.stopped}")
    if result.raw_findings:
        print("  the agent wrote a findings file that is not valid JSON", file=sys.stderr)
    for finding in result.findings:
        print(f"  {finding.get('sheet')}!{finding.get('cell')} "
              f"confidence={finding.get('confidence')}")
    print(f"  trajectory: {result.trace_path}")

    if arguments.results:
        # Written as each workbook finishes, so a sweep that dies halfway
        # keeps what it paid for.
        arguments.results.mkdir(parents=True, exist_ok=True)
        path = arguments.results / f"{Path(workbook).stem}.json"
        path.write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n")
        from materia.llm import write_provenance

        write_provenance(arguments.results, client)
        print(f"  result written to {path}")

    # A run the provider cut short is not a baseline that found nothing, and
    # make must not treat it as one.
    return result, 1 if result.failed else 0


def _baseline(arguments: argparse.Namespace) -> int:
    import json

    from materia.audit import outputs_for
    from materia.llm import ProviderError, get_client

    try:
        client = get_client(arguments.provider)
    except ProviderError as error:
        print(error, file=sys.stderr)
        return 1

    declared = (
        [item.strip() for item in arguments.outputs.split(",")]
        if arguments.outputs
        else None
    )

    if arguments.workbook.is_dir():
        manifest = json.loads((arguments.workbook / "manifest.json").read_text())
        workbooks = [arguments.workbook / entry["file"] for entry in manifest["workbooks"]]
    else:
        workbooks = [arguments.workbook]

    totals = {"in": 0, "out": 0}
    findings = failures = 0
    for index, workbook in enumerate(workbooks, start=1):
        try:
            outputs = declared or outputs_for(workbook)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1

        if len(workbooks) > 1:
            print(f"\n[{index}/{len(workbooks)}] {workbook.name}")
        result, code = _baseline_one(workbook, outputs, client, arguments)
        failures += code
        findings += len(result.findings)
        totals["in"] += result.tokens["in"]
        totals["out"] += result.tokens["out"]

        if len(workbooks) > 1:
            spent = _cost(client.model, totals)
            running = f"  running total: {totals['in'] + totals['out']:,} tokens"
            if spent is not None:
                running += (f", ${spent:.2f} spent, "
                            f"${spent / index * len(workbooks):.2f} projected")
            print(running)

    if len(workbooks) > 1:
        print(f"\n{len(workbooks)} workbooks, {findings} findings reported, "
              f"{totals['in'] + totals['out']:,} tokens")
        spent = _cost(client.model, totals)
        if spent is not None:
            print(f"cost at published {client.model} rates: ${spent:.2f}")
        if failures:
            print(f"{failures} of {len(workbooks)} were cut short by the provider",
                  file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="materia", description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"materia {__version__}")
    commands = parser.add_subparsers(dest="command")

    corpus = commands.add_parser("corpus", help="generate or verify the evaluation corpus")
    actions = corpus.add_subparsers(dest="action", required=True)

    build = actions.add_parser("build", help="generate all twelve workbooks")
    build.add_argument("--directory", type=Path, default=DEFAULT_CORPUS)
    build.set_defaults(handler=_build_corpus)

    check = actions.add_parser("check", help="compare workbooks against the checksums")
    check.add_argument("--directory", type=Path, default=DEFAULT_CORPUS)
    check.set_defaults(handler=_check_corpus)

    evaluate = commands.add_parser("eval", help="score result sets against the manifest")
    evaluate.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    evaluate.add_argument("--results", type=Path, default=Path("results"))
    evaluate.add_argument(
        "--changelog",
        type=Path,
        default=None,
        help="fill the matching changelog row in this file",
    )
    evaluate.add_argument(
        "--document", type=Path, default=Path("docs/EVALUATION.md"),
        help="results table to fill in place; pass an empty path to skip",
    )
    evaluate.set_defaults(handler=_evaluate)

    audit_command = commands.add_parser("audit", help="audit one workbook")
    audit_command.add_argument("workbook", type=Path)
    audit_command.add_argument(
        "--outputs",
        default=None,
        help='declared output cells, comma separated, for example "P&L!AA15,Valuation!B7"',
    )
    audit_command.add_argument("--provider", default=None, choices=["groq", "openai"])
    audit_command.add_argument("--traces", type=Path, default=Path("trajectories/solution"))
    audit_command.add_argument("--results", type=Path, default=None)
    audit_command.add_argument(
        "--max-candidates",
        type=int,
        default=None,
        help="stop after this many candidates, for a quick look or a tight budget",
    )
    audit_command.add_argument(
        "--explain",
        action="store_true",
        help="print where every figure came from, trajectory paths included",
    )
    audit_command.add_argument(
        "--repair",
        action="store_true",
        help="ask about each finding and write approved changes to a copy",
    )
    audit_command.add_argument(
        "--repair-to",
        type=Path,
        default=None,
        help="where the corrected copy goes. Never the input workbook.",
    )
    audit_command.set_defaults(handler=_audit)

    report_command = commands.add_parser(
        "report", help="re-render a report from saved trajectories, no model calls"
    )
    report_command.add_argument("workbook", type=Path)
    report_command.add_argument("--traces", type=Path, default=Path("trajectories/solution"))
    report_command.add_argument("--outputs", default=None)
    report_command.add_argument("--explain", action="store_true")
    report_command.add_argument(
        "--repair",
        action="store_true",
        help="ask about each finding and write approved changes to a copy",
    )
    report_command.add_argument("--repair-to", type=Path, default=None)
    report_command.set_defaults(handler=_report)

    baseline = commands.add_parser(
        "baseline", help="run the baseline agent against one workbook"
    )
    baseline.add_argument("workbook", type=Path)
    baseline.add_argument("--outputs", default=None)
    baseline.add_argument("--provider", default=None, choices=["groq", "openai"])
    baseline.add_argument("--traces", type=Path, default=Path("trajectories/baseline"))
    baseline.add_argument("--results", type=Path, default=None)
    baseline.add_argument(
        "--max-turns", type=int, default=_config("baseline", "max_turns", 67),
        help="defaults to the cap in config.yaml, which equals the solution's average",
    )
    baseline.add_argument(
        "--max-tokens", type=int, default=_config("baseline", "max_tokens", 211_000)
    )
    baseline.set_defaults(handler=_baseline)

    trace = commands.add_parser("trace", help="read and index agent trajectories")
    trace_actions = trace.add_subparsers(dest="trace_action", required=True)

    render_command = trace_actions.add_parser(
        "render", help="print one trajectory as readable markdown"
    )
    render_command.add_argument("trajectory", type=Path)
    render_command.set_defaults(handler=_trace_render)

    index_command = trace_actions.add_parser(
        "index", help="render the featured trajectories and write the index"
    )
    index_command.add_argument("--directory", type=Path, default=Path("trajectories"))
    index_command.set_defaults(handler=_trace_index)

    llm = commands.add_parser("llm", help="check the configured model provider")
    llm_actions = llm.add_subparsers(dest="llm_action", required=True)
    check_model = llm_actions.add_parser(
        "check", help="make one trivial call to confirm the model id is real"
    )
    check_model.add_argument("--provider", default=None, choices=["groq", "openai"])
    check_model.set_defaults(handler=_llm_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    handler = getattr(arguments, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1
    return handler(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
