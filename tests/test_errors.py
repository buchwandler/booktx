"""Regression tests for the booktx error helper."""

from __future__ import annotations

from booktx.errors import BooktxError, _err


def test_err_single_message_round_trips():
    err = _err("judge_next", "no missing records remain")
    assert isinstance(err, BooktxError)
    assert err.code == "judge_next"
    assert str(err) == "no missing records remain"


def test_err_concatenates_multiple_message_fragments():
    # Call sites in booktx/judge_acceptance.py split long messages across
    # several adjacent string literals; _err must concatenate them in order.
    err = _err(
        "judge_block_boundary_corrupt",
        "record r1 TARGET appears to contain the next record header; ",
        "reset the ingest file with ",
        "`booktx judge reset-ingest . --judge-task-id t1 ",
        "--format decisions --write`",
    )
    assert err.code == "judge_block_boundary_corrupt"
    assert str(err) == (
        "record r1 TARGET appears to contain the next record header; "
        "reset the ingest file with "
        "`booktx judge reset-ingest . --judge-task-id t1 "
        "--format decisions --write`"
    )
