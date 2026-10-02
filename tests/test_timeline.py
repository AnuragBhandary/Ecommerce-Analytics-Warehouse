"""Unit tests for the business-time model of the source (no database needed)."""

from __future__ import annotations

import random

import pandas as pd
import pytest

from olistwh.tables import BY_NAME
from olistwh.timeline import Timeline

T = pd.Timestamp


def _tiny() -> Timeline:
    orders = pd.DataFrame(
        {
            "order_id": ["o1", "o2", "o3"],
            "customer_id": ["c1", "c2", "c3"],
            "order_status": ["delivered", "canceled", "delivered"],
            "order_purchase_timestamp": [
                "2017-01-01 10:00",
                "2017-01-02 10:00",
                "2017-03-01 09:00",
            ],
            "order_approved_at": ["2017-01-01 12:00", "2017-01-02 11:00", "2017-03-01 10:00"],
            # o3's carrier date is *before* its approval: the timeline must keep it monotonic.
            "order_delivered_carrier_date": ["2017-01-03 08:00", None, "2017-03-01 09:30"],
            "order_delivered_customer_date": ["2017-01-08 15:00", None, "2017-03-05 15:00"],
            "order_estimated_delivery_date": ["2017-01-10", "2017-01-20", "2017-03-04"],
        }
    )
    customers = pd.DataFrame(
        {
            # u1 places o1 and o3 from different addresses: one address change.
            "customer_id": ["c1", "c2", "c3"],
            "customer_unique_id": ["u1", "u2", "u1"],
            "customer_zip_code_prefix": ["01001", "02002", "03003"],
            "customer_city": ["sao paulo", "rio", "campinas"],
            "customer_state": ["SP", "RJ", "SP"],
        }
    )
    items = pd.DataFrame(
        {
            "order_id": ["o1", "o1", "o3"],
            "order_item_id": [1, 2, 1],
            "product_id": ["p1", "p2", "p1"],
            "seller_id": ["s1", "s1", "s2"],
            "shipping_limit_date": ["2017-01-05", "2017-01-05", "2017-03-03"],
            "price": [10.0, 20.0, 30.0],
            "freight_value": [1.0, 2.0, 3.0],
        }
    )
    pay = pd.DataFrame(
        {
            "order_id": ["o1", "o2"],
            "payment_sequential": [1, 1],
            "payment_type": ["boleto"] * 2,
            "payment_installments": [1, 1],
            "payment_value": [33.0, 5.0],
        }
    )
    rev = pd.DataFrame(
        {
            "review_id": ["r1"],
            "order_id": ["o1"],
            "review_score": [4],
            "review_comment_title": [None],
            "review_comment_message": ["ok"],
            "review_creation_date": ["2017-01-09"],
            "review_answer_timestamp": ["2017-01-11 08:00"],
        }
    )
    prod = pd.DataFrame(
        {
            "product_id": ["p1", "p2", "p_unsold"],
            "product_category_name": ["a", "b", "c"],
            **{
                k: [1, 1, 1]
                for k in [
                    "product_name_lenght",
                    "product_description_lenght",
                    "product_photos_qty",
                    "product_weight_g",
                    "product_length_cm",
                    "product_height_cm",
                    "product_width_cm",
                ]
            },
        }
    )
    sel = pd.DataFrame(
        {
            "seller_id": ["s1", "s2"],
            "seller_zip_code_prefix": ["1", "2"],
            "seller_city": ["x", "y"],
            "seller_state": ["SP", "SP"],
        }
    )
    tr = pd.DataFrame({"product_category_name": ["a"], "product_category_name_english": ["A"]})
    return Timeline.build(orders, customers, items, pay, rev, prod, sel, tr)


def _status(tl: Timeline, order_id: str, t: str) -> str | None:
    s = tl.order_state(T(t))
    row = s[s["order_id"] == order_id]
    return None if row.empty else str(row["order_status"].iloc[0])


def test_order_moves_through_its_lifecycle() -> None:
    tl = _tiny()
    assert _status(tl, "o1", "2017-01-01 09:59") is None
    assert _status(tl, "o1", "2017-01-01 10:00") == "created"
    assert _status(tl, "o1", "2017-01-02") == "approved"
    assert _status(tl, "o1", "2017-01-04") == "shipped"
    assert _status(tl, "o1", "2017-01-09") == "delivered"


