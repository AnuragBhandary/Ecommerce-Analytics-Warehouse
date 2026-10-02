# Power BI report: build guide

A 4-page Power BI report on the warehouse's star schema: **33 DAX measures** (YoY/MoM time
intelligence, running totals, cohort retention, on-time delivery rate), a drill-through page per
seller, and **row-level security** by seller state.

Power BI Desktop is free but **Windows only**. Total build time is about half a day the first
time. Commit the result as a `.pbip` project (step 9) so it diffs in git like code.

## 1. Get the data

**Option A, live warehouse (recommended).** With the Docker stack running on the same machine:

1. Home > Get data > PostgreSQL database
2. Server `localhost:5433`, Database `warehouse`, Data Connectivity mode **Import**
3. Credentials: Database tab, user `bi_reader`, password `bi_reader` (the read-only BI role;
   it can only see `marts`)

**Option B, CSV, no Docker on Windows.** On the machine that runs the pipeline:
`make export` writes every mart to `exports/*.csv`. Copy the folder, then
Get data > Folder (or Text/CSV per file).

Load these tables (and nothing from raw or staging):
`dim_date, dim_customer, dim_product, dim_seller, fct_orders, fct_order_items, fct_payments,
fct_reviews, mart_seller_monthly, mart_customer_cohorts`.

In Power Query, check types: every `*_key` column is Whole number, `date` / `month` / `month_start`
are Date, money columns are Fixed decimal number. Close & Apply.

## 2. Date table

Table view > `dim_date` > Table tools > **Mark as date table** > column `date`.
Sort `month_name` by `month` (Column tools > Sort by column), or month axes sort alphabetically.

## 3. Relationships (Model view)

Delete anything Power BI auto-detected, then create:

| From (many) | To (one) | Notes |
|---|---|---|
| fct_orders[purchase_date_key] | dim_date[date_key] | |
| fct_orders[customer_sk] | dim_customer[customer_sk] | SCD2: the address at purchase time |
| fct_order_items[purchase_date_key] | dim_date[date_key] | |
| fct_order_items[product_sk] | dim_product[product_sk] | |
| fct_order_items[seller_sk] | dim_seller[seller_sk] | |
| fct_reviews[order_id] | fct_orders[order_id] | single direction |
| fct_reviews[sent_date_key] | dim_date[date_key] | **inactive** (used by `Reviews Sent`) |
| fct_payments[order_id] | fct_orders[order_id] | single direction |
| mart_seller_monthly[month] | dim_date[date] | |
| mart_customer_cohorts[cohort_month] | dim_date[date] | |

Every relationship is single-direction from the dimension to the fact. Do **not** link
fct_order_items to fct_orders: both are tied to dim_date, so that would give dim_date two paths
to the items table and Power BI rejects it as ambiguous. Be ready to explain that in an
interview.

Hide every `_sk`, `_key` and `_loaded_at` column (right-click > Hide in report view).

## 4. Measures

Create a measure table `_Measures` (Home > Enter data, OK, then delete its column) and paste each
measure from [`measures.dax`](measures.dax) with **New measure**. Set formats:
Revenue-type measures as currency R$ with 0 decimals; `%` measures as Percentage, 1 decimal.

## 5. Pages

**Page 1: Overview**
- Cards: Revenue, Orders, Average Order Value, Customers, On-time Delivery %, Avg Review Score
- Line chart: dim_date[month_start] × Revenue, with Revenue PY as a second line
- Bar chart: dim_product[category] × Revenue (Top N filter = 10)
- Slicers: dim_date[year], dim_customer[state]

**Page 2: Sales trends**
- Line and clustered column: month_start × Revenue (columns) and Revenue MoM % (line)
- Matrix: rows year, month_name; values Revenue, Revenue PY, Revenue YoY %, Revenue YTD
  (conditional formatting on YoY %: red-to-green background)
- Line chart: Revenue Running Total, Revenue 3M Moving Avg
- Donut: fct_payments[payment_type] × Payment Value, plus a Voucher Share % card

**Page 3: Delivery and reviews**
- Cards: On-time Delivery %, Avg Delivery Days, Avg Days Late (late orders)
- Line chart: month_start × On-time Delivery %
- Clustered bar: Avg Review Score (on time) vs Avg Review Score (late), which is the headline
  insight: late orders score much lower
- Filled map or bar: dim_customer[state] × On-time Delivery %
- Matrix: mart_customer_cohorts[cohort_month] rows, [months_since_first] columns,
  Cohort Retention % values, with background color scale (the classic cohort triangle)

**Page 4: Seller detail (drill-through)**
- Format pane > Page information > Page type **Drill through**; drill-through field
  `mart_seller_monthly[seller_id]`; keep "Keep all filters" on
- Cards: Seller Revenue, Seller Revenue Rank, Seller Late Rate %, average of avg_review_score
- Line chart: month × Seller Revenue
- Table: month, orders, revenue, late_orders, avg_review_score

Then, on page 3 (or a seller table on page 1), right-click any seller > **Drill through > Seller
detail**. A Back button appears automatically.

## 6. Row-level security (by seller state)

Modeling > **Manage roles** > New role, for example `Seller region SP`:

- table `dim_seller`, DAX filter: `[state] = "SP"`
- table `mart_seller_monthly`, DAX filter: `[seller_state] = "SP"`

Repeat for `RJ` and `MG`. Test with **Modeling > View as > Seller region SP**: seller revenue
drops to São Paulo sellers only, while customer-side visuals are unaffected.

Dynamic version, for an interview follow-up: a `user_region` table (email, state) related to
dim_seller[state], with the filter `[email] = USERPRINCIPALNAME()`. One role instead of one per
state.

## 7. Check the numbers

Revenue on page 1 (all years, no slicers) must equal the warehouse:

```sql
select sum(gross_revenue) from marts.fct_orders
where order_status not in ('canceled', 'unavailable');
```

That is the same figure as the Metabase "Revenue" card and the Excel workbook's total.

## 8. Screenshots and PDF

File > Export > Export to PDF > `powerbi/olist-report.pdf`. Also save one PNG per page into
`powerbi/screenshots/`. The README links them, because a recruiter can't open a .pbix.

## 9. Save as a project

File > Save as > **Power BI project (.pbip)** into `powerbi/olist-report/`. The model is saved as
TMDL text, so measure changes show up as readable git diffs. Commit that folder, the PDF and the
screenshots.

## What to be able to say

- **Why Import, not DirectQuery?** 100k orders fit in memory easily, and Import gives full DAX
  and fast visuals. DirectQuery makes sense for data too big or too fresh for scheduled refresh.
- **Filter context vs row context.** `Repeat Customer %` iterates customers (row context), and
  `CALCULATE` turns that into a filter context per customer. `ALL ( dim_date )` removes the date
  slicer so "has ordered more than once" means ever, not in the selected period.
- **Why `Cohort Retention %` is SUM/SUM.** Averaging per-cohort percentages would weight a
  2-customer cohort the same as a 7,000-customer one.
- **Why an inactive relationship.** Reviews have their own date (survey sent), but the active
  path to dim_date runs through orders. `USERELATIONSHIP` switches paths for one measure.
- **Why the date table.** Time-intelligence functions need a contiguous, marked date table.
  `SAMEPERIODLASTYEAR` on a fact's own date column silently skips missing days.
