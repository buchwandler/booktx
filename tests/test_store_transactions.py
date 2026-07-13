from __future__ import annotations

import pytest

from booktx.errors import BooktxError
from booktx.store.transactions import commit_v3_transaction, recover_v3_transactions


def test_recover_rolls_forward_after_journal_failure(tmp_path):
    root = tmp_path / "translation-store"
    transactions_root = root / "transactions"
    with pytest.raises(BooktxError):
        commit_v3_transaction(
            transactions_root=transactions_root,
            store_root=root,
            relative_to_text={"manifest.json": '{"version": 3, "format": "v3"}\n'},
            deletes=[],
            changed_chunk_ids=[],
            deleted_chunk_ids=[],
            changed_record_ids=[],
            wrote_manifest=True,
            fail_stage="after_journal_prepared",
        )

    assert not (root / "manifest.json").exists()
    recover_v3_transactions(transactions_root, root)
    assert (root / "manifest.json").is_file()
