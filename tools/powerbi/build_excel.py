"""Build excel/seller-performance.xlsx through Excel itself (COM), following excel/README.md."""
import csv
import os
import win32com.client as win32

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))  # tools/powerbi -> repo root
# Output file: argv[1], else tools/powerbi/out/seller-performance.xlsx.
OUT = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(HERE, "out", "seller-performance.xlsx")
EXPORTS = os.path.join(REPO, "exports")
PBI_REVENUE = 15735527.03  # Power BI: Revenue (all years, no slicers)
TOP_SELLER = "4869f7a5dfa277a7dca6462dcf3b52b2"
BRL = '"R$" #,##0.00'

M_MONTHLY = r'''let
    Folder = Excel.CurrentWorkbook(){[Name = "ExportsFolder"]}[Content]{0}[ExportsFolder],
    Source = Csv.Document(File.Contents(Folder & "\mart_seller_monthly.csv"), [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed = Table.TransformColumnTypes(Headers, {{"seller_id", type text}, {"seller_state", type text}, {"seller_city", type text}, {"month", type date}, {"orders", Int64.Type}, {"items", Int64.Type}, {"revenue", Currency.Type}, {"delivered_orders", Int64.Type}, {"late_orders", Int64.Type}, {"avg_review_score", type number}}, "en-US"),
    LateRate = Table.AddColumn(Typed, "late_rate", each if [delivered_orders] = 0 then null else [late_orders] / [delivered_orders], type number),
    YearMonth = Table.AddColumn(LateRate, "year_month", each Date.ToText([month], "yyyy-MM"), type text)
in
    YearMonth'''

M_TOTALS = r'''let
    Source = SellerMonthly,
    Grouped = Table.Group(Source, {"seller_id", "seller_state", "seller_city"}, {
        {"Sum of revenue", each List.Sum([revenue]), Currency.Type},
        {"Sum of orders", each List.Sum([orders]), Int64.Type},
        {"Sum of delivered_orders", each List.Sum([delivered_orders]), Int64.Type},
        {"Sum of late_orders", each List.Sum([late_orders]), Int64.Type},
        {"Average of avg_review_score", each List.Average([avg_review_score]), type number}}),
    LateRate = Table.AddColumn(Grouped, "late_rate", each if [Sum of delivered_orders] = 0 then null else [Sum of late_orders] / [Sum of delivered_orders], type number),
    Sorted = Table.Sort(LateRate, {{"Sum of revenue", Order.Descending}})
in
    Sorted'''

xlSrcExternal, xlCmdSql, xlDatabase = 0, 2, 1
xlRowField, xlColumnField = 1, 2
xlSum, xlAverage = -4157, -4106
xlSlicer, xlTimeline = 1, 2
xlCellValue, xlExpression, xlGreater = 1, 2, 5
xlValidateList = 3
xlLine = 4


def load_query(wb, ws, name):
    conn = (f"OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;"
            f"Location={name};Extended Properties=\"\"")
    lo = ws.ListObjects.Add(xlSrcExternal, conn, None, 1, ws.Range("A1"))
    lo.Name = name
    qt = lo.QueryTable
    qt.CommandType = xlCmdSql
    qt.CommandText = [f"SELECT * FROM [{name}]"]
    qt.RefreshOnFileOpen = False
    qt.Refresh(False)
    return lo


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):
        os.remove(OUT)
    xl = win32.DispatchEx("Excel.Application")
    xl.Visible = False
    xl.DisplayAlerts = False
    wb = xl.Workbooks.Add()
    try:
        build(xl, wb)
        wb.SaveAs(OUT, 51)  # xlOpenXMLWorkbook
        add_chart(wb)
        wb.Sheets("Scorecard").Activate()
        wb.Save()
        print("saved", OUT)
    finally:
        wb.Close(False)
        xl.Quit()


