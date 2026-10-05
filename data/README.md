# Data

Nothing in this folder is tracked by git. The market data belongs to GSE/GFIM, the Bank of
Ghana and the Ghana Statistical Service, and the trading reports alone run to about 450 MB.
Download your own copies and the pipeline will fill these folders.

## Expected layout

```
data/
  raw/
    GFIM_Reports/
      2023/ TRADING-REPORT-FOR-GFIM-03012023.xlsx ...
      2024/ ...
      2025/ ...
      2026/ ...
    reference/
      New_GoG_Notes__Bonds.csv
      Old_GoG_Notes_and_Bonds.xlsx
      Corporate_Bonds_GoG.csv
      ACTIVE_SECURITIES_-_Government.xlsx
      Treasury_Bill_Rates.xlsx
      Historical_Policy_Rate_Decisions.xlsx
      Time_Series_Monthly.xlsx
  processed/            created by the pipeline
    parsed.pkl
    curve_inputs.pkl
    fits_all.pkl
    Ghana_Yield_Curve_Dataset_2023_2026.xlsx
```

## Where each file comes from

**Daily trading reports.** gfim.com.gh/daily-trading-reports, one spreadsheet per trading
day, organised by year tabs. `src/gfim_download.py` pulls them. The naming pattern is
`TRADING-REPORT-FOR-GFIM-DDMMYYYY.xlsx` under `wp-content/uploads/YYYY/MM/`, though a
handful of days deviate from it.

Each file holds one sheet per market segment. Sheet names have changed over time: what was
a single GOG-NOTES & BONDS sheet in early 2023 later split into NEW GOG NOTES AND BONDS,
OLD GOG NOTES AND BONDS and DDEP BONDS. Column layouts shift too, which is why the parser
matches columns by header text rather than position.

Two traps in these files. The days-to-maturity column is an Excel formula against TODAY(),
so it is wrong for any historical date and the parser recomputes it. And the sell/buy-back
sheet is repo financing, not outright trading, so those yields are financing rates and are
kept separate.

**Security static data.** The GFIM site lists new GoG notes and bonds, the old pre-exchange
list, and corporate bonds, each with ISIN, coupon, maturity and tenor. Coupons arrive in
three different formats across those files (10.00%, 0.215, and locale-formatted 1.900,00),
all handled in `build_dataset.py`. The static tables cover 71 of the 96 sovereign bond ISINs
seen in trading; the rest are recovered from the coupon suffix in the security description,
for example GOG-BD-17/08/27-A6139-1838-10.00 implies a 10 percent coupon.

**Treasury bill auction results.** bog.gov.gh/treasury-and-the-markets/treasury-bill-rates,
exported as a spreadsheet with issue date, tender number, security type, discount rate and
interest rate. Set the date range to cover the whole study period; the default export stops
earlier than you expect.

**Policy rate.** Bank of Ghana publishes every MPC decision with its effective date.

**Inflation.** The Bank of Ghana monthly time series export stopped in April 2023 when this
project was built, so the rest was compiled from Ghana Statistical Service CPI releases.
Each GSS release prints a table of the previous 13 months, so a handful of releases covers
several years. Every month in the dataset carries a source label.
