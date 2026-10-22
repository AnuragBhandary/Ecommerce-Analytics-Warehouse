# Report-as-code tooling (Power BI project + Excel workbook)

These scripts produced the first version of [`powerbi/olist-report`](../../powerbi/olist-report)
and [`excel/seller-performance.xlsx`](../../excel/seller-performance.xlsx). After review, the
project was opened, refreshed, tested and re-saved in Power BI Desktop. **The committed,
Desktop-saved files are the version of record.** Edit the report in Desktop. Use these scripts
to see how the project is put together, or to regenerate it from scratch.

| File | What it does |
|---|---|
| `pbip_lib.py` | Writes a Power BI project (`.pbip`): the semantic model as TMDL and the report as PBIR JSON. Helpers for M queries, measures, visuals, filters and conditional formatting. |
| `build_olist.py` | The Olist spec: 10 tables with explicit types, 10 relationships, 3 RLS roles, 4 pages. Measures are read from [`powerbi/measures.dax`](../../powerbi/measures.dax), the single source of truth. |
| `validate_pbir.py` | Validates every JSON file against the Microsoft schema named in its `$schema`, following cross-file `$ref`s (schemas are fetched from github.com/microsoft/json-schemas and cached). |
| `build_excel.py` | Builds the Excel workbook by automating Excel itself (COM, Windows only): Power Query queries, PivotTables with a slicer and timeline, the XLOOKUP scorecard, conditional formatting and the reconciliation check. |

```bash
pip install jsonschema referencing pywin32
python tools/powerbi/build_olist.py                     # writes tools/powerbi/out/olist-report (gitignored)
python tools/powerbi/validate_pbir.py tools/powerbi/out/olist-report
python tools/powerbi/build_excel.py                     # writes tools/powerbi/out/seller-performance.xlsx
```

Both build scripts write to `tools/powerbi/out/` unless you give an output path, so the
committed project and workbook aren't overwritten by accident. A regenerated project matches the
committed one except for Desktop's formatting and `lineageTag`s.

How it was used:
1. Profile the `make export` CSVs: key uniqueness, orphaned foreign keys, types. This found the
   `t`/`f` booleans that the M queries convert.
2. Generate the project, then validate it. All 44 files passed, and a deliberately broken file
   is rejected, which shows the validator really checks.
3. Open in Power BI Desktop, refresh, review each page, fix the measure in `measures.dax` or the
   spec, regenerate. The bugs found this way are listed in
   [`powerbi/README.md`](../../powerbi/README.md).
4. Test drill-through and each RLS role (Modeling > View as), save in Desktop, and export the
   PDF.
