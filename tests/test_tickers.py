import sys; sys.path.insert(0, "scripts")
from tickers import extract
U = {"TSLA": "", "NVDA": "", "IT": "", "ALL": "", "AI": "", "F": "", "GME": "", "MU": "", "DD": "", "ON": "", "OPEN": "", "META": ""}
cases = [
    ("bought $TSLA calls", {"TSLA"}),
    ("TSLA to the moon", {"TSLA"}),
    ("IT is ALL about the DD", set()),
    ("$F is cheap", {"F"}),
    ("F this market", set()),
    ("nvidia earnings tomorrow", {"NVDA"}),
    ("MU looks good", {"MU"}),
    ("AI will change everything", set()),
    ("$AI is a meme stock", {"AI"}),
    ("BUY GME NOW BEFORE IT IS TOO LATE GUYS", set()),
    ("the meta right now is selling puts", set()),
    ("$gme gang", {"GME"}),
]
bad = 0
for text, exp in cases:
    got = set(extract(text, U))
    ok = got == exp
    bad += not ok
    print(("OK  " if ok else "FAIL"), repr(text), got)
print("fallos:", bad); sys.exit(bad)
