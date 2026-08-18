"""Backend-neutral access to the canonical translation store."""

from .detect import (
    DEFAULT_NEW_PROFILE_STORE_FORMAT,
    create_translation_store,
    detect_store_format,
    open_translation_store,
)
from .migration import execute_store_migration
from .models import (
    CompatibilityTranslationStoreRepository,
    MaterializedStoreRecord,
    MaterializedStoreSnapshot,
    StoreCommitResult,
    StoreFormat,
    StoreMigrationPlan,
    StoreMigrationResult,
    TranslationStoreRepository,
    materialize_compatibility_store,
    write_materialized_compatibility_store,
)

__all__ = [
    "detect_store_format",
    "DEFAULT_NEW_PROFILE_STORE_FORMAT",
    "create_translation_store",
    "execute_store_migration",
    "open_translation_store",
    "CompatibilityTranslationStoreRepository",
    "MaterializedStoreRecord",
    "MaterializedStoreSnapshot",
    "StoreCommitResult",
    "StoreFormat",
    "StoreMigrationPlan",
    "StoreMigrationResult",
    "TranslationStoreRepository",
    "materialize_compatibility_store",
    "write_materialized_compatibility_store",
]
