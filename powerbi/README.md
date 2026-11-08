# Power BI dashboard

The script prints three rows, one per scenario. This keeps all 2,250 (750 loans × 3 scenarios), so you can ask which sector or rating is actually behind the shortfall.

It's saved as a Power BI Project (`.pbip`). The model is TMDL and the report is PBIR JSON, both plain text, so changes show up properly in a git diff.

## Opening it

You need [Power BI Desktop](https://aka.ms/pbidesktopstore), which is Windows-only. Open `BaselStressTest.pbip` and click **Refresh**. A `.pbip` doesn't store data, so every visual is empty until that first refresh. If it asks for credentials for `raw.githubusercontent.com`, pick **Anonymous**.

The CSVs are read straight from this repo's `main` branch through the `DataUrl` parameter. To point it at a fork, another branch or a local server, change `DataUrl` under **Transform data → Edit parameters** and keep the trailing slash.

## Pages

| Page | What's on it |
|---|---|
| **Capital Adequacy** | Cards for the selected scenario (EAD, expected loss, RWA, post-stress capital ratio, shortfall), then the capital ratio against the 10.5% line, the shortfall and RWA across all three scenarios |
| **Risk Drivers** | Expected loss by sector and by collateral type, RWA density by rating, and stressed PD by rating and scenario. Slicers for scenario, sector, rating and seniority |
| **Loan Detail** | Stressed PD against stressed LGD for every loan (bubble size is EAD, colour is seniority), next to the loans ranked by expected loss |

## Model

```
Loans (750)  1 ──< Stress Results (2,250) >── 1  Scenarios (3)
```

The measures sit on `Stress Results`, in three folders:

- **Portfolio:** Loan Count, Total EAD
- **Credit Risk:** Expected Loss, Required Capital, RWA, Avg Stressed PD and LGD (EAD-weighted), EL Rate, RWA Density, Baseline Expected Loss, Baseline RWA, EL vs Baseline
- **Capital Adequacy:** Starting Capital Ratio (13%), Minimum Capital Ratio (10.5%), Available Capital, Incremental Expected Loss, Capital After Stress, Post-Stress Capital Ratio, Capital Shortfall, Capital Headroom

The capital measures copy `capital_adequacy()` in `basel_stress_test.py`. Starting capital is 13% of baseline RWA, only expected loss above baseline comes out of it, and the shortfall is measured against 10.5% of stressed RWA. Anything that depends on the scenario returns blank unless exactly one scenario is selected. Without that, a card would happily add Baseline, Adverse and Severely Adverse together.

With no filters, the dashboard should show the same numbers as the script:

| Scenario | Expected loss | RWA | Post-stress capital ratio | Shortfall |
|---|---|---|---|---|
| Baseline | ₹26.2 cr | ₹3,259.8 cr | 13.00% | ₹0.0 cr |
| Adverse | ₹95.1 cr | ₹3,812.0 cr | 9.31% | ₹45.3 cr |
| Severely Adverse | ₹215.6 cr | ₹4,313.8 cr | 5.43% | ₹218.6 cr |

Filter to one sector and the capital figures treat that sector as if it were the whole bank, with its own 13% starting capital. That's handy for comparing sectors. It isn't how capital is actually allocated.

## Regenerating the data

```bash
python3 powerbi/export_powerbi_data.py   # from the repo root
```

It rebuilds the seeded book with the functions in `generate_portfolio.py` and `basel_stress_test.py` and writes to `powerbi/data/`:

| File | One row per |
|---|---|
| `loans.csv` | loan |
| `scenarios.csv` | scenario |
| `stress_results.csv` | loan and scenario |
| `scenario_summary.csv` | scenario, as portfolio totals. Not loaded into the model; use it to check the measures |

The dashboard only sees new CSVs once they're pushed to `main`.
