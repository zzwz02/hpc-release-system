"""F1 — wiki timestamps are naive Beijing (matched pair: write path + migration).

Historically ``wiki_articles.created_at``/``updated_at`` stored UTC-ISO
(``2026-06-16T07:23:45+00:00``) while every other table stores naive Beijing
(``2026-06-16 15:23:45``).  ``app/services/wiki_service.py`` patches the
module-level ``wiki_core.now`` → ``beijing_timestamp`` for the duration of each
write call (save_article / set_pinned / delete_article) so new rows are written
in naive Beijing, WITHOUT editing the frozen ``release_system/wiki/core.py``.

These tests assert the write path produces the Beijing format (no ``+00:00`` /
``Z`` / ``T``) on every patched path and that the original ``now`` is restored
afterward (the monkeypatch must not leak).
"""
from __future__ import annotations

import re

from app.services import wiki_service
from release_system.wiki import core as wiki_core

# Naive Beijing format: "YYYY-MM-DD HH:MM:SS" exactly — no offset / 'Z' / 'T'.
_BEIJING_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


def _is_naive_beijing(val: str) -> bool:
    return bool(_BEIJING_RE.match(val or "")) and not any(
        marker in (val or "") for marker in ("+00:00", "Z", "T")
    )


def _create(conn, *, title="Beijing Wiki", body_md="# hello", pinned=False) -> dict:
    # wiki_core.save_article commits on its own connection — no transaction() wrapper.
    return wiki_service.create_or_update_article(
        conn,
        article_id=None,
        title=title,
        body_md=body_md,
        pinned=pinned,
        user="rm",
        role="RM",
    )


class TestWikiTimestampsBeijing:
    def test_new_article_columns_are_naive_beijing(self, temp_db):
        """created_at/updated_at written to the DB are naive Beijing, not UTC-ISO."""
        art = _create(temp_db)
        row = temp_db.execute(
            "SELECT created_at, updated_at FROM wiki_articles WHERE id=?",
            (art["id"],),
        ).fetchone()
        assert _is_naive_beijing(row["created_at"]), f"created_at={row['created_at']!r}"
        assert _is_naive_beijing(row["updated_at"]), f"updated_at={row['updated_at']!r}"

    def test_returned_dict_timestamps_are_naive_beijing(self, temp_db):
        art = _create(temp_db)
        assert _is_naive_beijing(art["created_at"]), f"created_at={art['created_at']!r}"
        assert _is_naive_beijing(art["updated_at"]), f"updated_at={art['updated_at']!r}"

    def test_update_refreshes_updated_at_in_beijing(self, temp_db):
        art = _create(temp_db)
        updated = wiki_service.create_or_update_article(
            temp_db,
            article_id=art["id"],
            title="changed",
            body_md="# changed",
            pinned=False,
            user="rm",
            role="RM",
        )
        assert _is_naive_beijing(updated["updated_at"]), updated["updated_at"]

    def test_set_pinned_writes_beijing_updated_at(self, temp_db):
        art = _create(temp_db)
        pinned = wiki_service.set_article_pinned(
            temp_db, art["id"], True, user="rm", role="RM"
        )
        assert _is_naive_beijing(pinned["updated_at"]), pinned["updated_at"]

    def test_delete_writes_beijing_timestamps(self, temp_db):
        art = _create(temp_db)
        wiki_service.delete_article(temp_db, art["id"], user="rm", role="RM")
        row = temp_db.execute(
            "SELECT deleted, deleted_at, updated_at FROM wiki_articles WHERE id=?",
            (art["id"],),
        ).fetchone()
        assert row["deleted"] == 1
        assert _is_naive_beijing(row["deleted_at"]), f"deleted_at={row['deleted_at']!r}"
        assert _is_naive_beijing(row["updated_at"]), f"updated_at={row['updated_at']!r}"

    def test_save_image_writes_beijing_uploaded_at(self, temp_db):
        """wiki_images.uploaded_at is also naive Beijing (N2: no wiki table in UTC)."""
        # 1x1 transparent PNG.
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
            "890000000a49444154789c6360000002000154a24f6e0000000049454e44ae42"
            "6082"
        )
        img = wiki_service.save_image(
            temp_db,
            content=png,
            filename="dot.png",
            content_type="image/png",
            user="rm",
            role="RM",
        )
        row = temp_db.execute(
            "SELECT uploaded_at FROM wiki_images WHERE id=?", (img["id"],)
        ).fetchone()
        assert _is_naive_beijing(row["uploaded_at"]), f"uploaded_at={row['uploaded_at']!r}"

    def test_now_restored_after_write(self, temp_db):
        """The monkeypatch must NOT leak: wiki_core.now is restored to the original."""
        orig = wiki_core.now
        _create(temp_db)
        assert wiki_core.now is orig

    def test_now_restored_even_on_error(self, temp_db):
        """If the write raises, the context manager still restores wiki_core.now."""
        orig = wiki_core.now
        try:
            # RM is a write role, so this enters the patched window; save_article
            # hits its "article not found" guard and raises KeyError from INSIDE
            # _use_beijing_time() — the finally block must still restore now.
            wiki_service.create_or_update_article(
                temp_db,
                article_id="wiki_does_not_exist",
                title="x",
                body_md="x",
                pinned=False,
                user="rm",
                role="RM",
            )
        except KeyError:
            pass
        assert wiki_core.now is orig
