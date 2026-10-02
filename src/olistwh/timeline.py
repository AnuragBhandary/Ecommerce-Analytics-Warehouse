"""The Olist CSVs are a static snapshot. This module turns them into a *timeline*: for every source
row it works out when the row first appears and when each of its later changes happens, in
business time (the dataset's own timestamps, 2016-09 to 2018-10).

`state_at(t)` then answers "what did the operational database look like at business time t?",
and `changed_between(a, b)` returns exactly the rows whose state differs between a and b. The
simulator only ever writes those rows, so MySQL's updated_at changes exactly when the business
data changes, which is what the incremental extractor relies on.

Lifecycles modelled:
  orders     created -> approved -> shipped -> delivered at the dataset's own timestamps; the
             order's final status (canceled, unavailable, invoiced, ...) applies at its last event
  reviews    survey sent (score empty) -> answered (score and comments filled in)
  customers  one row per real person (customer_unique_id); the address changes when a later
             order ships somewhere new, which is what the SCD2 snapshot captures
  products, sellers   appear with the first order that references them
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

STAGES = ("created", "approved", "shipped", "delivered")
STAGE_COLS = ("purchased_at", "approved_at", "delivered_carrier_at", "delivered_customer_at")
EPOCH = pd.Timestamp("2016-01-01")


def _read(data_dir: Path, name: str, **kw: object) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{name}.csv", keep_default_na=True, **kw)  # type: ignore[call-overload]


def _ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce")


@dataclass
class Timeline:
    orders: pd.DataFrame
    items: pd.DataFrame
    payments: pd.DataFrame
    reviews: pd.DataFrame
    customer_versions: pd.DataFrame
    products: pd.DataFrame
    sellers: pd.DataFrame
    translation: pd.DataFrame

    @property
    def start(self) -> pd.Timestamp:
        return pd.Timestamp(self.orders["purchased_at"].min())

    @property
    def end(self) -> pd.Timestamp:
        return pd.Timestamp(
            max(self.orders["last_event_at"].max(), self.reviews["answer_visible_at"].max())
        )

    # ------------------------------------------------------------------ loading

    @classmethod
    def load(cls, data_dir: Path) -> Timeline:
        zip_dtype = {"customer_zip_code_prefix": str, "seller_zip_code_prefix": str}
        o = _read(data_dir, "olist_orders_dataset")
        c = _read(data_dir, "olist_customers_dataset", dtype=zip_dtype)
        items = _read(data_dir, "olist_order_items_dataset")
        pay = _read(data_dir, "olist_order_payments_dataset")
        rev = _read(data_dir, "olist_order_reviews_dataset")
        prod = _read(data_dir, "olist_products_dataset")
        sel = _read(data_dir, "olist_sellers_dataset", dtype=zip_dtype)
        tr = _read(data_dir, "product_category_name_translation")
        return cls.build(o, c, items, pay, rev, prod, sel, tr)

    @classmethod
    def build(
        cls,
        o: pd.DataFrame,
        c: pd.DataFrame,
        items: pd.DataFrame,
        pay: pd.DataFrame,
        rev: pd.DataFrame,
        prod: pd.DataFrame,
        sel: pd.DataFrame,
        tr: pd.DataFrame,
    ) -> Timeline:
        o = o.merge(c, on="customer_id", how="left")
        orders = pd.DataFrame(
            {
                "order_id": o["order_id"],
                "customer_id": o["customer_id"],
                "customer_unique_id": o["customer_unique_id"],
                "final_status": o["order_status"],
                "purchased_at": _ts(o["order_purchase_timestamp"]),
                "approved_at": _ts(o["order_approved_at"]),
                "delivered_carrier_at": _ts(o["order_delivered_carrier_date"]),
                "delivered_customer_at": _ts(o["order_delivered_customer_date"]),
                "estimated_delivery_date": _ts(o["order_estimated_delivery_date"]).dt.date,
                "zip_code_prefix": o["customer_zip_code_prefix"].str.zfill(5),
                "city": o["customer_city"],
                "state": o["customer_state"],
            }
        )
        # When each lifecycle stage becomes visible. A handful of rows have stages out of order
        # (carrier date before approval); taking the running max keeps every order's history
        # monotonic, so a stage is never reached before the one it follows.
        raw = orders[list(STAGE_COLS)]
        ns = np.stack([raw[c].to_numpy("datetime64[s]").astype("float64") for c in STAGE_COLS], 1)
        ns[raw.isna().to_numpy()] = np.nan
        reach = np.fmax.accumulate(ns, axis=1)  # running max that skips missing stages
        reach[raw.isna().to_numpy()] = np.nan
        for i, stage in enumerate(STAGES):
            orders[f"reach_{stage}"] = pd.to_datetime(reach[:, i], unit="s")
        orders["last_event_at"] = pd.to_datetime(np.nanmax(reach, axis=1), unit="s")

        items = items.rename(columns={"shipping_limit_date": "shipping_limit_at"})
        items["shipping_limit_at"] = _ts(items["shipping_limit_at"])
        pay = pay.rename(columns={"payment_installments": "installments"})

        reviews = pd.DataFrame(
            {
                "review_id": rev["review_id"],
                "order_id": rev["order_id"],
                "score": rev["review_score"],
                "comment_title": rev["review_comment_title"],
                "comment_message": rev["review_comment_message"],
                "sent_at": _ts(rev["review_creation_date"]),
                "answered_at": _ts(rev["review_answer_timestamp"]),
            }
        ).drop_duplicates(["review_id", "order_id"])
        reviews["answered_at"] = reviews[["sent_at", "answered_at"]].max(axis=1)
        # 74 surveys are dated before their order was even placed (up to 111 days). The stored
        # timestamps stay as they are (dbt flags them with a warning test), but a review can't
        # exist in the source before its order, so it becomes visible at the purchase at the
        # earliest. Found by the full-data proof run: a tick boundary fell in between.
        purchased = reviews["order_id"].map(orders.set_index("order_id")["purchased_at"])
        reviews["visible_at"] = pd.concat([reviews["sent_at"], purchased], axis=1).max(axis=1)
        reviews["answer_visible_at"] = reviews[["answered_at", "visible_at"]].max(axis=1)

        # Customer address history: one version per run of consecutive orders shipped to the
        # same address, effective from the purchase that introduced it.
        oc = orders.sort_values(["customer_unique_id", "purchased_at", "order_id"])
        addr = oc["zip_code_prefix"] + "|" + oc["city"] + "|" + oc["state"]
        changed = (addr != addr.groupby(oc["customer_unique_id"]).shift()).to_numpy()
        versions = oc.loc[
            changed, ["customer_unique_id", "zip_code_prefix", "city", "state", "purchased_at"]
        ].rename(columns={"purchased_at": "address_updated_at"})

        # Products and sellers appear with the first order that sells them.
        first_seen = items.merge(orders[["order_id", "purchased_at"]], on="order_id")
        p_first = first_seen.groupby("product_id")["purchased_at"].min().rename("appears_at")
        s_first = first_seen.groupby("seller_id")["purchased_at"].min().rename("appears_at")
        products = prod.rename(
            columns={
                "product_category_name": "category_name",
                "product_name_lenght": "name_length",
                "product_description_lenght": "description_length",
                "product_photos_qty": "photos_qty",
                "product_weight_g": "weight_g",
                "product_length_cm": "length_cm",
                "product_height_cm": "height_cm",
                "product_width_cm": "width_cm",
            }
        ).merge(p_first, left_on="product_id", right_index=True, how="inner")
        sellers = sel.rename(
            columns={
                "seller_zip_code_prefix": "zip_code_prefix",
                "seller_city": "city",
                "seller_state": "state",
            }
        ).merge(s_first, left_on="seller_id", right_index=True, how="inner")
        sellers["zip_code_prefix"] = sellers["zip_code_prefix"].str.zfill(5)

        translation = tr.rename(
            columns={
                "product_category_name": "category_name",
                "product_category_name_english": "category_name_english",
            }
        )
        return cls(orders, items, pay, reviews, versions, products, sellers, translation)

    # ------------------------------------------------------------------ state

    def order_state(self, t: pd.Timestamp, rows: pd.DataFrame | None = None) -> pd.DataFrame:
        o = self.orders if rows is None else rows
        o = o[o["purchased_at"] <= t]
        status = pd.Series("created", index=o.index, dtype=object)
        out = pd.DataFrame(
            {
                "order_id": o["order_id"],
                "customer_id": o["customer_id"],
                "customer_unique_id": o["customer_unique_id"],
            }
        )
        for stage, col in zip(STAGES, STAGE_COLS, strict=True):
            reached = o[f"reach_{stage}"] <= t
            status = status.mask(reached, stage)
            out[col] = o[col].where(o[f"reach_{stage}"] <= t)
        done = o["last_event_at"] <= t
        out.insert(3, "order_status", status.mask(done, o["final_status"]))
        out["estimated_delivery_date"] = o["estimated_delivery_date"]
        return out

    def review_state(self, t: pd.Timestamp, rows: pd.DataFrame | None = None) -> pd.DataFrame:
        r = self.reviews if rows is None else rows
        r = r[r["visible_at"] <= t].copy()
        unanswered = ~(r["answer_visible_at"] <= t)
        r.loc[unanswered, ["score", "comment_title", "comment_message", "answered_at"]] = None
        return r

    def customer_state(self, t: pd.Timestamp) -> pd.DataFrame:
        v = self.customer_versions[self.customer_versions["address_updated_at"] <= t]
        return v.sort_values("address_updated_at").drop_duplicates(
            "customer_unique_id", keep="last"
        )

    def _orders_purchased(self, a: pd.Timestamp, b: pd.Timestamp) -> pd.Series:
        p = self.orders["purchased_at"]
        return self.orders.loc[(p > a) & (p <= b), "order_id"]

    def changed_between(self, a: pd.Timestamp, b: pd.Timestamp) -> dict[str, pd.DataFrame]:
        """Rows whose state at b differs from their state at a, as they look at b."""

        def hit(col: pd.Series) -> np.ndarray:
            return ((col > a) & (col <= b)).to_numpy()

        o = self.orders
        touched = np.zeros(len(o), dtype=bool)
        for stage in STAGES:
            touched |= hit(o[f"reach_{stage}"])
        touched |= hit(o["last_event_at"])
        new_orders = set(self._orders_purchased(a, b))

        r = self.reviews
        rtouched = hit(r["visible_at"]) | hit(r["answer_visible_at"])

        cv = self.customer_versions
        cust_ids = cv.loc[hit(cv["address_updated_at"]), "customer_unique_id"].unique()
        customers = self.customer_state(b)
        customers = customers[customers["customer_unique_id"].isin(cust_ids)]

        return {
            "category_translation": self.translation if a < self.start else self.translation[:0],
            "customers": customers,
            "sellers": self.sellers[hit(self.sellers["appears_at"])],
            "products": self.products[hit(self.products["appears_at"])],
            "orders": self.order_state(b, o[touched]),
            "order_items": self.items[self.items["order_id"].isin(new_orders)],
            "order_payments": self.payments[self.payments["order_id"].isin(new_orders)],
            "order_reviews": self.review_state(b, r[rtouched]),
        }
