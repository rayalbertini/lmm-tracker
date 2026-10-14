# LMM tracker

Daily snapshot of lead market maker assignments for Cboe BZX and Nasdaq (NYSE Arca once you
have a source), harmonised to one asset-class list, with a self-updating dashboard.

## Quick start
    pip install -r requirements.txt
    python lmm_pipeline.py --demo        # synthetic data, to see the dashboard
    python -m http.server 8000           # open http://localhost:8000/dashboard/
    python lmm_pipeline.py               # real pull (writes data/)

## Sources (config.yaml)
- Cboe BZX: CSV download linked from cboe.com/us/equities/listings/trade_on_cboe/lmm/securities/
- Nasdaq:   HTML table at nasdaqtrader.com/trader.aspx?id=etf_definitions. Nasdaq's term is
            Designated Liquidity Provider (DLP). Secondary "Market Quality Supporter" firms are
            excluded by default; add them under include_roles if you want them.
- NYSE Arca: no public file confirmed. Drop the list into inbox/nyse_arca/ (mode: inbox) and
            fill in `columns` using:  python lmm_pipeline.py --inspect nyse_arca

If a site blocks automated downloads (HTTP 403), set that source to `mode: inbox` and drop the
file in inbox/<source>/. A failed source shows as stale in the dashboard and keeps its last data.

## Asset class
Exchanges label differently (Cboe: US Equity / Outcome-Based / Other; Nasdaq: liquidity buckets).
Resolution order: reference/asset_class_overrides.csv -> class_map -> type_map -> fund-name
keywords -> Unclassified. The exchange's own label stays in the "Exchange label" column.
Review "Unclassified" in the dashboard and add fixes to the overrides file; they stick.

## Firm names
reference/firm_aliases.csv merges variants ("Jane Street" / "Jane Street Capital, LLC") so the
grid combines exchanges. New firm with an odd name? Add one line.

## Daily refresh
Push to GitHub, enable Pages (Source: GitHub Actions). daily.yml runs weekdays, appends to
data/history.csv and republishes the dashboard.
