"""SQLAlchemy declarative base, metadata, and domain models."""

from sentinel_prism.db.models import Base, metadata


def test_base_and_metadata_share_registry() -> None:
    assert metadata is Base.metadata


def test_users_table_registered() -> None:
    assert "users" in Base.metadata.tables
    table = Base.metadata.tables["users"]
    assert "email" in table.c
    assert "password_hash" in table.c
    assert "role" in table.c
