"""Validate every JSON file of a PBIP folder against the schema named in its $schema."""
import glob
import hashlib
import json
import os
import sys
import urllib.request

from jsonschema import Draft7Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT7

CACHE = os.path.join(os.path.dirname(__file__), "schema_cache")
os.makedirs(CACHE, exist_ok=True)
GH = "https://raw.githubusercontent.com/microsoft/json-schemas/main/"


def fetch(uri):
    uri = uri.split("#")[0]
    path = os.path.join(CACHE, hashlib.md5(uri.encode()).hexdigest() + ".json")
    if not os.path.exists(path):
        raw = uri.replace("https://developer.microsoft.com/json-schemas/", GH)
        with urllib.request.urlopen(raw) as r:
            open(path, "wb").write(r.read())
    return json.load(open(path, encoding="utf-8"))


def retrieve(uri):
    return Resource.from_contents(fetch(uri), default_specification=DRAFT7)


registry = Registry(retrieve=retrieve)
errors = 0
files = [f for f in glob.glob(os.path.join(sys.argv[1], "**", "*.*"), recursive=True)
         if f.endswith((".json", ".pbir", ".pbip", ".pbism")) or os.path.basename(f) == ".platform"]
files += glob.glob(os.path.join(sys.argv[1], "**", ".platform"), recursive=True)
for f in sorted(set(files)):
    doc = json.load(open(f, encoding="utf-8"))
    uri = doc.get("$schema")
    if not uri:
        continue
    v = Draft7Validator(fetch(uri), registry=registry)
    errs = list(v.iter_errors(doc))
    for e in errs[:5]:
        errors += 1
        print(f"{os.path.relpath(f, sys.argv[1])}: {'/'.join(map(str, e.absolute_path))}: {e.message[:300]}")
print(f"checked {len(set(files))} files, {errors} errors")
