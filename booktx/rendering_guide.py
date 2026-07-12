"""Human and JSON renderers for ``booktx guide``."""

from __future__ import annotations

import json

from rich.console import Console

from booktx.human_guide import GuideResult

console = Console()


def print_guide_human(result: GuideResult) -> None:
    console.print(f"Lifecycle stage: {result.stage.upper()}")
    console.print()
    if result.profile:
        console.print(f"Profile: {result.profile}")
        console.print()
    if result.human_blockers:
        console.print("Human blockers:")
        for blocker in result.human_blockers:
            console.print(f"  - {blocker}")
        console.print()
    console.print("Human action:")
    if result.human_next is None:
        console.print("  None.")
    else:
        console.print(f"  {result.human_next.summary}")
        if result.human_next.command:
            console.print("  Command:")
            console.print(
                f"    {result.human_next.command}", soft_wrap=True, markup=False
            )
    console.print()
    console.print("Agent action:")
    if result.agent_next is None:
        console.print("  None.")
    else:
        console.print(f"  {result.agent_next.summary}")
        if result.agent_next.command:
            console.print("  Command:")
            console.print(
                f"    {result.agent_next.command}", soft_wrap=True, markup=False
            )
    if result.warnings:
        console.print()
        console.print("Warnings:")
        for warning in result.warnings:
            console.print(f"  - {warning}")


def print_guide_json(result: GuideResult) -> None:
    console.print_json(json.dumps(result.to_payload(), ensure_ascii=False))