def test_timestamps_appear_only_once_reached() -> None:
    tl = _tiny()
    row = tl.order_state(T("2017-01-02")).set_index("order_id").loc["o1"]
    assert row["approved_at"] == T("2017-01-01 12:00")
    assert pd.isna(row["delivered_carrier_at"]) and pd.isna(row["delivered_customer_at"])


def test_final_status_applies_at_last_event() -> None:
    tl = _tiny()
    assert _status(tl, "o2", "2017-01-02 10:30") == "created"
    assert _status(tl, "o2", "2017-01-02 11:00") == "canceled"


def test_out_of_order_stages_stay_monotonic() -> None:
    tl = _tiny()
    # o3: carrier 09:30 is before approval 10:00, so "shipped" can't be reached before 10:00.
    assert _status(tl, "o3", "2017-03-01 09:45") == "created"
    assert _status(tl, "o3", "2017-03-01 10:00") == "shipped"


def test_customer_address_history() -> None:
    tl = _tiny()
    assert len(tl.customer_versions) == 3  # u1 twice, u2 once
    before = tl.customer_state(T("2017-02-01")).set_index("customer_unique_id")
    after = tl.customer_state(T("2017-03-02")).set_index("customer_unique_id")
    assert before.loc["u1", "zip_code_prefix"] == "01001"
    assert after.loc["u1", "zip_code_prefix"] == "03003"
    assert after.loc["u1", "address_updated_at"] == T("2017-03-01 09:00")


def test_review_score_hidden_until_answered() -> None:
    tl = _tiny()
    sent = tl.review_state(T("2017-01-10")).iloc[0]
    assert pd.isna(sent["score"]) and pd.isna(sent["answered_at"])
    answered = tl.review_state(T("2017-01-12")).iloc[0]
    assert answered["score"] == 4 and answered["comment_message"] == "ok"


def test_products_and_sellers_appear_with_first_sale() -> None:
    tl = _tiny()
    assert "p_unsold" not in set(tl.products["product_id"])
    ch = tl.changed_between(T("2017-02-01"), T("2017-03-02"))
    assert list(ch["sellers"]["seller_id"]) == ["s2"]
    assert list(ch["products"]["product_id"]) == []  # p1 was already sold in January


def test_nothing_changes_in_a_quiet_window() -> None:
    tl = _tiny()
    ch = tl.changed_between(T("2017-01-20"), T("2017-02-20"))
    assert all(df.empty for df in ch.values())


def _apply(state: dict[str, dict[tuple, tuple]], changes: dict[str, pd.DataFrame]) -> None:
    for table, df in changes.items():
        t = BY_NAME[table]
        for row in df[t.col_names].itertuples(index=False):
            d = row._asdict()
            state[table][tuple(d[k] for k in t.pk)] = tuple(
                None if pd.isna(v) else v for v in d.values()
            )


def _full_state(tl: Timeline, t: pd.Timestamp) -> dict[str, dict[tuple, tuple]]:
    state: dict[str, dict[tuple, tuple]] = {name: {} for name in BY_NAME}
    _apply(state, tl.changed_between(T("2000-01-01"), t))
    return state


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_replaying_in_random_ticks_equals_the_final_state(timeline: Timeline, seed: int) -> None:
    """The core simulator property: applying only the changed rows tick by tick, with arbitrary
    tick sizes, ends in exactly the state a single jump would produce."""
    rnd = random.Random(seed)
    end = timeline.end + pd.Timedelta(days=1)
    cuts = sorted(T(timeline.start) + (end - timeline.start) * rnd.random() for _ in range(40))
    state: dict[str, dict[tuple, tuple]] = {name: {} for name in BY_NAME}
    prev = T("2000-01-01")
    for cut in [*cuts, end]:
        _apply(state, timeline.changed_between(prev, cut))
        prev = cut
    assert state == _full_state(timeline, end)
    assert len(state["orders"]) == len(timeline.orders)


def test_review_never_visible_before_its_order(timeline: Timeline) -> None:
    """Found by the full-data proof run: some surveys are dated before the purchase."""
    purchased = timeline.orders.set_index("order_id")["purchased_at"]
    for t in pd.date_range(timeline.start, timeline.end, freq="7D"):
        visible = timeline.review_state(t)
        assert (visible["order_id"].map(purchased) <= t).all()
