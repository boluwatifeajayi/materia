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

    manifest = json.loads((arguments.corpus / "manifest.json").read_text())
    scores = [
        score(
            "Detectors only",
            detector_results(arguments.corpus, manifest),
            manifest,
        )
    ]

    written = write_results(scores, arguments.results)
    for name, path in written.items():
        print(f"{name}: {path}")

    if arguments.changelog:
        for item in scores:
            if update_changelog(arguments.changelog, "Iteration 1", item):
                print(f"changelog: Iteration 1 filled in {arguments.changelog}")
            else:
                print(
                    f"changelog: no Iteration 1 row in {arguments.changelog}",
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


def _audit(arguments: argparse.Namespace) -> int:
    from materia.audit import audit, write_result
    from materia.llm import ProviderError, get_client
    from materia.preflight import PreflightRejected

    try:
        client = get_client(arguments.provider)
    except ProviderError as error:
        print(error, file=sys.stderr)
        return 1

    outputs = (
        [item.strip() for item in arguments.outputs.split(",")]
        if arguments.outputs
        else None
    )

    try:
        result = audit(
            arguments.workbook,
            outputs=outputs,
            client=client,
            trace_directory=arguments.traces,
            max_candidates=arguments.max_candidates,
        )
    except PreflightRejected as rejection:
        print(f"{arguments.workbook} was not audited.", file=sys.stderr)
        print(f"  {rejection.message}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1

    print(result.render())

    if arguments.results:
        written = write_result(result, arguments.results)
        print(f"result written to {written}")
    if arguments.explain:
        print(_explain(result))
    return 0


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
    if arguments.explain:
        print(_explain(result))
    return 0


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
    evaluate.set_defaults(handler=_evaluate)

    audit_command = commands.add_parser("audit", help="audit one workbook")
    audit_command.add_argument("workbook", type=Path)
    audit_command.add_argument(
        "--outputs",
        default=None,
        help='declared output cells, comma separated, for example "P&L!AA15,Valuation!B7"',
    )
    audit_command.add_argument("--provider", default=None, choices=["groq", "anthropic"])
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
    audit_command.set_defaults(handler=_audit)

    report_command = commands.add_parser(
        "report", help="re-render a report from saved trajectories, no model calls"
    )
    report_command.add_argument("workbook", type=Path)
    report_command.add_argument("--traces", type=Path, default=Path("trajectories/solution"))
    report_command.add_argument("--outputs", default=None)
    report_command.add_argument("--explain", action="store_true")
    report_command.set_defaults(handler=_report)

    llm = commands.add_parser("llm", help="check the configured model provider")
    llm_actions = llm.add_subparsers(dest="llm_action", required=True)
    check_model = llm_actions.add_parser(
        "check", help="make one trivial call to confirm the model id is real"
    )
    check_model.add_argument("--provider", default=None, choices=["groq", "anthropic"])
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
