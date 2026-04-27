"""Fix EMA RSS feed URL (404 → correct /rss/news.xml path).

The EMA website reorganised its RSS paths; the old /en/rss.xml endpoint
now returns HTTP 404, causing every scheduled EMA poll to fail.  This
migration updates the primary_url to the current working feed path so
the RSS connector can fetch items again.

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-04-27

"""

from typing import Sequence, Union

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_URL = "https://www.ema.europa.eu/en/rss.xml"
_NEW_URL = "https://www.ema.europa.eu/en/rss/news.xml"


def upgrade() -> None:
    op.execute(
        f"UPDATE sources SET primary_url = '{_NEW_URL}' "
        f"WHERE primary_url = '{_OLD_URL}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE sources SET primary_url = '{_OLD_URL}' "
        f"WHERE primary_url = '{_NEW_URL}'"
    )
