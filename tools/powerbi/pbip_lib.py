"""Helpers to write a Power BI project (.pbip): TMDL semantic model + PBIR report."""
import json
import os
import shutil
import uuid

NS = uuid.UUID("6f1c2f6e-3a1b-4c55-9a77-0c1d2e3f4a5b")
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
V_PAGE = f"{SCHEMA}/item/report/definition/page/2.0.0/schema.json"
V_VISUAL = f"{SCHEMA}/item/report/definition/visualContainer/2.4.0/schema.json"
V_REPORT = f"{SCHEMA}/item/report/definition/report/3.0.0/schema.json"
V_PAGES = f"{SCHEMA}/item/report/definition/pagesMetadata/1.0.0/schema.json"
V_VERSION = f"{SCHEMA}/item/report/definition/versionMetadata/1.0.0/schema.json"


def gid(*parts):
    return str(uuid.uuid5(NS, "/".join(parts)))


def short(*parts):
    return gid(*parts).replace("-", "")[:20]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def wjson(path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ TMDL

M_TYPE = {"text": "type text", "int": "Int64.Type", "num": "type number", "cur": "Currency.Type",
          "date": "type date", "dt": "type datetime"}
TMDL_TYPE = {"text": "string", "int": "int64", "num": "double", "cur": "decimal", "date": "dateTime",
             "dt": "dateTime", "bool": "boolean"}
DEFAULT_FMT = {"int": "#,0", "num": "#,0.00", "cur": "#,0.00", "date": "yyyy-mm-dd",
               "dt": "yyyy-mm-dd hh:nn:ss"}


def q(name):
    """Quote a TMDL object name when needed."""
    if all(c.isalnum() or c == "_" for c in name):
        return name
    return "'" + name.replace("'", "''") + "'"


def indent(text, tabs):
    return "\n".join(("\t" * tabs + line) if line.strip() else "" for line in text.strip("\n").split("\n"))


def csv_partition_m(file, cols, param, nulls=("",), web=False):
    """M that reads one CSV export: blanks -> null, Postgres t/f -> logical, then types (en-US).

    web=True reads `param` as a base URL (Web.Contents with RelativePath), else as a local folder."""
    names = ", ".join(f'"{c[0]}"' for c in cols)
    null_test = " or ".join(f'_ = "{n}"' for n in nulls)
    contents = (f'Web.Contents({param}, [RelativePath = "{file}"])' if web
                else f'File.Contents({param} & "\\{file}")')
    bools = [c[0] for c in cols if c[1] == "bool"]
    typed = ", ".join(f'{{"{c[0]}", {M_TYPE[c[1]]}}}' for c in cols if c[1] != "bool")
    steps = [
        f'Source = Csv.Document({contents}, [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        f"Kept = Table.SelectColumns(Headers, {{{names}}}),",
        f'Blanks = Table.TransformColumns(Kept, {{}}, each if {null_test} then null else _),',
    ]
    last = "Blanks"
    if bools:
        conv = ", ".join(f'{{"{b}", each if _ = null then null else _ = "t", type logical}}' for b in bools)
        steps.append(f"Flags = Table.TransformColumns(Blanks, {{{conv}}}),")
        last = "Flags"
    steps.append(f'Typed = Table.TransformColumnTypes({last}, {{{typed}}}, "en-US")')
    body = "\n".join("    " + s for s in steps)
    return f"let\n{body}\nin\n    Typed"


def table_tmdl(name, cols, m, data_category=None, description=None, measures=None):
    """cols: (name, kind, {hidden, key, fmt, sum, sortBy})"""
    out = []
    if description:
        out.append(f"/// {description}")
    out.append(f"table {q(name)}")
    if data_category:
        out.append(f"\tdataCategory: {data_category}")
    out.append("")
    for meas in measures or []:
        out.append(measure_tmdl(meas))
        out.append("")
    for c in cols:
        cname, kind = c[0], c[1]
        opt = c[2] if len(c) > 2 else {}
        out.append(f"\tcolumn {q(cname)}")
        out.append(f"\t\tdataType: {TMDL_TYPE[kind]}")
        if opt.get("key"):
            out.append("\t\tisKey")
        if opt.get("hidden"):
            out.append("\t\tisHidden")
        fmt = opt.get("fmt", DEFAULT_FMT.get(kind))
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        summ = opt.get("sum")
        if summ is None:
            summ = "sum" if kind in ("int", "num", "cur") else "none"
        out.append(f"\t\tsummarizeBy: {summ}")
        out.append(f"\t\tsourceColumn: {cname}")
        if opt.get("sortBy"):
            out.append(f"\t\tsortByColumn: {q(opt['sortBy'])}")
        out.append("")
    out.append(f"\tpartition {q(name)} = m")
    out.append("\t\tmode: import")
    out.append("\t\tsource =")
    out.append(indent(m, 4))
    out.append("")
    return "\n".join(out)


def measure_tmdl(m):
    """m: dict(name, expr, fmt, folder, desc)"""
    lines = []
    if m.get("desc"):
        for d in m["desc"].split("\n"):
            lines.append(f"\t/// {d}")
    lines.append(f"\tmeasure {q(m['name'])} =")
    lines.append(indent(m["expr"], 3))
    if m.get("fmt"):
        lines.append(f"\t\tformatString: {m['fmt']}")
    if m.get("folder"):
        lines.append(f"\t\tdisplayFolder: {m['folder']}")
    return "\n".join(lines)


def write_semantic_model(root, name, tables, relationships, roles, expressions, query_order):
    sm = os.path.join(root, f"{name}.SemanticModel")
    if os.path.isdir(os.path.join(sm, "definition")):
        shutil.rmtree(os.path.join(sm, "definition"))
    wjson(os.path.join(sm, "definition.pbism"), {
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2",
        "settings": {},
    })
    wjson(os.path.join(sm, ".platform"), {
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "SemanticModel", "displayName": name},
        "config": {"version": "2.0", "logicalId": gid(name, "sm")},
    })
    d = os.path.join(sm, "definition")
    write(os.path.join(d, "database.tmdl"), "database\n\tcompatibilityLevel: 1600\n\n")
    model = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        "annotation PBI_QueryOrder = " + json.dumps(query_order, separators=(",", ":")),
        "",
        "annotation PBI_ProTooling = [\"DevMode\"]",
        "",
    ]
    for t in tables:
        model.append(f"ref table {q(t[0])}")
    model.append("")
    for r in roles:
        model.append(f"ref role {q(r[0])}")
    model.append("")
    write(os.path.join(d, "model.tmdl"), "\n".join(model) + "\n")
    write(os.path.join(d, "expressions.tmdl"), "\n".join(expressions) + "\n")
    for tname, text in tables:
        write(os.path.join(d, "tables", f"{tname}.tmdl"), text)
    rel = []
    for frm, to, opts in relationships:
        rel.append(f"relationship {gid(name, frm, to)}")
        if opts.get("inactive"):
            rel.append("\tisActive: false")
        rel.append(f"\tfromColumn: {frm}")
        rel.append(f"\ttoColumn: {to}")
        rel.append("")
    write(os.path.join(d, "relationships.tmdl"), "\n".join(rel) + "\n")
    for rname, perms in roles:
        lines = [f"role {q(rname)}", "\tmodelPermission: read", ""]
        for tname, expr in perms:
            lines.append(f"\ttablePermission {q(tname)} = {expr}")
            lines.append("")
        write(os.path.join(d, "roles", f"{rname}.tmdl"), "\n".join(lines) + "\n")


