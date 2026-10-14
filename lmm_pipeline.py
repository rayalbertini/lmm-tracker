#!/usr/bin/env python3
"""
Daily lead-market-maker snapshot for Cboe, Nasdaq and NYSE Arca.

  python lmm_pipeline.py                 # run all sources
  python lmm_pipeline.py --only cboe     # run one source
  python lmm_pipeline.py --inspect cboe  # show headers/first rows found, change nothing
  python lmm_pipeline.py --demo          # synthetic data to try the dashboard

Outputs (under data/):
  history.csv       one row per (as_of, exchange, symbol, lmm), append-only
  lmm_latest.json   what the dashboard reads (records, changes, source health)
"""
from __future__ import annotations
import argparse, glob, io, json, os, re, sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import yaml

ROOT = Path(__file__).parent
DATA = ROOT / "data"
HISTORY = DATA / "history.csv"
LATEST = DATA / "lmm_latest.json"
STD_COLS = ["as_of", "exchange", "symbol", "fund_name", "lmm", "lmm_raw", "role", "asset_class", "source_class"]

# ------------------------------------------------------------- asset class
# Last-resort keyword match on the fund name. Ordered: first match wins.
KEYWORDS = [
    ("Digital Assets", r"bitcoin|\bbtc\b|ether(eum)?\b|crypto|blockchain|solana|\bxrp\b|digital asset"),
    ("Volatility",     r"\bvix\b|volatility index|vol\b"),
    ("Outcome-Based",  r"buffer|floor\d|defined outcome|structured alt|protection etf|accelerated"),
    ("Commodity",      r"\bgold\b|silver|platinum|palladium|\boil\b|crude|natural gas|commodit|copper|agricultur|wheat|\bcorn\b"),
    ("Currency",       r"currency|dollar index|\beuro\b|\byen\b"),
    ("Fixed Income",   r"bond|treasur|fixed income|aggregate|municipal|\bmuni|credit|high yield|t-bill|floating rate|\btips\b|\bclo\b|\bloan"),
    ("Multi-Asset",    r"allocation|multi-asset|target date|balanced"),
    ("Real Estate",    r"real estate|\breit\b"),
    ("Equity",         r"equity|equities|stock|s&p|nasdaq|russell|\bdow\b|msci|dividend|growth|value|small.?cap|mid.?cap|large.?cap|\bsector\b"),
]
KEYWORDS = [(c, re.compile(p, re.I)) for c, p in KEYWORDS]


def _apply_map(value: str, rules: list[dict] | None) -> str | None:
    for r in rules or []:
        if re.search(r["match"], value or "", re.I):
            return r["to"]
    return None


def classify(symbol, name, source_class, typ, scfg, overrides) -> str:
    return (overrides.get(symbol)
            or _apply_map(source_class, scfg.get("class_map"))
            or _apply_map(typ, scfg.get("type_map"))
            or next((c for c, rx in KEYWORDS if rx.search(name or "")), None)
            or "Unclassified")


def load_overrides() -> dict[str, str]:
    p = ROOT / "reference" / "asset_class_overrides.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p, dtype=str).dropna()
    return dict(zip(df["symbol"].str.upper().str.strip(), df["asset_class"].str.strip()))


# ---------------------------------------------------------- firm names
_SUFFIX = re.compile(r"[,\s]+(L\.?L\.?C\.?|Inc\.?|L\.?P\.?|Ltd\.?|Corp\.?)\s*$", re.I)


def load_aliases() -> list[tuple[re.Pattern, str]]:
    p = ROOT / "reference" / "firm_aliases.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype=str).dropna()
    return [(re.compile(a, re.I), c) for a, c in zip(df["pattern"], df["canonical"])]


def canonical_firm(raw: str, aliases) -> str:
    for rx, canon in aliases:
        if rx.search(raw):
            return canon
    return _SUFFIX.sub("", raw).strip()


# ------------------------------------------------------------- fetching
def _read(raw: bytes, fmt: str, label: str) -> pd.DataFrame:
    if fmt == "csv":
        return pd.read_csv(io.BytesIO(raw), dtype=str, encoding_errors="replace")
    if fmt == "xlsx":
        return pd.read_excel(io.BytesIO(raw), dtype=str)
    if fmt == "html_table":
        tables = pd.read_html(io.StringIO(raw.decode("utf-8", errors="replace")))
        if not tables:
            raise ValueError(f"{label}: no HTML tables found")
        return max(tables, key=len).astype(str).replace("nan", "")  # biggest table = the data
    if fmt == "json":
        return pd.json_normalize(json.loads(raw))
    raise ValueError(f"{label}: unknown format {fmt!r}")


