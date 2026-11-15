"""Detección de tickers con foco en evitar falsos positivos.

Reglas (de más a menos confiable):
1. Cashtag ($TSLA, $tsla): se acepta si el símbolo existe en el universo de tickers.
2. Nombre de empresa conocido ("nvidia", "tesla"): se mapea a su ticker.
3. Palabra suelta en MAYÚSCULAS (TSLA): se acepta solo si
   - existe en el universo,
   - tiene 3 a 5 letras (las de 1-2 letras solo valen como cashtag),
   - no está en la lista negra de palabras comunes / jerga,
   - el comentario no está escrito casi todo en mayúsculas (gritos).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

# ETFs y símbolos populares que no aparecen en el listado de empresas de la SEC.
EXTRA_TICKERS = {
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ (Nasdaq 100)", "IWM": "iShares Russell 2000",
    "VOO": "Vanguard S&P 500 ETF", "VTI": "Vanguard Total Market", "DIA": "SPDR Dow Jones ETF",
    "TQQQ": "ProShares UltraPro QQQ", "SQQQ": "ProShares UltraPro Short QQQ",
    "SPXL": "Direxion S&P 500 Bull 3x", "SPXS": "Direxion S&P 500 Bear 3x",
    "SOXL": "Direxion Semiconductor Bull 3x", "SOXS": "Direxion Semiconductor Bear 3x",
    "UVXY": "ProShares Ultra VIX", "VXX": "iPath VIX Futures", "TLT": "iShares 20+ Yr Treasury",
    "GLD": "SPDR Gold", "SLV": "iShares Silver", "USO": "US Oil Fund", "ARKK": "ARK Innovation ETF",
    "SMH": "VanEck Semiconductor ETF", "XLF": "Financial Select SPDR", "XLE": "Energy Select SPDR",
    "IBIT": "iShares Bitcoin Trust", "TSLL": "Direxion TSLA Bull 2x", "NVDL": "GraniteShares NVDA 2x",
}

# Nombres coloquiales -> ticker (solo nombres poco ambiguos).
ALIASES = {
    "nvidia": "NVDA", "tesla": "TSLA", "apple": "AAPL", "microsoft": "MSFT", "amazon": "AMZN",
    # Se excluyen a propósito palabras ambiguas: "meta" (el meta), "intel" (información),
    # "lucid" (adjetivo), "reddit" (la plataforma, no la acción).
    "google": "GOOGL", "alphabet": "GOOGL", "facebook": "META", "netflix": "NFLX",
    "palantir": "PLTR", "gamestop": "GME", "robinhood": "HOOD", "broadcom": "AVGO",
    "micron": "MU", "coinbase": "COIN", "microstrategy": "MSTR",
    "sofi": "SOFI", "rivian": "RIVN", "nike": "NKE", "disney": "DIS",
    "boeing": "BA", "pfizer": "PFE", "walmart": "WMT", "costco": "COST", "oracle": "ORCL",
    "salesforce": "CRM", "snowflake": "SNOW", "unitedhealth": "UNH", "starbucks": "SBUX",
    "rocket lab": "RKLB", "rocketlab": "RKLB", "super micro": "SMCI", "supermicro": "SMCI",
    "tsmc": "TSM", "opendoor": "OPEN", "nebius": "NBIS",
}

# Palabras en inglés, jerga de WSB y siglas que coinciden con tickers reales.
BLACKLIST = set("""
ALL ARE FOR ONE NOW CAN HAS NEW BIG OPEN REAL RUN SEE TWO LOVE PLAY FUN GOOD BEST
EVER WELL WAY WHO WHY HOW OUT ANY OLD SAY SAID JUST LIKE MAKE MORE MOST VERY HUGE
HOLD HODL MOON YOLO FOMO FUD LMAO LOL WTF IMO IMHO TLDR EDIT POST DD ATH ATL EOD
EOW EOY YTD IPO CEO CFO CTO COO SEC FED FOMC CPI PPI GDP USA USD EUR GBP API ETF
IRA ROTH LLC INC CORP OTM ITM ATM DTE IV PE EPS AH PM AM RSI MACD EMA SMA VWAP
BUY SELL CALL PUT CALLS PUTS LONG SHORT BULL BEAR GAIN LOSS RED GREEN PUMP DUMP
APE APES GAY TOO OFF YES BRO DUDE MAN GUY GUYS LIFE TIME DAY WEEK YEAR CASH
FREE SAFE RISK HIGH LOW TOP PEAK HOPE CARE FAST NEXT LAST GOAT KING MOM DAD WIFE
TRUE FAKE NEWS INFO TECH AI EV IT ON AT AN OR SO GO BE ME MY WE US UP DO NO OK
OUR HIS HER SHE HIM YOU YOUR THEY THEM THAT THIS THEN THAN WHAT WHEN WILL WITH
FROM HAVE BEEN WERE YEAH NOPE NICE COOL EASY HARD PAY PAID RICH POOR BANK FUND
NYSE CNBC WSB OP RIP AF PDT PT TA FA VS CC LEAP LEAPS FD FDS GG HYPE MEME
ANY EVERY ELSE STILL BACK HOME WORK JOB JOBS RATE RATES CUT CUTS LINE PLAN
BOOM BOOK CASE DEAL DROP FALL FIND FIRE GAME GOLD GROW HUGE IDEA KEEP KIDS
LIVE LOOK LOST MIND MOVE NEED PICK PLUS POOL RACE RIDE ROCK SAVE SEND SIGN SNOW
SOLO STAY STEP SURE TALK TEAM TELL TRIP TURN VIEW WAIT WALK WANT WARM WISH ZERO
""".split())

# Tickers de 2 letras que sí se aceptan sin $ porque casi nunca son otra cosa.
ALLOW_BARE_2 = {"MU", "GM", "GE"}

CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})(?![A-Za-z0-9])")
BARE_RE = re.compile(r"(?<![A-Za-z0-9$])([A-Z]{2,5})(?![A-Za-z0-9])")


_SUFFIX_RE = re.compile(r"\s+-\s+(Class [A-Z] )?(Common Stock|Ordinary Shares|American Depositary Shares|"
                        r"Common Shares|Class [A-Z].*|ADS.*|.*Shares.*)$", re.I)


def _from_nasdaqtrader() -> dict[str, str]:
    """Todas las acciones y ETFs que cotizan en bolsas de EE. UU. (sin OTC)."""
    r = requests.get("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt", timeout=30)
    r.raise_for_status()
    out = {}
    lines = r.text.splitlines()
    head = lines[0].split("|")
    i_sym, i_name, i_test = head.index("Symbol"), head.index("Security Name"), head.index("Test Issue")
    for ln in lines[1:]:
        p = ln.split("|")
        if len(p) <= i_test or p[i_test] == "Y" or not p[i_sym].isalpha():
            continue
        out[p[i_sym].upper()] = _SUFFIX_RE.sub("", p[i_name]).strip()
    return out


def _from_sec(user_agent: str) -> dict[str, str]:
    r = requests.get("https://www.sec.gov/files/company_tickers.json",
                     headers={"User-Agent": user_agent}, timeout=30)
    r.raise_for_status()
    return {row["ticker"].upper(): row["title"] for row in r.json().values() if row["ticker"].isalpha()}


def load_universe(cache_path: Path, user_agent: str, allow_small: bool = False) -> dict[str, str]:
    """Devuelve {TICKER: nombre}. Nasdaq Trader -> SEC -> caché. Si todo falla, se detiene."""
    universe: dict[str, str] = {}
    for name, fn in (("Nasdaq Trader", _from_nasdaqtrader), ("SEC", lambda: _from_sec(user_agent))):
        try:
            universe = fn()
            if len(universe) > 3000:
                print(f"[tickers] {len(universe)} símbolos desde {name}")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(universe, separators=(",", ":")))
                break
        except Exception as e:  # noqa: BLE001
            print(f"[tickers] {name} no respondió ({type(e).__name__})")
        universe = {}
    if not universe and cache_path.exists():
        universe = json.loads(cache_path.read_text())
        print(f"[tickers] uso caché: {len(universe)} símbolos")
    if len(universe) < 3000 and not allow_small:
        raise SystemExit("[tickers] No hay lista de tickers confiable; no se publican datos para evitar errores.")
    universe.update(EXTRA_TICKERS)
    return universe


def _mostly_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 25:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.6


def extract(text: str, universe: dict[str, str]) -> dict[str, str]:
    """Devuelve {TICKER: tipo} con tipo en {'cashtag', 'name', 'bare'}."""
    found: dict[str, str] = {}
    if not text:
        return found

    for m in CASHTAG_RE.finditer(text):
        t = m.group(1).upper()
        if t in universe:
            found[t] = "cashtag"

    low = text.lower()
    for alias, t in ALIASES.items():
        if t not in found and re.search(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])", low):
            found[t] = "name"

    if not _mostly_caps(text):
        for m in BARE_RE.finditer(text):
            t = m.group(1)
            if t in found or t in BLACKLIST or t not in universe:
                continue
            if len(t) == 2 and t not in ALLOW_BARE_2:
                continue
            found[t] = "bare"
    return found
