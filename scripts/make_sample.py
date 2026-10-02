"""Cuts a small, self-consistent sample of the Olist dataset for tests and CI.

Customer-complete: every order of each sampled customer is kept, along with every item, payment,
review, product and seller those orders touch. All customers who changed address are included,
so the SCD2 path is exercised.

    uv run python scripts/make_sample.py data tests/fixtures/olist_sample

Olist data: CC BY-NC-SA 4.0, (c) Olist, https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def main(src: Path, dst: Path, n_customers: int = 1500, seed: int = 7) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    read = {
        n: pd.read_csv(src / f"{n}.csv", dtype=str, keep_default_na=False)
        for n in (
            "olist_orders_dataset",
            "olist_customers_dataset",
            "olist_order_items_dataset",
            "olist_order_payments_dataset",
            "olist_order_reviews_dataset",
            "olist_products_dataset",
            "olist_sellers_dataset",
            "product_category_name_translation",
        )
    }
    c = read["olist_customers_dataset"]
    movers = c.groupby("customer_unique_id")["customer_zip_code_prefix"].nunique()
    movers = set(movers[movers > 1].index)
    others = c.loc[~c["customer_unique_id"].isin(movers), "customer_unique_id"].drop_duplicates()
    keep = movers | set(others.sample(n_customers, random_state=seed))

    c = c[c["customer_unique_id"].isin(keep)]
    o = read["olist_orders_dataset"]
    o = o[o["customer_id"].isin(c["customer_id"])]
    ids = set(o["order_id"])
    items = read["olist_order_items_dataset"]
    items = items[items["order_id"].isin(ids)]
    out = {
        "olist_orders_dataset": o,
        "olist_customers_dataset": c,
        "olist_order_items_dataset": items,
        "olist_order_payments_dataset": read["olist_order_payments_dataset"].query(
            "order_id in @ids"
        ),
        "olist_order_reviews_dataset": read["olist_order_reviews_dataset"].query(
            "order_id in @ids"
        ),
        "olist_products_dataset": read["olist_products_dataset"].query(
            "product_id in @items.product_id"
        ),
        "olist_sellers_dataset": read["olist_sellers_dataset"].query(
            "seller_id in @items.seller_id"
        ),
        "product_category_name_translation": read["product_category_name_translation"],
    }
    for name, df in out.items():
        df.to_csv(dst / f"{name}.csv", index=False)
        print(f"{name:40s} {len(df):>6,}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
