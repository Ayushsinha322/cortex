"""Freeze a live cortex into a JSON fixture the static demo can serve."""
import json, os, sys, urllib.parse, urllib.request

BASE, TOKEN = sys.argv[1], sys.argv[2]
REAL = os.path.expanduser("~/cortex")
FAKE = "/home/dev/cortex"          # the real path never reaches the public demo

def get(route, **params):
    params["t"] = TOKEN
    url = f"{BASE}{route}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url) as r:
        return json.load(r)

def scrub(obj):
    if isinstance(obj, str):
        return obj.replace(REAL, FAKE)
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    if isinstance(obj, dict):
        return {scrub(k): scrub(v) for k, v in obj.items()}
    return obj

root = get("/api/root")
children, previews, raws = {}, {}, {}

queue, seen = [root["id"]], set()
while queue:
    path = queue.pop(0)
    if path in seen:
        continue
    seen.add(path)
    kids = get("/api/children", path=path)
    children[path] = kids
    for k in kids:
        if k.get("dir"):
            queue.append(k["id"])
        else:
            p = get("/api/preview", path=k["id"])
            previews[k["id"]] = p
            if p.get("kind") in ("image", "pdf", "video", "audio"):
                raws[k["id"]] = os.path.relpath(k["id"], REAL)

fixture = {
    "root": scrub(root),
    "children": scrub(children),
    "previews": scrub(previews),
    "links": scrub(get("/api/links")),
    "git": scrub(get("/api/git")),
    "layout": scrub(get("/api/layout")),
    "editors": {"editors": []},
    "raw": scrub(raws),
    "realRoot": FAKE,
}
json.dump(fixture, open(sys.argv[3], "w"), separators=(",", ":"))
print(f"  folders: {len(children)}  files: {len(previews)}  raw assets: {len(raws)}")
print(f"  links: {len(fixture['links'].get('edges') or [])} edges")
