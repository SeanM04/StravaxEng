"""Claude Code PostToolUse hook: lint an edited Python file with pylint.

Reads the hook payload (JSON) from stdin, runs pylint, including the Google
docstring checks configured in ``.pylintrc``, on the file Claude just wrote
or edited, and, if anything is reported, prints a ``"decision": "block"``
response so the findings are fed back to Claude to fix.

Files outside the project, non-``.py`` files, migrations and the virtualenv
are skipped.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
SKIP_DIRS = {".venv", "venv", "migrations", "__pycache__"}


def _target_file(payload: dict) -> Path | None:
    """Return the project-relative ``.py`` file to lint, or ``None`` to skip.

    Args:
        payload: The PostToolUse hook payload.

    Returns:
        Path relative to the project root, or ``None`` if the edited file is not
        a project Python file that should be linted.
    """
    file_path = (payload.get("tool_input") or {}).get("file_path") or (
        payload.get("tool_response") or {}
    ).get("filePath")
    if not file_path:
        return None
    path = Path(file_path).resolve()
    if path.suffix != ".py" or not path.is_file():
        return None
    try:
        rel = path.relative_to(PROJECT_DIR.resolve())
    except ValueError:
        return None
    if SKIP_DIRS.intersection(rel.parts):
        return None
    return rel


def _run_pylint(rel: Path) -> tuple[list[dict], str]:
    """Run pylint on one file.

    Args:
        rel: File path relative to the project root.

    Returns:
        The reported messages (pylint ``json2`` format) and pylint's stderr.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "pylint",
            f"--rcfile={PROJECT_DIR / '.pylintrc'}",
            "--output-format=json2",
            str(rel),
        ],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # PYTHONPATH lets pylint-django import StravaxEng.settings for any file.
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(PROJECT_DIR)},
        check=False,
    )
    try:
        messages = json.loads(result.stdout).get("messages", [])
    except json.JSONDecodeError:
        messages = []
    return messages, result.stderr.strip()


def _format(messages: list[dict]) -> list[str]:
    """Format pylint messages as ``L<line> <id> <symbol>: <message>`` lines.

    Args:
        messages: Pylint ``json2`` message dicts.

    Returns:
        One formatted line per message, ordered by line number.
    """
    return [
        f"  L{m['line']} {m['messageId']} {m['symbol']}: {m['message']}"
        for m in sorted(messages, key=lambda m: (m["line"], m["column"]))
    ]


def main() -> int:
    """Hook entry point.

    Returns:
        Process exit code: 0 on success or skip, 1 if pylint could not run.
    """
    rel = _target_file(json.load(sys.stdin))
    if rel is None:
        return 0

    messages, stderr = _run_pylint(rel)
    if not messages:
        if stderr and "Traceback" in stderr:
            print(
                f"pylint hook could not lint {rel.as_posix()}:\n{stderr[-1500:]}",
                file=sys.stderr,
            )
            return 1
        return 0

    doc_msgs = [m for m in messages if "doc" in m["symbol"]]
    other_msgs = [m for m in messages if "doc" not in m["symbol"]]

    sections = [f"pylint reported {len(messages)} issue(s) in {rel.as_posix()}:"]
    if doc_msgs:
        sections.append("Docstrings (Google style: Args / Returns / Raises / Yields):")
        sections.extend(_format(doc_msgs))
    if other_msgs:
        sections.append("Pylint:")
        sections.extend(_format(other_msgs))
    sections.append(
        "Fix these. If a finding is a genuine false positive, silence it with a targeted "
        "`# pylint: disable=<symbol>` comment on that line and say why."
    )

    print(json.dumps({"decision": "block", "reason": "\n".join(sections)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
