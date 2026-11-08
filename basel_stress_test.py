"""Basel III credit risk stress test: PD/LGD/EAD -> IRB capital -> capital shortfall.

Reads loan_portfolio.xlsx (see generate_portfolio.py), then for a baseline and
two recession scenarios:

  1. Stresses each loan's PD with the single-factor Vasicek/Merton model used
     by Basel's IRB formula (BCBS, "An Explanatory Note on the Basel II IRB
     Risk Weight Functions") -- a systematic factor Z shifts every obligor's
     conditional default probability together, which is how a recession
     shows up in the model.
  2. Applies a downturn LGD uplift (collateral haircuts widen in a downturn).
  3. Recomputes EAD (drawn + CCF x undrawn -- held constant across scenarios).
  4. Migrates each loan across rating grades with the same factor Z
     (conditional transition matrix, see migration_probs) and runs the Basel
     corporate IRB capital formula on the post-migration grades' long-run PDs
     to get required Pillar 1 capital. Expected loss uses the stressed PD
     from step 1.
  5. Aggregates to the portfolio level and compares stressed capital against
     an assumed starting capital base to quantify the capital shortfall
     against the regulatory minimum ratio.

The IRB formula expects a long-run average PD and stresses it to the 99.9th
percentile itself, so the scenario's conditional PD from step 1 is used for
losses only. Feeding it into the capital formula would stress the same
systematic factor twice; rating migration is how the scenario reaches RWA.

Simplifications (see README): CCF is not stressed, starting capital is an
assumed ratio rather than an actual reported CET1 figure, and baseline EL is
assumed already provisioned so only incremental stress losses hit capital.
"""
import numpy as np
import pandas as pd
from scipy.stats import norm

from generate_portfolio import RATING_PD, RATINGS

INPUT_FILE = "loan_portfolio.xlsx"
OUTPUT_FILE = "stress_test_results.xlsx"
CHART_FILE = "capital_shortfall.png"

CONFIDENCE = 0.999          # Basel IRB target solvency confidence
CAPITAL_RATIO_START = 0.13  # bank's assumed starting CET1 ratio (above the regulatory minimum)
CAPITAL_RATIO_MIN = 0.105   # 8% Pillar 1 + 2.5% capital conservation buffer

# Scenarios: Z is the standardized systematic risk factor (0 = average conditions,
# negative = recession). lgd_uplift scales base LGD to a "downturn LGD".
SCENARIOS = {
    "Baseline":         {"Z": 0.0, "lgd_uplift": 1.00},
    "Adverse":          {"Z": -1.5, "lgd_uplift": 1.15},
    "Severely Adverse": {"Z": -2.5, "lgd_uplift": 1.30},
}

# Illustrative one-year rating transition matrix (rows: from, columns: to, in RATINGS
# order, default excluded), shaped like S&P's long-run global corporate averages.
# migration_probs rescales each row to 1 - PD, so the default column always agrees
# with the loan's own base PD.
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
    """Basel corporate IRB asset correlation rho(PD)."""
    w = (1 - np.exp(-50 * pd_)) / (1 - np.exp(-50))
    return 0.12 * w + 0.24 * (1 - w)


def maturity_adjustment(pd_, maturity):
    m = np.clip(maturity, 1, 5)
    b = (0.11852 - 0.05478 * np.log(pd_)) ** 2
    return (1 + (m - 2.5) * b) / (1 - 1.5 * b)


def stressed_pd(pd_, rho, z):
    """Conditional PD given systematic factor z (Vasicek single-factor model)."""
    return norm.cdf((norm.ppf(pd_) - np.sqrt(rho) * z) / np.sqrt(1 - rho))


def migration_probs(base_pd, grade, rho, z):
    """End-of-year default probability and grade distribution given systematic factor z.

    CreditMetrics-style: the transition matrix row sets asset-return thresholds,
    and z shifts the obligor's asset return the same way stressed_pd does, so
    the default probability returned equals stressed_pd(base_pd, rho, z).
    Returns (p_default (n,), p_grades (n, len(RATINGS)) in RATINGS order).
    """
    row = TRANSITIONS[grade]
    row = row / row.sum(axis=1, keepdims=True) * (1 - base_pd)[:, None]
    # Cumulative probability from the worst outcome up: default, CCC, B, ..., AAA.
    cum = np.cumsum(np.column_stack([base_pd, row[:, ::-1]]), axis=1)
    thresholds = norm.ppf(np.clip(cum, 0, 1))
    cond = norm.cdf((thresholds - np.sqrt(rho)[:, None] * z) / np.sqrt(1 - rho)[:, None])
    cond[:, -1] = 1.0
    probs = np.diff(cond, axis=1, prepend=0.0)
    return probs[:, 0], probs[:, 1:][:, ::-1]


def basel_capital_ratio(pd_, lgd, maturity):
    """Basel corporate IRB capital requirement K, as a fraction of EAD."""
    rho = asset_correlation(pd_)
    ma = maturity_adjustment(pd_, maturity)
    wcdr = norm.cdf((norm.ppf(pd_) + np.sqrt(rho) * norm.ppf(CONFIDENCE)) / np.sqrt(1 - rho))
    k = lgd * (wcdr - pd_) * ma
    return np.clip(k, 0, None)