def fetch_raw(cfg: dict, key: str, http: dict) -> tuple[bytes, str]:
    if cfg.get("mode", "url") == "inbox":
        files = [f for f in glob.glob(str(ROOT / "inbox" / key / "*")) if not f.endswith(".gitkeep")]
        if not files:
            raise FileNotFoundError(f"inbox/{key}/ is empty - drop the latest download there")
        newest = max(files, key=os.path.getmtime)
        return Path(newest).read_bytes(), f"inbox file {Path(newest).name}"
    if not cfg.get("url"):
        raise ValueError("no url set in config.yaml")
    r = requests.get(cfg["url"], headers={"User-Agent": http["user_agent"]}, timeout=http["timeout_seconds"])
    if r.status_code in (401, 403):
        raise PermissionError(f"HTTP {r.status_code}: site blocked the automated download. "
                              f"Switch this source to mode: inbox and drop the file in inbox/{key}/")
    r.raise_for_status()
    return r.content, cfg["url"]


def normalise(df: pd.DataFrame, cfg: dict, as_of: str, overrides: dict, aliases) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    colmap = cfg["columns"]
    missing = [v for v in colmap.values() if v not in df.columns]
    if missing:
        raise KeyError(f"columns {missing} not found - exchange may have changed its layout. Headers present: {list(df.columns)}")
    out = pd.DataFrame({std: df[src].fillna("").astype(str).str.strip() for std, src in colmap.items()})
    for c in ("fund_name", "source_class", "type", "role"):
        if c not in out:
            out[c] = ""
    if "role" not in colmap:
        out["role"] = cfg.get("role_label", "Lead Market Maker")
    if cfg.get("include_roles"):
        out = out[out["role"].isin(cfg["include_roles"])]
    out["symbol"] = out["symbol"].str.upper()
    out = out[(out.symbol != "") & (out.lmm != "") & (out.lmm.str.lower() != "nan")]
    out = out.assign(lmm_raw=out["lmm"].str.split(r"\s*[;|]\s*")).explode("lmm_raw")
    out["lmm"] = out["lmm_raw"].map(lambda s: canonical_firm(s, aliases))
    out["asset_class"] = [classify(s, n, sc, t, cfg, overrides)
                          for s, n, sc, t in zip(out.symbol, out.fund_name, out.source_class, out.type)]
    out["exchange"] = cfg["label"]
    out["as_of"] = as_of
    return out[STD_COLS].drop_duplicates()


# -------------------------------------------------------------- history
def load_history() -> pd.DataFrame:
    if HISTORY.exists():
        return pd.read_csv(HISTORY, dtype=str).fillna("").reindex(columns=STD_COLS, fill_value="")
    return pd.DataFrame(columns=STD_COLS)


def diff_against_previous(hist: pd.DataFrame, today: pd.DataFrame, exch: str, as_of: str) -> list[dict]:
    prev_days = sorted(d for d in hist.loc[hist.exchange == exch, "as_of"].unique() if d < as_of)
    if not prev_days:
        return []
    prev = hist[(hist.exchange == exch) & (hist.as_of == prev_days[-1])]
    p = prev.groupby("symbol")["lmm"].apply(lambda s: sorted(set(s))).to_dict()
    t = today.groupby("symbol")["lmm"].apply(lambda s: sorted(set(s))).to_dict()
    names = {**dict(zip(prev.symbol, prev.fund_name)), **dict(zip(today.symbol, today.fund_name))}
    rows = []
    for s in sorted(set(p) | set(t)):
        base = dict(exchange=exch, symbol=s, fund_name=names.get(s, ""))
        if s not in p:
            rows.append(dict(type="added", to=", ".join(t[s]), **base))
        elif s not in t:
            rows.append(dict(type="removed", frm=", ".join(p[s]), **base))
        elif p[s] != t[s]:
            rows.append(dict(type="changed", frm=", ".join(p[s]), to=", ".join(t[s]), **base))
    return rows


