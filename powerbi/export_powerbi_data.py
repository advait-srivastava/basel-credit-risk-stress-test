"""Write the CSVs the Power BI dashboard loads, into powerbi/data/.

The dashboard needs every loan in every scenario so it can slice by sector or
rating, which the three-row summary can't do. This rebuilds the seeded book and
reuses basel_stress_test.py's functions, so the numbers can't drift from the script.

  loans.csv             one row per loan: attributes and EAD
  scenarios.csv         one row per scenario: Z, LGD uplift, sort order
  stress_results.csv    one row per loan per scenario: stressed PD/LGD, K, RWA, EL
  scenario_summary.csv  portfolio totals per scenario. Not loaded into the model;
                        it's there to check the DAX measures against.

Run from the repo root:
    python3 powerbi/export_powerbi_data.py
"""
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from basel_stress_test import SCENARIOS, add_ead, loan_results, scenario_summary  # noqa: E402
from generate_portfolio import RATINGS, build_portfolio  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"


def main():
    OUT_DIR.mkdir(exist_ok=True)
    loans = add_ead(build_portfolio())

    rating_rank = {r: i + 1 for i, r in enumerate(RATINGS)}  # so Power BI sorts AAA..CCC, not alphabetically
    loans_out = pd.DataFrame({
        "Loan ID": loans["loan_id"],
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
    })

    scenarios_out = pd.DataFrame([
        {"Scenario": name, "Scenario Order": i + 1,
         "Systematic Factor Z": cfg["Z"], "LGD Uplift": cfg["lgd_uplift"]}
        for i, (name, cfg) in enumerate(SCENARIOS.items())
    ])

    results_out = loan_results(loans)
    summary = scenario_summary(loans)

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
