# Seller performance workbook (Excel): build guide

A monthly seller-performance workbook fed by **Power Query** from the warehouse marts, with
**PivotTables**, **XLOOKUP** and conditional formatting, and a total that reconciles to the Power
BI report. Excel 365 or 2021+ is needed for XLOOKUP. Build time is about an hour.

Save it as `excel/seller-performance.xlsx`.

## 1. Data: Power Query

On the pipeline machine, `make export` writes `exports/mart_seller_monthly.csv` (plus every
other mart). Copy the `exports` folder to wherever the workbook lives.

Data > Get Data > From File > From Text/CSV > `mart_seller_monthly.csv` > **Transform Data**:

1. Types: `month` Date; `orders`, `items`, `delivered_orders`, `late_orders` Whole Number;
   `revenue` Currency; `avg_review_score` Decimal
2. Add Column > Custom Column `late_rate` =
   `if [delivered_orders] = 0 then null else [late_orders] / [delivered_orders]`
3. Add Column > Custom Column `year_month` = `Date.ToText([month], "yyyy-MM")`
4. Rename the query `SellerMonthly` > Close & Load To > **Table** on a new sheet named `Data`

Second query, for the lookup sheet: right-click `SellerMonthly` > Reference, then
Group By `seller_id`, `seller_state`, `seller_city` with Sum of revenue, Sum of orders,
Sum of delivered_orders, Sum of late_orders, Average of avg_review_score. Add `late_rate` as in
step 2. Name it `SellerTotals` and load it as a table on sheet `Sellers`.

Refreshing later: Data > Refresh All re-reads the CSVs. The applied steps are replayed, so the
workbook never needs manual edits after new data arrives.

## 2. PivotTables (sheet `Pivot`)

Insert > PivotTable > from table `SellerMonthly`:

- **Pivot 1, revenue by state and month:** Rows `seller_state`, Columns `year_month`, Values Sum
  of revenue. Insert > Slicer on `seller_state`; Insert > Timeline on `month`
- **Pivot 2, top sellers:** Rows `seller_id`, Values Sum of revenue, Sum of orders, Average of
  avg_review_score. Value filter: Top 10 by revenue
- **Pivot 3, late deliveries by state:** Rows `seller_state`; Values Sum of late_orders and Sum of
  delivered_orders, plus a calculated field `Late %` = `late_orders / delivered_orders`
  (PivotTable Analyze > Fields, Items & Sets > Calculated Field), formatted as %

## 3. Scorecard with XLOOKUP (sheet `Scorecard`)

A one-seller scorecard. Type a seller id and everything fills in:

| Cell | Content |
|---|---|
| B2 | seller id (Data > Data Validation > List, source `=SellerTotals[seller_id]`) |
| B4 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[seller_state], "not found")` |
| B5 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[seller_city], "")` |
| B6 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Sum of revenue], 0)` |
| B7 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Sum of orders], 0)` |
| B8 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[late_rate], "")` |
| B9 | `=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Average of avg_review_score], "")` |
| B10 | rank: `=COUNTIF(SellerTotals[Sum of revenue], ">" & B6) + 1` |
| B11 | state rank: `=COUNTIFS(SellerTotals[seller_state], B4, SellerTotals[Sum of revenue], ">" & B6) + 1` |

Monthly trend below it, using dynamic arrays:
`=FILTER(SellerMonthly[[year_month]:[revenue]], SellerMonthly[seller_id] = $B$2)`.
Add a line chart over that spill range.

## 4. Conditional formatting

- `Sellers[late_rate]`: Home > Conditional Formatting > Highlight Cells > Greater Than `0.1`,
  light red fill
- `Sellers[Average of avg_review_score]`: 3-color scale (red low, green high)
- Scorecard B8: a rule with formula `=$B$8>0.1`, red bold; B9: `=$B$9<3.5`, red bold
- `Data[revenue]`: data bars

## 5. Reconciliation

On `Scorecard`, add:

| Cell | Content |
|---|---|
| E2 | `Total revenue (workbook)` |
| F2 | `=SUM(SellerMonthly[revenue])` |
| E3 | `Total revenue (Power BI)` |
| F3 | type the Revenue card value from the Power BI Overview page |
| E4 | `Check` |
| F4 | `=IF(ROUND(F2-F3, 2) = 0, "OK", "MISMATCH")`, with conditional format green/red |

`mart_seller_monthly` excludes canceled and unavailable orders, exactly like the Power BI
`Revenue` measure, so the two must match to the cent.

## What to be able to say

- **Why Power Query and not copy-paste?** The steps are recorded and replayed on refresh, so the
  workbook is repeatable and auditable, and the same transformations are never typed twice.
- **XLOOKUP vs VLOOKUP.** XLOOKUP looks left or right, defaults to exact match, has a built-in
  "not found" value, and doesn't break when a column is inserted.
- **Why the workbook reads a mart instead of raw tables.** The joins and business rules (which
  orders count, what "late" means) live once in dbt, are tested there, and every tool (Power BI,
  Metabase, Excel) reads the same numbers.
