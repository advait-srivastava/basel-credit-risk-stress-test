# Power BI dashboard

An interactive Power BI version of the stress test. It keeps the full loan × scenario
detail, so capital, RWA and expected loss can be sliced by sector, rating, seniority
and collateral instead of only seen as the three-row scenario summary.

Saved as a Power BI Project (`.pbip`): the semantic model is TMDL and the report is
PBIR JSON, so both diff cleanly in git.

## Open it

1. Install [Power BI Desktop](https://aka.ms/pbidesktopstore) (Windows).
2. Open `BaselStressTest.pbip`.
3. Click **Refresh**. A `.pbip` doesn't store data, so the first open shows empty
   visuals until it refreshes. If prompted for credentials for
   `raw.githubusercontent.com`, choose **Anonymous**.

The CSVs load from this repo's `main` branch on GitHub via the `DataUrl` parameter
(**Transform data → Edit parameters**). To refresh from a fork, a different branch or a
local web server, change `DataUrl` to the folder that holds the CSVs, keeping the
trailing slash.

## Pages

| Page | What it shows |
|---|---|
| **Capital Adequacy** | KPI cards for the selected scenario (EAD, expected loss, RWA, post-stress CET1 ratio, capital shortfall), plus CET1 ratio vs the 10.5% minimum, shortfall and RWA across all three scenarios |
| **Risk Drivers** | Expected loss by sector and by collateral type, RWA density by rating, and a rating × scenario matrix of stressed PD. Slicers for scenario, sector, rating and seniority |
| **Loan Detail** | Stressed PD vs stressed LGD for every loan (bubble size = EAD, colour = seniority) next to a table of loans ranked by expected loss |

## Model

```
Loans (750)  1 ──< Stress Results (2,250 = 750 loans × 3 scenarios) >── 1  Scenarios (3)
```

Measures live on `Stress Results`, grouped into display folders:

- **Portfolio:** Loan Count, Total EAD
- **Credit Risk:** Expected Loss, Required Capital, RWA, Avg Stressed PD / LGD (EAD-weighted), EL Rate, RWA Density, Baseline Expected Loss / RWA, EL vs Baseline
- **Capital Adequacy:** Starting CET1 Ratio (13%), Minimum CET1 Ratio (10.5%), Available Capital, Incremental Expected Loss, Capital After Stress, Post-Stress CET1 Ratio, Capital Shortfall, CET1 Headroom

The capital adequacy measures use the same logic as `capital_adequacy()` in
`basel_stress_test.py`: available capital is 13% of baseline RWA, only expected loss
above baseline reduces capital, and the shortfall is measured against 10.5% of
stressed RWA. Scenario-dependent measures return blank unless exactly one scenario is
in context, so they never add the three scenarios together.

With no filters applied, the dashboard should match the script:

| Scenario | Expected loss | RWA | Post-stress CET1 | Capital shortfall |
|---|---|---|---|---|
| Baseline | ₹26.2 cr | ₹2,879.6 cr | 13.00% | ₹0.0 cr |
| Adverse | ₹95.1 cr | ₹5,886.7 cr | 5.19% | ₹312.6 cr |
| Severely Adverse | ₹215.6 cr | ₹8,802.6 cr | 2.10% | ₹739.3 cr |

Filtering by sector, rating, etc. recomputes these figures for that slice of the
portfolio, as if it were a standalone book.

## Regenerating the data

```bash
python3 powerbi/export_powerbi_data.py   # run from the repo root
```

This rebuilds the seeded portfolio and reruns the stress test using the functions in
`generate_portfolio.py` and `basel_stress_test.py`, then writes the following to `powerbi/data/`:

| File | Grain |
|---|---|
| `loans.csv` | one row per loan |
| `scenarios.csv` | one row per scenario |
| `stress_results.csv` | one row per loan × scenario |
| `scenario_summary.csv` | portfolio totals per scenario, for reconciling the dashboard against the script (not loaded into the model) |

Commit the CSVs and push to `main`; the next refresh in Power BI picks them up.