def load_portfolio(path=INPUT_FILE):
    df = pd.read_excel(path, sheet_name="Loans")
    df["ead_inr_cr"] = df["drawn_amount_inr_cr"] + df["ccf"] * df["undrawn_amount_inr_cr"]
    return df


def loan_metrics(df, z, lgd_uplift):
    """Per-loan stressed PD/LGD, capital ratio K and expected loss for one scenario."""
    base_pd = df["base_pd"].to_numpy()
    rho = asset_correlation(base_pd)
    grade = df["credit_rating"].map(RATINGS.index).to_numpy()
    lgd_s = np.clip(df["base_lgd"].to_numpy() * lgd_uplift, 0, 0.95)
    maturity = df["maturity_years"].to_numpy()

    pd_s = stressed_pd(base_pd, rho, z)
    _, p_grades = migration_probs(base_pd, grade, rho, z)
    # Long-run PD of each grade the loan can land in, keeping its idiosyncratic jitter.
    grade_pd = GRADE_PD[None, :] * (base_pd / GRADE_PD[grade])[:, None]
    k_grades = basel_capital_ratio(grade_pd, lgd_s[:, None], maturity[:, None])
    # The share that defaults leaves the performing book: its loss reaches capital through EL.
    k = (p_grades * k_grades).sum(axis=1)

    return {"pd": pd_s, "lgd": lgd_s, "k": k, "el": pd_s * lgd_s * df["ead_inr_cr"].to_numpy()}


def run_scenario(df, name, z, lgd_uplift):
    m = loan_metrics(df, z, lgd_uplift)
    pd_s, lgd_s = m["pd"], m["lgd"]
    ead = df["ead_inr_cr"].to_numpy()

    el = m["el"]                             # expected loss, INR cr
    required_capital = m["k"] * ead          # Pillar 1 capital requirement, INR cr
    rwa = required_capital * 12.5            # risk-weighted assets, INR cr

    return pd.Series({
        "scenario": name,
        "avg_stressed_pd": np.average(pd_s, weights=ead),
        "avg_stressed_lgd": np.average(lgd_s, weights=ead),
        "total_ead_inr_cr": ead.sum(),
        "expected_loss_inr_cr": el.sum(),
        "required_capital_inr_cr": required_capital.sum(),
        "rwa_inr_cr": rwa.sum(),
    })


def capital_adequacy(results):
    baseline = results.loc[results["scenario"] == "Baseline"].iloc[0]
    available_capital = CAPITAL_RATIO_START * baseline["rwa_inr_cr"]

    rows = []
    for _, r in results.iterrows():
        incremental_loss = r["expected_loss_inr_cr"] - baseline["expected_loss_inr_cr"]
        capital_after_stress = available_capital - max(incremental_loss, 0.0)
        post_stress_ratio = capital_after_stress / r["rwa_inr_cr"]
        min_required_capital = CAPITAL_RATIO_MIN * r["rwa_inr_cr"]
        shortfall = max(0.0, min_required_capital - capital_after_stress)
        rows.append({
            "scenario": r["scenario"],
            "capital_after_stress_inr_cr": capital_after_stress,
            "post_stress_capital_ratio": post_stress_ratio,
            "capital_shortfall_inr_cr": shortfall,
        })
    return pd.DataFrame(rows)


def main():
    df = load_portfolio()
    print(f"Loaded {len(df)} loans, total EAD INR {df['ead_inr_cr'].sum():,.1f} cr\n")

    results = pd.DataFrame([
        run_scenario(df, name, cfg["Z"], cfg["lgd_uplift"]) for name, cfg in SCENARIOS.items()
    ])
    adequacy = capital_adequacy(results)
    summary = results.merge(adequacy, on="scenario")
    summary["available_capital_inr_cr"] = CAPITAL_RATIO_START * results.loc[results["scenario"] == "Baseline", "rwa_inr_cr"].iloc[0]

    pd.options.display.float_format = "{:,.2f}".format
    print(summary.set_index("scenario")[[
        "avg_stressed_pd", "avg_stressed_lgd", "expected_loss_inr_cr",
        "required_capital_inr_cr", "rwa_inr_cr", "post_stress_capital_ratio",
        "capital_shortfall_inr_cr",
    ]])

    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Scenario_Summary", index=False)
        df.to_excel(writer, sheet_name="Loan_Level_Inputs", index=False)
    print(f"\nSaved detailed results -> {OUTPUT_FILE}")

    make_chart(summary)
    print(f"Saved chart -> {CHART_FILE}")


def make_chart(summary):
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    ax = axes[0]
    ax.bar(summary["scenario"], summary["post_stress_capital_ratio"] * 100, color="#2f6f5e")
    ax.axhline(CAPITAL_RATIO_MIN * 100, color="#b23a2f", linestyle="--", label=f"Min. required ({CAPITAL_RATIO_MIN:.1%})")
    ax.set_ylabel("Post-stress CET1 ratio (%)")
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
