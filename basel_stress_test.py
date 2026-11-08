"""Stress the loan book through three scenarios and measure the capital shortfall.

For each scenario (Baseline, Adverse, Severely Adverse):

  1. Shift every loan's PD with the Vasicek single-factor model. One systematic
     factor Z, the state of the economy, moves all borrowers at once.
  2. Scale LGD up for the downturn, since collateral sells for less in a recession.
  3. Migrate loans across rating grades with the same Z, then charge Basel
     corporate IRB capital at the long-run PD of whichever grade each loan
     lands in.
  4. Take expected loss (from the stressed PD) out of capital, divide by
     stressed RWA, and compare the ratio with the 10.5% minimum.

Why step 3 doesn't just feed the stressed PD into the IRB formula: the formula
expects a long-run PD and pushes it out to the 99.9th percentile on its own.
Giving it a recession PD would stress the same factor twice, and in an earlier
version of this repo that tripled RWA.

Inputs come from loan_portfolio.xlsx (run generate_portfolio.py first).
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

from generate_portfolio import AS_OF_DATE, RATING_PD, RATINGS

INPUT_FILE = "loan_portfolio.xlsx"
OUTPUT_FILE = "stress_test_results.xlsx"
CHART_FILE = "capital_shortfall.png"

CONFIDENCE = 0.999          # IRB solvency standard
# Ratios are total capital / RWA. (The CET1 version of the minimum would be 4.5% + 2.5% = 7%.)
CAPITAL_RATIO_START = 0.13  # assumed, not taken from any real bank
CAPITAL_RATIO_MIN = 0.105   # 8% Pillar 1 + 2.5% capital conservation buffer
PD_FLOOR = 0.0005           # Basel III corporate PD floor

# Z is a standard normal draw for the economy: 0 is a median year, negative is a recession.
# lgd_uplift multiplies each loan's base LGD.
SCENARIOS = {
    "Baseline":         {"Z": 0.0, "lgd_uplift": 1.00},
    "Adverse":          {"Z": -1.5, "lgd_uplift": 1.15},
    "Severely Adverse": {"Z": -2.5, "lgd_uplift": 1.30},
}

# One-year transition matrix in %, rows = from, columns = to, default left out. The numbers are
# made up to look like S&P's long-run corporate averages, not taken from their tables.
# migration_probs rescales each row to 1 - PD, so the default column is always the loan's own PD.
TRANSITIONS = np.array([
    #  AAA    AA     A      BBB    BB     B      CCC
    [90.80,  8.30,  0.70,  0.06,  0.08,  0.03,  0.03],   # AAA
    [ 0.60, 90.60,  7.90,  0.60,  0.06,  0.10,  0.02],   # AA
    [ 0.05,  2.00, 91.90,  5.40,  0.40,  0.15,  0.04],   # A
    [ 0.02,  0.20,  4.00, 91.00,  3.90,  0.70,  0.15],   # BBB
    [ 0.02,  0.05,  0.20,  5.50, 84.50,  8.00,  1.00],   # BB
    [ 0.00,  0.05,  0.15,  0.25,  6.00, 84.50,  4.50],   # B
    [ 0.00,  0.00,  0.20,  0.30,  1.00, 13.00, 70.00],   # CCC
])
GRADE_PD = np.array([RATING_PD[r] for r in RATINGS])


def asset_correlation(pd_):
    """Basel corporate asset correlation: 24% for the safest names, falling to 12% as PD rises."""
    w = (1 - np.exp(-50 * pd_)) / (1 - np.exp(-50))
    return 0.12 * w + 0.24 * (1 - w)


def maturity_adjustment(pd_, maturity):
    m = np.clip(maturity, 1, 5)
    b = (0.11852 - 0.05478 * np.log(pd_)) ** 2
    return (1 + (m - 2.5) * b) / (1 - 1.5 * b)


def stressed_pd(pd_, rho, z):
    """PD in a year where the economy draws z (Vasicek)."""
    return norm.cdf((norm.ppf(pd_) - np.sqrt(rho) * z) / np.sqrt(1 - rho))


def migration_probs(base_pd, grade, rho, z):
    """Where each loan ends the year, given z: (P(default), P(each grade) in RATINGS order).

    The loan's matrix row is turned into asset-return cut-offs, and z shifts the
    asset return exactly as in stressed_pd. So P(default) here always equals
    stressed_pd(base_pd, rho, z); the rest of the probability is spread over grades.
    """
    row = TRANSITIONS[grade]
    row = row / row.sum(axis=1, keepdims=True) * (1 - base_pd)[:, None]
    # Cumulative from the bottom up: default, CCC, B, ..., AAA.
    cum = np.cumsum(np.column_stack([base_pd, row[:, ::-1]]), axis=1)
    thresholds = norm.ppf(np.clip(cum, 0, 1))
    cond = norm.cdf((thresholds - np.sqrt(rho)[:, None] * z) / np.sqrt(1 - rho)[:, None])
    cond[:, -1] = 1.0
    probs = np.diff(cond, axis=1, prepend=0.0)
    return probs[:, 0], probs[:, 1:][:, ::-1]


def basel_capital_ratio(pd_, lgd, maturity):
    """IRB capital K per unit of EAD: unexpected loss at 99.9%, adjusted for maturity."""
    pd_ = np.maximum(pd_, PD_FLOOR)
    rho = asset_correlation(pd_)
    ma = maturity_adjustment(pd_, maturity)
    wcdr = norm.cdf((norm.ppf(pd_) + np.sqrt(rho) * norm.ppf(CONFIDENCE)) / np.sqrt(1 - rho))
    k = lgd * (wcdr - pd_) * ma
    return np.clip(k, 0, None)


def add_ead(df):
    """EAD = drawn + CCF x undrawn. Not stressed, so the same in every scenario."""
    df = df.copy()
    df["ead_inr_cr"] = df["drawn_amount_inr_cr"] + df["ccf"] * df["undrawn_amount_inr_cr"]
    return df


def load_portfolio(path=INPUT_FILE):
    return add_ead(pd.read_excel(path, sheet_name="Loans"))


def loan_metrics(df, z, lgd_uplift):
    """Stressed PD and LGD, capital ratio K and expected loss per loan, for one scenario."""
    base_pd = df["base_pd"].to_numpy()
    rho = asset_correlation(base_pd)
    grade = df["credit_rating"].map(RATINGS.index).to_numpy()
    lgd_s = np.clip(df["base_lgd"].to_numpy() * lgd_uplift, 0, 0.95)
    # Basel's M is time left to maturity, not the original term.
    maturity = ((pd.to_datetime(df["maturity_date"]) - AS_OF_DATE).dt.days / 365.25).to_numpy()

    pd_s = stressed_pd(base_pd, rho, z)
    _, p_grades = migration_probs(base_pd, grade, rho, z)
    # Long-run PD of every grade the loan could move to, keeping its own +/-15% offset.
    grade_pd = GRADE_PD[None, :] * (base_pd / GRADE_PD[grade])[:, None]
    k_grades = basel_capital_ratio(grade_pd, lgd_s[:, None], maturity[:, None])
    # Loans that default carry no K here. Their loss hits capital through expected loss instead.
    k = (p_grades * k_grades).sum(axis=1)

    return {"pd": pd_s, "lgd": lgd_s, "k": k, "el": pd_s * lgd_s * df["ead_inr_cr"].to_numpy()}


def loan_results(df):
    """One row per loan per scenario. Column names match the Power BI model."""
    ead = df["ead_inr_cr"].to_numpy()
    frames = []
    for name, cfg in SCENARIOS.items():
        m = loan_metrics(df, cfg["Z"], cfg["lgd_uplift"])
        frames.append(pd.DataFrame({
            "Loan ID": df["loan_id"],
            "Scenario": name,
            "Stressed PD": m["pd"],
            "Stressed LGD": m["lgd"],
            "EAD": ead,
            "Capital K": m["k"],
            "Required Capital": m["k"] * ead,
            "RWA": m["k"] * ead * 12.5,
            "Expected Loss": m["el"],
        }))
    return pd.concat(frames, ignore_index=True)


def run_scenario(df, name, z, lgd_uplift):
    m = loan_metrics(df, z, lgd_uplift)
    ead = df["ead_inr_cr"].to_numpy()
    required_capital = m["k"] * ead          # INR cr

    return pd.Series({
        "scenario": name,
        "avg_stressed_pd": np.average(m["pd"], weights=ead),
        "avg_stressed_lgd": np.average(m["lgd"], weights=ead),
        "total_ead_inr_cr": ead.sum(),
        "expected_loss_inr_cr": m["el"].sum(),
        "required_capital_inr_cr": required_capital.sum(),
        "rwa_inr_cr": required_capital.sum() * 12.5,
    })


def capital_adequacy(results):
    """Capital left after each scenario, the ratio it gives, and how far short of 10.5% that is.

    Starting capital is 13% of baseline RWA. Baseline EL counts as already provisioned,
    so only the extra EL in a scenario comes out of capital.
    """
    baseline = results.loc[results["scenario"] == "Baseline"].iloc[0]
    available_capital = CAPITAL_RATIO_START * baseline["rwa_inr_cr"]

    rows = []
    for _, r in results.iterrows():
        incremental_loss = r["expected_loss_inr_cr"] - baseline["expected_loss_inr_cr"]
        capital_after_stress = available_capital - max(incremental_loss, 0.0)
        rows.append({
            "scenario": r["scenario"],
            "capital_after_stress_inr_cr": capital_after_stress,
            "post_stress_capital_ratio": capital_after_stress / r["rwa_inr_cr"],
            "capital_shortfall_inr_cr": max(0.0, CAPITAL_RATIO_MIN * r["rwa_inr_cr"] - capital_after_stress),
        })
    return pd.DataFrame(rows)


def scenario_summary(df):
    results = pd.DataFrame([
        run_scenario(df, name, cfg["Z"], cfg["lgd_uplift"]) for name, cfg in SCENARIOS.items()
    ])
    return results.merge(capital_adequacy(results), on="scenario")


def main():
    df = load_portfolio()
    print(f"Loaded {len(df)} loans, total EAD INR {df['ead_inr_cr'].sum():,.1f} cr\n")

    summary = scenario_summary(df)
    pd.options.display.float_format = "{:,.4f}".format
    print(summary.set_index("scenario")[[
        "avg_stressed_pd", "expected_loss_inr_cr", "rwa_inr_cr",
        "post_stress_capital_ratio", "capital_shortfall_inr_cr",
    ]].to_string())

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Scenario_Summary", index=False)
        loan_results(df).to_excel(writer, sheet_name="Loan_Results", index=False)
    print(f"\nSaved results -> {OUTPUT_FILE}")

    make_chart(summary)
    print(f"Saved chart -> {CHART_FILE}")


def make_chart(summary):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    ax = axes[0]
    ax.bar(summary["scenario"], summary["post_stress_capital_ratio"] * 100, color="#2f6f5e")
    ax.axhline(CAPITAL_RATIO_MIN * 100, color="#b23a2f", linestyle="--", label=f"Minimum ({CAPITAL_RATIO_MIN:.1%})")
    ax.set_ylabel("Post-stress total capital ratio (%)")
    ax.set_title("Capital ratio by scenario")
    ax.legend()

    ax = axes[1]
    ax.bar(summary["scenario"], summary["capital_shortfall_inr_cr"], color="#b23a2f")
    ax.set_ylabel("Capital shortfall (INR cr)")
    ax.set_title("Capital shortfall by scenario")

    fig.tight_layout()
    fig.savefig(CHART_FILE, dpi=150)


if __name__ == "__main__":
    main()
