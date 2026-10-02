"""Export the stress test as flat CSVs for the Power BI dashboard.

Rebuilds the seeded portfolio from generate_portfolio.py and re-runs the same
PD/LGD/EAD -> IRB capital calculation as basel_stress_test.py, but keeps the
loan x scenario detail instead of collapsing it to a scenario summary, so the
dashboard can slice capital and expected loss by sector, rating, seniority, etc.

Writes to powerbi/data/:
  loans.csv            one row per loan (dimension attributes + EAD)
  scenarios.csv        one row per scenario (Z, LGD uplift, sort order)
  stress_results.csv   one row per loan x scenario (stressed PD/LGD, K, RWA, EL)
  scenario_summary.csv portfolio-level capital adequacy per scenario, used to
                       reconcile the dashboard's DAX measures against the script

Run from the repo root:
    python3 powerbi/export_powerbi_data.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from basel_stress_test import (  # noqa: E402
    SCENARIOS, asset_correlation, basel_capital_ratio, capital_adequacy,
    run_scenario, stressed_pd,
)
from generate_portfolio import RATINGS, build_portfolio  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"


def build_loans(portfolio):
    df = portfolio.copy()
    df["ead_inr_cr"] = df["drawn_amount_inr_cr"] + df["ccf"] * df["undrawn_amount_inr_cr"]
    return df


def loan_scenario_results(loans):
    base_pd = loans["base_pd"].to_numpy()
    ead = loans["ead_inr_cr"].to_numpy()
    maturity = loans["maturity_years"].to_numpy()

    frames = []
    for name, cfg in SCENARIOS.items():
        pd_s = stressed_pd(base_pd, asset_correlation(base_pd), cfg["Z"])
        lgd_s = np.clip(loans["base_lgd"].to_numpy() * cfg["lgd_uplift"], 0, 0.95)
        k = basel_capital_ratio(pd_s, lgd_s, maturity)
        frames.append(pd.DataFrame({
            "Loan ID": loans["loan_id"],
            "Scenario": name,
            "Stressed PD": pd_s,
            "Stressed LGD": lgd_s,
            "EAD": ead,
            "Capital K": k,
            "Required Capital": k * ead,
            "RWA": k * ead * 12.5,
            "Expected Loss": pd_s * lgd_s * ead,
        }))
    return pd.concat(frames, ignore_index=True)


def main():
    OUT_DIR.mkdir(exist_ok=True)
    loans = build_loans(build_portfolio())

    rating_rank = {r: i + 1 for i, r in enumerate(RATINGS)}
    loans_out = pd.DataFrame({
        "Loan ID": loans["loan_id"],
        "Borrower ID": loans["borrower_id"],
        "Sector": loans["sector"],
        "Loan Type": loans["loan_type"],
        "Credit Rating": loans["credit_rating"],
        "Rating Rank": loans["credit_rating"].map(rating_rank),
        "Seniority": loans["seniority"],
        "Collateral Type": loans["collateral_type"],
        "Collateral Coverage": loans["collateral_coverage_ratio"],
        "Origination Date": loans["origination_date"].dt.date,
        "Maturity Date": loans["maturity_date"].dt.date,
        "Maturity Years": loans["maturity_years"],
        "Committed Amount": loans["committed_amount_inr_cr"],
        "Drawn Amount": loans["drawn_amount_inr_cr"],
        "Undrawn Amount": loans["undrawn_amount_inr_cr"],
        "CCF": loans["ccf"],
        "EAD": loans["ead_inr_cr"],
        "Base PD": loans["base_pd"],
        "Base LGD": loans["base_lgd"],
        "Interest Rate": loans["interest_rate"],
    })

    scenarios_out = pd.DataFrame([
        {"Scenario": name, "Scenario Order": i + 1,
         "Systematic Factor Z": cfg["Z"], "LGD Uplift": cfg["lgd_uplift"]}
        for i, (name, cfg) in enumerate(SCENARIOS.items())
    ])

    results_out = loan_scenario_results(loans)

    # Same scenario summary basel_stress_test.py prints, for reconciliation.
    summary = pd.DataFrame([
        run_scenario(loans, name, cfg["Z"], cfg["lgd_uplift"]) for name, cfg in SCENARIOS.items()
    ])
    summary = summary.merge(capital_adequacy(summary), on="scenario")

    loans_out.to_csv(OUT_DIR / "loans.csv", index=False, float_format="%.10f")
    scenarios_out.to_csv(OUT_DIR / "scenarios.csv", index=False)
    results_out.to_csv(OUT_DIR / "stress_results.csv", index=False, float_format="%.12f")
    summary.to_csv(OUT_DIR / "scenario_summary.csv", index=False, float_format="%.6f")

    print(f"Wrote {len(loans_out)} loans, {len(scenarios_out)} scenarios, "
          f"{len(results_out)} loan x scenario rows -> {OUT_DIR}")
    pd.options.display.float_format = "{:,.4f}".format
    print(summary.set_index("scenario")[[
        "total_ead_inr_cr", "expected_loss_inr_cr", "rwa_inr_cr",
        "post_stress_capital_ratio", "capital_shortfall_inr_cr",
    ]].to_string())


if __name__ == "__main__":
    main()
