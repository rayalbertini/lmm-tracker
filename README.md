# LMM tracker

Daily snapshot of lead market maker assignments for Cboe BZX, Nasdaq, NYSE Arca and TXSE, harmonised to one asset-class list, with a self-updating dashboard.

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
- NYSE Arca: XLSX at nyse.com/publicdocs/nyse/markets/nyse-arca/NYSE_Arca_Equities_LMM_Current.xlsx.
            Header names are matched case-insensitively against the candidate lists in config.yaml and
            title rows above the header are skipped. Confirm with:  python lmm_pipeline.py --inspect nyse_arca
            If nyse.com returns 403, drop the file in inbox/nyse_arca/ (inbox_fallback is on).
- TXSE:     JSON feed behind the table at txse.com/exchange-traded-products (txse.com/api/etp-products).
            ETPs listed with no LMM yet ("-") are skipped until one is assigned.

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

## Tidal overlay
List Tidal fund tickers (one per row, header `symbol`) in reference/tidal_funds.csv. Every record gets
`is_tidal` (ticker match, case-insensitive). The dashboard then shows a TIDAL badge on each fund, a
"Tidal funds only" filter, and Tidal / % Tidal columns in the LMM grid (Tidal funds / all funds the firm
covers, respecting the exchange filter; hover for the firm's share of all Tidal funds).
After editing the list, run `python lmm_pipeline.py --retag` to re-tag without re-pulling (the daily run
does it automatically). Tickers in the list that appear on no exchange file are flagged in a banner.

## Daily refresh
Push to GitHub, enable Pages (Source: GitHub Actions). daily.yml runs weekdays, appends to
data/history.csv and republishes the dashboard.