# ------------------------------------------------------------------ PBIR

def lit(v):
    """Literal expression; strings are quoted the PBIR way ('text'), bools/numbers raw."""
    if isinstance(v, bool):
        val = "true" if v else "false"
    elif isinstance(v, (int, float)):
        val = f"{v}D" if isinstance(v, float) else f"{v}L"
    else:
        val = "'" + v.replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": val}}}


def col(entity, prop):
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def meas(prop, entity="_Measures"):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


AGG_NAME = {0: "Sum", 1: "Avg", 3: "Min", 4: "Max"}


def agg(entity, prop, fn=0):
    return {"Aggregation": {"Expression": col(entity, prop), "Function": fn}}


def qref(field):
    if "Column" in field:
        c = field["Column"]
        return f"{c['Expression']['SourceRef']['Entity']}.{c['Property']}", c["Property"]
    if "Measure" in field:
        c = field["Measure"]
        return f"{c['Expression']['SourceRef']['Entity']}.{c['Property']}", c["Property"]
    a = field["Aggregation"]
    c = a["Expression"]["Column"]
    fn = AGG_NAME[a["Function"]]
    return f"{fn}({c['Expression']['SourceRef']['Entity']}.{c['Property']})", f"{fn} of {c['Property']}"


def proj(field, display=None):
    ref, native = qref(field)
    p = {"field": field, "queryRef": ref, "nativeQueryRef": native.replace(" ", "_")}
    if display:
        p["displayName"] = display
    return p


def visual(name, vtype, pos, roles=None, title=None, objects=None, sort=None, filters=None,
           vco=None, extra=None):
    x, y, w, h = pos
    v = {"visualType": vtype}
    if roles:
        qs = {}
        for role, fields in roles.items():
            qs[role] = {"projections": [f if "field" in f else proj(f) for f in fields]}
        v["query"] = {"queryState": qs}
        if sort:
            v["query"]["sortDefinition"] = {
                "sort": [{"field": f, "direction": d} for f, d in sort], "isDefaultSort": False}
    if objects:
        v["objects"] = objects
    container = dict(vco or {})
    if title:
        container["title"] = [{"properties": {"show": lit(True), "text": lit(title)}}]
    if container:
        v["visualContainerObjects"] = container
    if extra:
        v.update(extra)
    v["drillFilterOtherVisuals"] = True
    out = {"$schema": V_VISUAL, "name": name,
           "position": {"x": x, "y": y, "z": 0, "width": w, "height": h, "tabOrder": 0}, "visual": v}
    if filters:
        out["filterConfig"] = {"filters": filters}
    return out


