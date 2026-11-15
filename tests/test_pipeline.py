"""Simula Reddit y Claude para probar el modo real de punta a punta sin red."""
import json, sys, time, types
sys.path.insert(0, "scripts")
import collect, tickers

now = time.time()
def c(i, body, author, score=5, replies=""):
    return {"kind": "t1", "data": {"name": f"t1_{i}", "body": body, "author": author, "score": score,
            "created_utc": now - 3600, "permalink": f"/r/wsb/c/{i}", "replies": replies}}
POST = {"kind": "t3", "data": {"name": "t3_p1", "id": "p1", "title": "NVDA earnings play", "selftext": "Loading $NVDA calls",
        "author": "op", "score": 900, "created_utc": now - 7200, "permalink": "/r/wsb/p1"}}
nested = {"data": {"children": [c(3, "TSLA puts printing", "u3"), {"kind": "more", "data": {}}]}}
COMMENTS = [c(1, "NVDA to the moon", "u1", 40), c(2, "nvidia is overvalued, bought puts", "u2", 12, nested),
            c(4, "$TSLA calls", "u4"), c(5, "TSLA is done", "u5"), c(6, "NVDA bagholder here", "u6"),
            c(7, "IT is ALL about the DD", "u7"), c(8, "$TSLA lol", "AutoModerator")]

class FakeReddit:
    mode = "reddit-oauth"
    def get(self, path, **kw):
        if path.endswith("/new"): return {"data": {"children": [POST], "after": None}}
        if path.endswith("/comments"): return {"data": {"children": COMMENTS[:2] + COMMENTS[3:], "after": None}}
        if path.endswith("/hot"): return {"data": {"children": [POST]}}
        if path.startswith("/comments/"): return [{}, {"data": {"children": COMMENTS}}]
collect.Reddit = FakeReddit

docs, mode = collect.fetch_reddit(["wallstreetbets"], 24)
U = {"NVDA": "NVIDIA", "TSLA": "Tesla", "IT": "Gartner", "ALL": "Allstate", "DD": "DuPont"}
ranked, tagged = collect.aggregate(docs, U)
print("docs", len(docs), "tagged", tagged, [(r["ticker"], r["mentions"], r["unique_authors"]) for r in ranked])
assert {r["ticker"] for r in ranked} == {"NVDA", "TSLA"}
assert all(d["author"] != "AutoModerator" for d in docs)

# Claude falso: responde según palabras clave
class FakeMsg:
    def __init__(self, prompt):
        out = []
        for line in prompt.split("Comentarios:\n")[1].splitlines():
            j = json.loads(line)
            s = "bear" if any(w in j["texto"].lower() for w in ("puts", "done", "overvalued", "bagholder")) else "bull"
            out += [{"id": j["id"], "t": t, "s": s, "meme": "moon" in j["texto"]} for t in j["tickers"]]
        self.content = [types.SimpleNamespace(type="text", text="Aquí tienes:\n" + json.dumps(out))]
fake = types.ModuleType("anthropic")
fake.Anthropic = lambda api_key: types.SimpleNamespace(messages=types.SimpleNamespace(create=lambda **kw: FakeMsg(kw["messages"][0]["content"])))
sys.modules["anthropic"] = fake
import os; os.environ["ANTHROPIC_API_KEY"] = "x"
labels = collect.classify_ai(ranked, 15, 40, "m")
g = collect.apply_labels(ranked, labels, 15)
for r in ranked: print(r["ticker"], r["sentiment"])
print("global", g)
nv = next(r for r in ranked if r["ticker"] == "NVDA")["sentiment"]
assert nv["bull"] == 2 and nv["bear"] == 2, nv
print("PIPELINE OK")
