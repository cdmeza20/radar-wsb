"""Radar WSB — recolecta menciones de acciones en Reddit, mide el sentimiento con IA
y genera docs/data/latest.json para el dashboard.

Uso:
    python scripts/collect.py           # modo real (usa los secrets/variables de entorno)
    python scripts/collect.py --demo    # datos simulados para probar el dashboard

Variables de entorno (todas opcionales; el sistema degrada con elegancia):
    REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET  -> API oficial de Reddit (recomendado)
    ANTHROPIC_API_KEY                       -> sentimiento con IA
    CLAUDE_MODEL         (def. claude-haiku-5-5)
    SUBREDDITS           (def. wallstreetbets,stocks,options)
    WINDOW_HOURS         (def. 24)
    AI_TOP_TICKERS       (def. 15)  cuántos tickers se analizan con IA
    AI_MAX_PER_TICKER    (def. 40)  comentarios por ticker que lee la IA
    SEC_USER_AGENT       (def. genérico) la SEC pide un User-Agent con contacto
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from tickers import extract, load_universe  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "docs" / "data"
UA = "python:radar-wsb:v1.0 (educational dashboard)"
SKIP_AUTHORS = {"AutoModerator", "VisualMod", "[deleted]", "None", ""}
MAX_DOCS_PER_AUTHOR_PER_TICKER = 3  # anti-spam: un usuario no puede inflar un ticker


def env(name, default):
    v = os.environ.get(name, "").strip()
    return v if v else default


# --------------------------------------------------------------------------- Reddit
class Reddit:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
        self.mode = "reddit-public"
        self.base = "https://www.reddit.com"
        if cid and secret:
            r = self.s.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(cid, secret), data={"grant_type": "client_credentials"}, timeout=30,
            )
            r.raise_for_status()
            self.s.headers["Authorization"] = f"bearer {r.json()['access_token']}"
            self.base = "https://oauth.reddit.com"
            self.mode = "reddit-oauth"

    def get(self, path, **params):
        url = self.base + path + ("" if self.mode == "reddit-oauth" else ".json")
        params["raw_json"] = 1
        for attempt in range(3):
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            time.sleep(0.7)  # respeta el límite de ~100 pedidos/minuto
            return r.json()
        r.raise_for_status()


def _doc(d, kind, sub):
    text = (d.get("title", "") + "\n" + d.get("selftext", "")) if kind == "post" else d.get("body", "")
    return {
        "id": d["name"], "kind": kind, "sub": sub, "author": str(d.get("author")),
        "score": int(d.get("score") or 0), "created": float(d.get("created_utc") or 0),
        "text": text.strip(), "url": "https://www.reddit.com" + d.get("permalink", ""),
    }


def fetch_reddit(subs, window_h):
    rd = Reddit()
    cutoff = time.time() - window_h * 3600
    docs = {}

    def add(d):
        if d["created"] >= cutoff and d["author"] not in SKIP_AUTHORS and d["text"]:
            docs[d["id"]] = d

    for sub in subs:
        # 1) Posts nuevos de la ventana
        after = None
        for _ in range(10):
            j = rd.get(f"/r/{sub}/new", limit=100, after=after)
            items = j["data"]["children"]
            for c in items:
                add(_doc(c["data"], "post", sub))
            after = j["data"].get("after")
            if not after or not items or items[-1]["data"]["created_utc"] < cutoff:
                break
        # 2) Flujo de comentarios recientes
        after = None
        for _ in range(10):
            j = rd.get(f"/r/{sub}/comments", limit=100, after=after)
            items = j["data"]["children"]
            for c in items:
                add(_doc(c["data"], "comment", sub))
            after = j["data"].get("after")
            if not after or not items or items[-1]["data"]["created_utc"] < cutoff:
                break
        # 3) Comentarios de los hilos más activos (incluye el Daily Discussion)
        hot = rd.get(f"/r/{sub}/hot", limit=8)["data"]["children"]
        for h in hot[:6]:
            j = rd.get(f"/comments/{h['data']['id']}", limit=500, sort="top", depth=8)
            stack = list(j[1]["data"]["children"]) if len(j) > 1 else []
            while stack:
                c = stack.pop()
                if c.get("kind") != "t1":
                    continue
                add(_doc(c["data"], "comment", sub))
                rep = c["data"].get("replies")
                if isinstance(rep, dict):
                    stack.extend(rep["data"]["children"])
        print(f"[reddit] r/{sub}: acumulados {len(docs)} textos")
    return list(docs.values()), rd.mode


def fetch_apewisdom():
    rows = []
    for page in (1, 2):
        r = requests.get(f"https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/{page}",
                         headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        rows += r.json().get("results", [])
    return rows


# --------------------------------------------------------------------------- Agregación
def aggregate(docs, universe):
    per = defaultdict(lambda: {"docs": [], "authors": defaultdict(int), "upvotes": 0, "cashtag": 0})
    tagged = 0
    for d in docs:
        found = extract(d["text"], universe)
        if not found:
            continue
        tagged += 1
        d["tickers"] = sorted(found)
        for t, how in found.items():
            p = per[t]
            if p["authors"][d["author"]] >= MAX_DOCS_PER_AUTHOR_PER_TICKER:
                continue
            p["authors"][d["author"]] += 1
            p["docs"].append(d)
            p["upvotes"] += max(d["score"], 0)
            p["cashtag"] += how == "cashtag"
    out = []
    for t, p in per.items():
        n = len(p["docs"])
        out.append({
            "ticker": t, "name": universe.get(t, ""), "mentions": n,
            "unique_authors": len(p["authors"]), "upvotes": p["upvotes"],
            "cashtag_pct": round(100 * p["cashtag"] / n) if n else 0, "_docs": p["docs"],
        })
    # Para entrar al ranking: al menos 3 autores distintos (filtra spam de una sola cuenta)
    out = [o for o in out if o["unique_authors"] >= 3]
    out.sort(key=lambda o: (o["mentions"], o["unique_authors"]), reverse=True)
    return out, tagged


# --------------------------------------------------------------------------- IA
PROMPT = """Eres un analista que clasifica comentarios de foros de bolsa (r/wallstreetbets y similares).
El lenguaje es irónico, con jerga ("calls", "puts", "bagholder", "to the moon", "rope", emojis 🚀🌈🐻).
Para CADA comentario y CADA ticker indicado, decide la postura del autor sobre ESE ticker:
- "bull": espera que suba / compró acciones o calls / lo recomienda.
- "bear": espera que baje / compró puts / vendió / se burla de quien lo tiene.
- "neutral": pregunta, dato, noticia, o no se puede saber.
Marca "meme": true si es principalmente broma o shitpost sin argumento.
Ten en cuenta la ironía: "mi cuenta está muerta por $X, compré más" es bull.