def build(xl, wb):
    while wb.Sheets.Count < 5:
        wb.Sheets.Add(After=wb.Sheets(wb.Sheets.Count))
    names = ["Scorecard", "Pivot", "Sellers", "Data", "Settings"]
    for i, n in enumerate(names, 1):
        wb.Sheets(i).Name = n
    sc, pv, se, da, st = (wb.Sheets(n) for n in names)

    # Settings: where the CSVs live (Power Query reads this named cell)
    st.Range("A1").Value = "ExportsFolder"
    st.Range("A2").Value = EXPORTS
    st.ListObjects.Add(1, st.Range("A1:A2"), None, 1).Name = "ExportsFolder"
    st.Range("A4").Value = ("Folder with the `make export` CSVs. Change A2 if the folder moves, "
                            "then Data > Refresh All.")
    st.Columns("A").ColumnWidth = 80

    # 1. Power Query
    wb.Queries.Add("SellerMonthly", M_MONTHLY, "Seller x month from marts.mart_seller_monthly")
    wb.Queries.Add("SellerTotals", M_TOTALS, "One row per seller, grouped from SellerMonthly")
    data = load_query(wb, da, "SellerMonthly")
    sellers = load_query(wb, se, "SellerTotals")
    for lo, cols in ((data, {"revenue": BRL, "late_rate": "0.0%", "avg_review_score": "0.00",
                             "month": "yyyy-mm-dd"}),
                     (sellers, {"Sum of revenue": BRL, "late_rate": "0.0%",
                                "Average of avg_review_score": "0.00"})):
        for c, fmt in cols.items():
            lo.ListColumns(c).DataBodyRange.NumberFormat = fmt
        lo.Range.Columns.AutoFit()

    # 4. Conditional formatting on the tables
    rng = sellers.ListColumns("late_rate").DataBodyRange
    fc = rng.FormatConditions.Add(xlCellValue, xlGreater, "=0.1")
    fc.Interior.Color = 0xC7CEFF  # light red (BGR)
    fc.Font.Color = 0x06009C
    cs = sellers.ListColumns("Average of avg_review_score").DataBodyRange.FormatConditions.AddColorScale(3)
    cs.ColorScaleCriteria(1).FormatColor.Color = 0x6B69F8  # red
    cs.ColorScaleCriteria(2).FormatColor.Color = 0x84EBFF  # yellow
    cs.ColorScaleCriteria(3).FormatColor.Color = 0x7BBE63  # green
    data.ListColumns("revenue").DataBodyRange.FormatConditions.AddDatabar()

    # 2. PivotTables
    cache = wb.PivotCaches().Create(xlDatabase, "SellerMonthly")
    pv.Range("A1").Value = "Revenue by seller state and month"
    pv.Range("A1").Font.Bold = True
    p1 = cache.CreatePivotTable(pv.Range("A12"), "RevenueByStateMonth")
    p1.PivotFields("seller_state").Orientation = xlRowField
    p1.PivotFields("year_month").Orientation = xlColumnField
    f = p1.AddDataField(p1.PivotFields("revenue"), "Revenue ", xlSum)
    f.NumberFormat = '"R$" #,##0'

    top = 12 + p1.TableRange2.Rows.Count + 3
    pv.Range(f"A{top - 1}").Value = "Top 10 sellers by revenue"
    pv.Range(f"A{top - 1}").Font.Bold = True
    p2 = cache.CreatePivotTable(pv.Range(f"A{top}"), "TopSellers")
    p2.PivotFields("seller_id").Orientation = xlRowField
    p2.AddDataField(p2.PivotFields("revenue"), "Revenue ", xlSum).NumberFormat = '"R$" #,##0'
    p2.AddDataField(p2.PivotFields("orders"), "Orders ", xlSum).NumberFormat = "#,##0"
    p2.AddDataField(p2.PivotFields("avg_review_score"), "Avg review score ", xlAverage).NumberFormat = "0.00"
    p2.PivotFields("seller_id").PivotFilters.Add2(Type=1, DataField=p2.DataFields("Revenue "), Value1=10)  # xlTopCount

    pv.Range(f"G{top - 1}").Value = "Late deliveries by seller state"
    pv.Range(f"G{top - 1}").Font.Bold = True
    p3 = cache.CreatePivotTable(pv.Range(f"G{top}"), "LateByState")
    p3.PivotFields("seller_state").Orientation = xlRowField
    p3.AddDataField(p3.PivotFields("late_orders"), "Late orders ", xlSum).NumberFormat = "#,##0"
    p3.AddDataField(p3.PivotFields("delivered_orders"), "Delivered orders ", xlSum).NumberFormat = "#,##0"
    p3.CalculatedFields().Add("LateRate", "= late_orders / delivered_orders", True)
    p3.AddDataField(p3.PivotFields("LateRate"), "Late % ", xlSum).NumberFormat = "0.0%"

    # Slicer + timeline above the pivots, connected to all three
    sl = wb.SlicerCaches.Add2(p1, "seller_state", "Slicer_seller_state", xlSlicer)
    sl.Slicers.Add(SlicerDestination=pv, Name="StateSlicer", Caption="Seller state",
                   Top=pv.Range("A2").Top, Left=pv.Range("A2").Left, Width=150, Height=160)
    tl = wb.SlicerCaches.Add2(p1, "month", "Timeline_month", xlTimeline)
    tl.Slicers.Add(SlicerDestination=pv, Name="MonthTimeline", Caption="Month",
                   Top=pv.Range("A2").Top, Left=pv.Range("A2").Left + 170, Width=520, Height=110)
    for c in (sl, tl):
        c.PivotTables.AddPivotTable(p2)
        c.PivotTables.AddPivotTable(p3)
    pv.Columns("A").ColumnWidth = 36

    # 3. Scorecard
    sc.Range("A1").Value = "Seller scorecard"
    sc.Range("A1").Font.Size = 16
    sc.Range("A1").Font.Bold = True
    sc.Range("A2").Value = "Seller id"
    sc.Range("B2").Value = TOP_SELLER
    sc.Range("B2").Interior.Color = 0xCCF2FF
    sc.Range("B2").Validation.Add(xlValidateList, 1, 1, '=INDIRECT("SellerTotals[seller_id]")')
    rows = [
        ("State", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[seller_state], "not found")', None),
        ("City", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[seller_city], "")', None),
        ("Revenue", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Sum of revenue], 0)', BRL),
        ("Orders", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Sum of orders], 0)', "#,##0"),
        ("Late rate", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[late_rate], "")', "0.0%"),
        ("Avg review score", '=XLOOKUP($B$2, SellerTotals[seller_id], SellerTotals[Average of avg_review_score], "")', "0.00"),
        ("Revenue rank (all sellers)", '=COUNTIF(SellerTotals[Sum of revenue], ">" & B6) + 1', "#,##0"),
        ("Revenue rank (in state)", '=COUNTIFS(SellerTotals[seller_state], B4, SellerTotals[Sum of revenue], ">" & B6) + 1', "#,##0"),
    ]
    for i, (label, formula, fmt) in enumerate(rows, 4):
        sc.Range(f"A{i}").Value = label
        # .Formula with the _xlfn. prefix stores XLOOKUP as written; this Excel (unlicensed, reduced
        # functionality) can't evaluate it, any licensed Excel 365 / Excel for the web does.
        sc.Range(f"B{i}").Formula = formula.replace("=XLOOKUP(", "=_xlfn.XLOOKUP(")
        if fmt:
            sc.Range(f"B{i}").NumberFormat = fmt
    sc.Range("B4:B11").HorizontalAlignment = -4152  # right
    c = sc.Range("B8").FormatConditions.Add(xlExpression, None, '=AND(ISNUMBER($B$8), $B$8>0.1)')
    c.Font.Color, c.Font.Bold = 0x0000C0, True
    c = sc.Range("B9").FormatConditions.Add(xlExpression, None, '=AND(ISNUMBER($B$9), $B$9<3.5)')
    c.Font.Color, c.Font.Bold = 0x0000C0, True

    # Monthly trend for the selected seller: one row per month, SUMIFS against the Data table
    sc.Range("A13").Value = "Monthly trend (selected seller)"
    sc.Range("A13").Font.Bold = True
    for col, h in zip("ABCD", ["Month", "Orders", "Revenue", "Late orders"]):
        sc.Range(f"{col}14").Value = h
        sc.Range(f"{col}14").Font.Bold = True
    months = sorted({r["month"][:7] for r in csv.DictReader(open(os.path.join(EXPORTS, "mart_seller_monthly.csv"),
                                                                  encoding="utf-8"))})
    last = 14 + len(months)
    sc.Range(f"A15:A{last}").NumberFormat = "@"
    for i, m in enumerate(months, 15):
        sc.Range(f"A{i}").Value = m
    for col, field in (("B", "orders"), ("C", "revenue"), ("D", "late_orders")):
        sc.Range(f"{col}15:{col}{last}").Formula = (
            f"=SUMIFS(SellerMonthly[{field}], SellerMonthly[seller_id], $B$2, SellerMonthly[year_month], $A15)")
    sc.Range(f"C15:C{last}").NumberFormat = BRL
    
    # 5. Reconciliation
    rec = [("Total revenue (workbook)", "=SUM(SellerMonthly[revenue])"),
           ("Total revenue (Power BI)", PBI_REVENUE),
           ("Check", '=IF(ROUND(F2-F3, 2) = 0, "OK", "MISMATCH")')]
    for i, (label, val) in enumerate(rec, 2):
        sc.Range(f"E{i}").Value = label
        if isinstance(val, str):
            sc.Range(f"F{i}").Formula = val
        else:
            sc.Range(f"F{i}").Value = val
    sc.Range("F2:F3").NumberFormat = BRL
    ok = sc.Range("F4").FormatConditions.Add(xlCellValue, 3, '="OK"')  # xlEqual
    ok.Interior.Color, ok.Font.Color, ok.Font.Bold = 0xCEEFC6, 0x006100, True
    bad = sc.Range("F4").FormatConditions.Add(xlCellValue, 3, '="MISMATCH"')
    bad.Interior.Color, bad.Font.Color, bad.Font.Bold = 0xC7CEFF, 0x06009C, True
    sc.Range("E5").Value = "Power BI figure: Revenue card on the Overview page (all years, no slicers)."
    sc.Range("E5").Font.Italic = True
    sc.Columns("A").ColumnWidth = 26
    sc.Columns("B").ColumnWidth = 36
    sc.Columns("C").ColumnWidth = 16
    sc.Columns("D").ColumnWidth = 12
    sc.Columns("E").ColumnWidth = 26
    sc.Columns("F").ColumnWidth = 18
    wb.ForceFullCalculation = True  # recalc XLOOKUP when opened in a licensed Excel
    xl.CalculateFull()


def add_chart(wb):
    """Line chart over the scorecard's monthly trend."""
    sc = wb.Sheets("Scorecard")
    last = 14 + sum(1 for _ in range(15, 200) if sc.Range(f"A{_}").Value)
    ch = sc.ChartObjects().Add(sc.Range("F8").Left, sc.Range("F8").Top, 520, 280).Chart
    ch.ChartType = xlLine
    while ch.SeriesCollection().Count:
        ch.SeriesCollection(1).Delete()
    s = ch.SeriesCollection().NewSeries()
    s.Name = "Revenue"
    s.XValues = sc.Range(f"A15:A{last}")
    s.Values = sc.Range(f"C15:C{last}")
    ch.HasTitle = True
    ch.ChartTitle.Text = "Seller revenue by month"
    ch.HasLegend = False


if __name__ == "__main__":
    main()
