"""The published coverage matrix against the records of the runs. No Azure access."""

import re

from tests.full_matrix.published import EVIDENCE, published, records, rendered


def test_the_published_matrix_is_the_rendering_of_the_records_of_the_runs():
    assert published() == rendered(records())


def test_no_record_names_a_subscription_or_holds_a_token():
    for record in EVIDENCE.glob("*.json"):
        written = record.read_text(encoding="utf-8")
        assert not re.search(r"/subscriptions/[0-9a-fA-F]{8}-", written), record.name
        assert not re.search(r"eyJ[\w-]{10,}\.[\w-]+\.", written), record.name
        assert "Bearer " not in written, record.name