Responde SOLO un JSON válido con esta forma, sin texto extra:
[{"id": "...", "t": "TICKER", "s": "bull|bear|neutral", "meme": true|false}, ...]

Comentarios:
"""


def classify_ai(ranked, top_n, per_ticker, model):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    jobs = {}  # doc_id -> (doc, set(tickers))
    for row in ranked[:top_n]:
        docs = sorted(row["_docs"], key=lambda d: d["score"], reverse=True)[:per_ticker]
        for d in docs:
            jobs.setdefault(d["id"], (d, set()))[1].add(row["ticker"])

    labels = {}  # (doc_id, ticker) -> (s, meme)
    items = list(jobs.values())
    for i in range(0, len(items), 20):
        batch = items[i:i + 20]
        body = "\n".join(
            json.dumps({"id": d["id"], "tickers": sorted(ts), "texto": d["text"][:600]}, ensure_ascii=False)
            for d, ts in batch
        )
        for attempt in range(3):
            try:
                msg = client.messages.create(
                    model=model, max_tokens=2500,
                    messages=[{"role": "user", "content": PROMPT + body}],
                )
                txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
                arr = json.loads(re.search(r"\[.*\]", txt, re.S).group(0))
                for a in arr:
                    if a.get("s") in ("bull", "bear", "neutral"):
                        labels[(a["id"], str(a["t"]).upper())] = (a["s"], bool(a.get("meme")))
                break
            except Exception as e:  # noqa: BLE001
                print(f"[ia] lote {i // 20 + 1} intento {attempt + 1} falló: {e}")
                time.sleep(3)
    print(f"[ia] {len(labels)} etiquetas de {sum(len(t) for _, t in items)} pedidas")
    return labels


def apply_labels(ranked, labels, top_n):
    totals = {"bull": 0, "bear": 0, "neutral": 0, "meme": 0}
    for row in ranked:
        row["sentiment"] = None
        row["samples"] = []
        docs = sorted(row["_docs"], key=lambda d: d["score"], reverse=True)
        if labels is not None and row in ranked[:top_n]:
            c = {"bull": 0, "bear": 0, "neutral": 0, "meme": 0}
            for d in docs:
                lab = labels.get((d["id"], row["ticker"]))
                if not lab:
                    continue
                c[lab[0]] += 1
                c["meme"] += lab[1]
            n = c["bull"] + c["bear"] + c["neutral"]
            if n:
                row["sentiment"] = {**c, "n": n, "score": round((c["bull"] - c["bear"]) / n, 3)}
                for k in totals:
                    totals[k] += c[k]
        for d in docs[:8]:
            lab = labels.get((d["id"], row["ticker"])) if labels else None
            row["samples"].append({
                "text": d["text"][:320] + ("…" if len(d["text"]) > 320 else ""),
                "score": d["score"], "url": d["url"], "sub": d["sub"],
                "label": lab[0] if lab else None, "meme": lab[1] if lab else None,
            })
    n = totals["bull"] + totals["bear"] + totals["neutral"]
    return {**totals, "n": n, "score": round((totals["bull"] - totals["bear"]) / n, 3) if n else None}


# --------------------------------------------------------------------------- Precios
def add_prices(rows):
    try:
        import yfinance as yf
    except ImportError:
        return
    syms = [r["ticker"] for r in rows]
    if not syms:
        return
    try:
        df = yf.download([s.replace(".", "-") for s in syms], period="10d", interval="1d",
                         group_by="ticker", progress=False, auto_adjust=True, threads=True)
    except Exception as e:  # noqa: BLE001
        print(f"[precios] error: {e}")
        return
    for r in rows:
        s = r["ticker"].replace(".", "-")
        try:
            close = (df[s]["Close"] if len(syms) > 1 else df["Close"]).dropna()
            if len(close) >= 2:
                last = float(close.iloc[-1])
                r["price"] = {
                    "last": round(last, 2),
                    "chg_1d": round(100 * (last / float(close.iloc[-2]) - 1), 2),
                    "chg_5d": round(100 * (last / float(close.iloc[-6]) - 1), 2) if len(close) >= 6 else None,
                    "asof": str(close.index[-1].date()),
                }
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- Histórico
def update_history(rows, source, today):
    path = DATA / "history.json"
    hist = json.loads(path.read_text()) if path.exists() else {}
    family = "apewisdom" if source == "apewisdom" else ("demo" if source == "demo" else "reddit")
    days = hist.setdefault(family, {})
    days[today] = {r["ticker"]: r["mentions"] for r in rows}
    for d in sorted(days)[:-60]:  # guarda 60 días
        del days[d]
    path.write_text(json.dumps(hist, separators=(",", ":")))

    prev = [d for d in sorted(days) if d < today][-7:]
    for r in rows:
        r["history"] = [{"date": d, "mentions": days[d].get(r["ticker"], 0)} for d in sorted(days)[-30:]]
        if len(prev) >= 2:
            base = sum(days[d].get(r["ticker"], 0) for d in prev) / len(prev)
            r["baseline"] = round(base, 1)
            r["ratio"] = round(r["mentions"] / max(base, 1), 2)
        else:
            r["baseline"] = r["ratio"] = None
    return len(prev)


# --------------------------------------------------------------------------- Demo
DEMO_SNIPPETS = {
    "bull": ["$TICK calls printing, see you on the moon 🚀", "Loaded more TICK at the open, earnings will rip",
             "TICK is criminally undervalued, holding till 2027", "my TICK 0DTE calls just 4x'd lmao"],
    "bear": ["TICK puts are free money, this is overvalued garbage", "sold all my TICK, chart looks like a cliff",
             "TICK bagholders in shambles 🌈🐻", "bought $TICK puts expiring Friday, valuation makes no sense"],
    "neutral": ["what do you guys think about TICK before earnings?", "TICK reports Thursday after close",
                "anyone holding TICK through the split?"],
}
DEMO_TICKERS = {"NVDA": (420, .62), "TSLA": (310, -.25), "PLTR": (190, .45), "GME": (140, .3),
                "AMD": (120, .1), "SPY": (260, -.15), "HOOD": (80, .5), "RKLB": (75, .55),
                "INTC": (60, -.45), "SMCI": (55, -.3), "MSTR": (50, .2), "SOFI": (45, .35)}


DEMO_NAMES = {"NVDA": "NVIDIA Corp", "TSLA": "Tesla, Inc.", "PLTR": "Palantir Technologies", "GME": "GameStop Corp.",
              "AMD": "Advanced Micro Devices", "HOOD": "Robinhood Markets", "RKLB": "Rocket Lab",
              "INTC": "Intel Corp", "SMCI": "Super Micro Computer", "MSTR": "Strategy Inc", "SOFI": "SoFi Technologies"}


def demo_docs():
    random.seed(7)
    now = time.time()
    docs = []
    for t, (n, bias) in DEMO_TICKERS.items():
        for i in range(n):
            r = random.random()
            s = "bull" if r < (0.45 + bias / 2) else ("bear" if r < 0.85 else "neutral")
            txt = random.choice(DEMO_SNIPPETS[s]).replace("TICK", t)
            docs.append({"id": f"t1_{t}{i}", "kind": "comment", "sub": "wallstreetbets",
                         "author": f"user{random.randint(1, 900)}", "score": int(random.paretovariate(1.3)),
                         "created": now - random.random() * 80000, "text": txt,
                         "url": "https://www.reddit.com/r/wallstreetbets/", "_demo_s": s})
    # trampas de falsos positivos: no deben contarse
    for txt in ["IT is ALL about the DD, YOLO", "CEO said ON the call that AI is the future", "BUY THE DIP NOW GUYS ALL IN"]:
        docs.append({"id": f"t1_x{len(docs)}", "kind": "comment", "sub": "wallstreetbets", "author": "trap",
                     "score": 50, "created": now, "text": txt, "url": "", "_demo_s": "neutral"})
    return docs


def demo_history():
    path = DATA / "history.json"
    hist = json.loads(path.read_text()) if path.exists() else {}
    days = {}
    today = datetime.now(timezone.utc).date()
    random.seed(11)
    for k in range(10, 0, -1):
        d = str(today - timedelta(days=k))
        days[d] = {t: max(3, int(n * random.uniform(0.4, 0.9) * (0.35 if t == "RKLB" else 1)))
                   for t, (n, _) in DEMO_TICKERS.items()}
    hist["demo"] = days
    path.write_text(json.dumps(hist, separators=(",", ":")))


# --------------------------------------------------------------------------- Main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    DATA.mkdir(parents=True, exist_ok=True)
    subs = [s.strip() for s in env("SUBREDDITS", "wallstreetbets,stocks,options").split(",") if s.strip()]
    window = int(env("WINDOW_HOURS", "24"))
    top_n = int(env("AI_TOP_TICKERS", "15"))
    per_t = int(env("AI_MAX_PER_TICKER", "40"))
    model = env("CLAUDE_MODEL", "claude-haiku-5-5")
    universe = load_universe(DATA / "tickers_cache.json",
                             env("SEC_USER_AGENT", "Radar WSB educational project admin@example.com"),
                             allow_small=args.demo)
    now = datetime.now(timezone.utc)
    notes = []

    labels = None
    if args.demo:
        source = "demo"
        for t, name in DEMO_NAMES.items():
            universe.setdefault(t, name)
        docs = demo_docs()
        demo_history()
        ranked, tagged = aggregate(docs, universe)
        labels = {}
        for row in ranked:
            for d in row["_docs"]:
                labels[(d["id"], row["ticker"])] = (d["_demo_s"], random.random() < 0.3)
        notes.append("DATOS SIMULADOS: solo para probar el diseño.")
    else:
        try:
            docs, source = fetch_reddit(subs, window)
            ranked, tagged = aggregate(docs, universe)
            if not ranked:
                raise RuntimeError("Reddit no devolvió menciones")
        except Exception as e:  # noqa: BLE001
            print(f"[reddit] falló ({e}); paso a ApeWisdom")
            notes.append(f"Reddit no respondió ({type(e).__name__}); se usaron conteos de ApeWisdom, sin sentimiento IA.")
            source, docs, tagged = "apewisdom", [], 0
            ranked = [{
                "ticker": r["ticker"], "name": r.get("name", ""), "mentions": int(r["mentions"]),
                "unique_authors": None, "upvotes": int(r.get("upvotes") or 0), "cashtag_pct": None,
                "prev_24h": int(r["mentions_24h_ago"]) if r.get("mentions_24h_ago") else None, "_docs": [],
            } for r in fetch_apewisdom()]
        else:
            labels = classify_ai(ranked, top_n, per_t, model)
            if labels is None:
                notes.append("Sin ANTHROPIC_API_KEY: se muestran menciones sin sentimiento IA.")

    ranked = ranked[:40]
    glob = apply_labels(ranked, labels, top_n)
    if args.demo:
        random.seed(3)
        for r in ranked:
            r["price"] = {"last": round(random.uniform(15, 900), 2), "chg_1d": round(random.gauss(0.5, 3), 2),
                          "chg_5d": round(random.gauss(1, 7), 2), "asof": str(now.date())}
    else:
        add_prices(ranked[:30])
    prev_days = update_history(ranked, source, str(now.date()))
    if prev_days < 2:
        notes.append("Histórico en construcción: la comparación contra el promedio aparece tras 2+ días de datos.")
    for r in ranked:
        r.pop("_docs", None)

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "source": source, "subreddits": subs if source != "apewisdom" else ["wallstreetbets"],
        "window_hours": window,
        "stats": {
            "texts": len(docs), "texts_with_tickers": tagged,
            "posts": sum(d["kind"] == "post" for d in docs),
            "comments": sum(d["kind"] == "comment" for d in docs),
            "authors": len({d["author"] for d in docs}),
        },
        "ai": {"enabled": labels is not None, "model": "simulado" if args.demo else model,
               "classified": glob["n"], "top_n": top_n},
        "global_sentiment": glob if glob["n"] else None,
        "tickers": ranked, "notes": notes,
    }
    (DATA / "latest.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"[ok] {source}: {len(ranked)} tickers, {len(docs)} textos -> docs/data/latest.json")


if __name__ == "__main__":
    main()