# ----------------------------------------------------------------- main
def run(only, inspect, cfg_path) -> int:
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    overrides, aliases = load_overrides(), load_aliases()
    DATA.mkdir(exist_ok=True)
    as_of = date.today().isoformat()

    if inspect:
        scfg = cfg["sources"][inspect]
        raw, src = fetch_raw(scfg, inspect, cfg["http"])
        df = _read(raw, scfg["format"], inspect)
        print(f"Source: {src}\nHeaders: {list(df.columns)}\nRows: {len(df)}\n{df.head(5).to_string()}")
        return 0

    hist = load_history()
    prev_latest = json.loads(LATEST.read_text()) if LATEST.exists() else {"sources": {}, "changes": []}
    frames, changes, status = [], [], {}

    for key, scfg in cfg["sources"].items():
        if only and key != only:
            continue
        try:
            raw, src = fetch_raw(scfg, key, cfg["http"])
            df = normalise(_read(raw, scfg["format"], scfg["label"]), scfg, as_of, overrides, aliases)
            if df.empty:
                raise ValueError("parsed 0 rows - layout or filters need checking")
            changes += diff_against_previous(hist, df, scfg["label"], as_of)
            frames.append(df)
            status[key] = dict(label=scfg["label"], ok=True, rows=len(df), as_of=as_of, source=src, error=None)
            print(f"[ok]   {scfg['label']}: {len(df)} rows from {src}")
        except Exception as e:  # keep going; dashboard marks this source stale
            last_ok = prev_latest.get("sources", {}).get(key, {}).get("as_of")
            status[key] = dict(label=scfg["label"], ok=False, rows=0, as_of=last_ok, source=None, error=str(e))
            print(f"[FAIL] {scfg['label']}: {e}", file=sys.stderr)

    if frames:
                fresh = pd.concat(frames)
        # a rerun on the same day replaces that exchange's rows for the day (so renamed firms don't linger)
        hist = hist[~(hist["as_of"].eq(as_of) & hist["exchange"].isin(fresh["exchange"].unique()))]
        hist = pd.concat([hist, fresh]).drop_duplicates(subset=["as_of", "exchange", "symbol", "lmm"], keep="last")
        hist.sort_values(["as_of", "exchange", "symbol"]).to_csv(HISTORY, index=False)

    # latest = newest snapshot per exchange, so a failed source falls back to its last good day
    parts = [g[g.as_of == g.as_of.max()] for _, g in hist.groupby("exchange")]
    latest = pd.concat(parts) if parts else pd.DataFrame(columns=STD_COLS)
    # a failed source keeps its previously reported changes; fresh runs replace them
    kept = [c for c in prev_latest.get("changes", []) if c["exchange"] not in {s["label"] for s in status.values() if s["ok"]}]
    payload = dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        sources={**prev_latest.get("sources", {}), **status},
        changes=kept + changes,
        records=latest[STD_COLS].to_dict("records"),
    )
    LATEST.write_text(json.dumps(payload, indent=1))
    return 1 if any(not s["ok"] for s in status.values()) else 0


def demo() -> None:
    """Synthetic data so you can see the dashboard. NOT real assignments."""
    import random
    random.seed(7)
    funds = [("SPY","SPDR S&P 500 ETF Trust","Equity"),("QQQ","Invesco QQQ Trust","Equity"),("GLD","SPDR Gold Shares","Commodity"),
             ("TLT","iShares 20+ Year Treasury Bond ETF","Fixed Income"),("IBIT","iShares Bitcoin Trust ETF","Digital Assets"),
             ("HYG","iShares iBoxx High Yield Corporate Bond ETF","Fixed Income"),("VNQ","Vanguard Real Estate ETF","Real Estate"),
             ("AOR","iShares Core Growth Allocation ETF","Multi-Asset"),("ARKK","ARK Innovation ETF","Equity"),
             ("APRB","Aptus April Buffer ETF","Outcome-Based"),("EFA","iShares MSCI EAFE ETF","Equity"),("ETHA","iShares Ethereum Trust ETF","Digital Assets"),
             ("VXX","iPath S&P 500 VIX Short-Term Futures ETN","Volatility"),("FXE","Invesco CurrencyShares Euro Trust","Currency")]
    firms = ["Jane Street","Susquehanna","Virtu","Flow Traders","IMC","Citadel Securities","GTS","Wolverine","RBC Capital Markets"]
    rows = []
    for ex, sample in [("Cboe BZX", funds[:9]), ("Nasdaq", funds[4:]), ("NYSE Arca", funds[::2])]:
        for s, n, ac in sample:
            f = random.choice(firms)
            rows.append(dict(as_of=date.today().isoformat(), exchange=ex, symbol=s, fund_name=n, lmm=f, lmm_raw=f,
                             role="Lead Market Maker", asset_class=ac, source_class=ac))
    DATA.mkdir(exist_ok=True)
    pd.DataFrame(rows).to_csv(HISTORY, index=False)
    LATEST.write_text(json.dumps(dict(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), demo=True,
        sources={k: dict(label=l, ok=True, rows=0, as_of=date.today().isoformat(), source="demo", error=None)
                 for k, l in [("cboe","Cboe BZX"),("nasdaq","Nasdaq"),("nyse_arca","NYSE Arca")]},
        changes=[dict(type="changed", exchange="Nasdaq", symbol="HYG", fund_name="iShares iBoxx High Yield Corporate Bond ETF", frm="Virtu", to="IMC"),
                 dict(type="added", exchange="Cboe BZX", symbol="ETHA", fund_name="iShares Ethereum Trust ETF", to="Flow Traders")],
        records=rows), indent=1))
    print("demo data written to data/")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--only"); ap.add_argument("--inspect"); ap.add_argument("--demo", action="store_true")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    a = ap.parse_args()
    if a.demo:
        demo(); sys.exit(0)
    sys.exit(run(a.only, a.inspect, a.config))