def gradient(field, ref, colors):
    """Background colour scale on a matrix/table value column."""
    if len(colors) == 2:
        rule = {"linearGradient2": {"min": {"color": lit(colors[0])["expr"]},
                                    "max": {"color": lit(colors[1])["expr"]},
                                    "nullColoringStrategy": {"strategy": lit("noColor")["expr"]}}}
    else:
        rule = {"linearGradient3": {"min": {"color": lit(colors[0])["expr"]},
                                    "mid": {"color": lit(colors[1])["expr"]},
                                    "max": {"color": lit(colors[2])["expr"]},
                                    "nullColoringStrategy": {"strategy": lit("noColor")["expr"]}}}
    return {"properties": {"backColor": {"solid": {"color": {"expr": {"FillRule": {"Input": field, "FillRule": rule}}}}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": ref}}


CMP = {">": 1, ">=": 2, "<": 3, "<=": 4}


def range_filter(name, entity, column, conditions):
    """Advanced filter: conditions = [(op, literal_value_string)], ANDed. e.g. [('>=', "datetime'2017-01-01T00:00:00'")]"""
    ref = {"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": column}}
    conds = [{"Comparison": {"ComparisonKind": CMP[op], "Left": ref, "Right": {"Literal": {"Value": v}}}}
             for op, v in conditions]
    cond = conds[0]
    for c in conds[1:]:
        cond = {"And": {"Left": cond, "Right": c}}
    return {"name": name, "field": col(entity, column), "type": "Advanced",
            "filter": {"Version": 2, "From": [{"Name": "d", "Entity": entity, "Type": 0}],
                       "Where": [{"Condition": cond}]}}


def topn_filter(name, entity, column, measure, n):
    return {
        "name": name,
        "field": col(entity, column),
        "type": "TopN",
        "filter": {
            "Version": 2,
            "From": [
                {"Name": "subquery", "Expression": {"Subquery": {"Query": {
                    "Version": 2,
                    "From": [{"Name": "d", "Entity": entity, "Type": 0},
                             {"Name": "m", "Entity": "_Measures", "Type": 0}],
                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": column},
                                "Name": "field"}],
                    "OrderBy": [{"Direction": 2, "Expression": {
                        "Measure": {"Expression": {"SourceRef": {"Source": "m"}}, "Property": measure}}}],
                    "Top": n}}}, "Type": 2},
                {"Name": "d", "Entity": entity, "Type": 0},
            ],
            "Where": [{"Condition": {"In": {
                "Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "d"}}, "Property": column}}],
                "Table": {"SourceRef": {"Source": "subquery"}}}}}],
        },
    }


def write_report(root, name, pages):
    """pages: list of dict(name, displayName, visuals, binding=None, filters=None)"""
    rp = os.path.join(root, f"{name}.Report")
    if os.path.isdir(os.path.join(rp, "definition")):
        shutil.rmtree(os.path.join(rp, "definition"))
    wjson(os.path.join(rp, "definition.pbir"), {
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}},
    })
    wjson(os.path.join(rp, ".platform"), {
        "$schema": f"{SCHEMA}/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": name},
        "config": {"version": "2.0", "logicalId": gid(name, "report")},
    })
    d = os.path.join(rp, "definition")
    wjson(os.path.join(d, "version.json"), {"$schema": V_VERSION, "version": "2.0.0"})
    wjson(os.path.join(d, "report.json"), {
        "$schema": V_REPORT,
        "themeCollection": {},
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True,
                     "useEnhancedTooltips": True, "useDefaultAggregateDisplayName": True},
    })
    wjson(os.path.join(d, "pages", "pages.json"), {
        "$schema": V_PAGES, "pageOrder": [p["name"] for p in pages], "activePageName": pages[0]["name"]})
    for p in pages:
        pd = os.path.join(d, "pages", p["name"])
        page = {"$schema": V_PAGE, "name": p["name"], "displayName": p["displayName"],
                "displayOption": "FitToPage", "height": 720, "width": 1280}
        if p.get("filters"):
            page["filterConfig"] = {"filters": p["filters"]}
        if p.get("binding"):
            page["pageBinding"] = p["binding"]
        if p.get("visibility"):
            page["visibility"] = p["visibility"]
        wjson(os.path.join(pd, "page.json"), page)
        for i, v in enumerate(p["visuals"]):
            v["position"]["z"] = i * 1000
            v["position"]["tabOrder"] = i * 1000
            wjson(os.path.join(pd, "visuals", v["name"], "visual.json"), v)


def write_pbip(root, name):
    wjson(os.path.join(root, f"{name}.pbip"), {
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0",
        "artifacts": [{"report": {"path": f"{name}.Report"}}],
        "settings": {"enableAutoRecovery": True},
    })
