"""Generate a synthetic corporate loan portfolio as the input for the stress test.

Produces loan_portfolio.xlsx: one row per loan, with the exposure, rating, and
collateral fields the Basel IRB capital formula needs (PD comes from the rating
via RATING_PD, LGD and EAD are derived per-loan below). Everything is random
but seeded, so re-running this script reproduces the same portfolio.
"""
import numpy as np
import pandas as pd

SEED = 42
N_LOANS = 750
AS_OF_DATE = pd.Timestamp("2026-03-31")

rng = np.random.default_rng(SEED)

# Illustrative 1-year average default rates by rating grade (S&P/Moody's-style).
RATING_PD = {
    "AAA": 0.0002, "AA": 0.0005, "A": 0.0010,
    "BBB": 0.0030, "BB": 0.0150, "B": 0.0500, "CCC": 0.1500,
}
RATINGS = list(RATING_PD.keys())
RATING_WEIGHTS = [0.03, 0.07, 0.15, 0.30, 0.25, 0.15, 0.05]  # skewed to mid-grade

SECTORS = [
    "Manufacturing", "IT Services", "Real Estate", "Infrastructure",
    "Retail & Consumer", "Agriculture", "Healthcare", "Energy & Power",
    "Financial Services", "Textiles",
]

LOAN_TYPES = ["Term Loan", "Working Capital / Revolving"]
LOAN_TYPE_WEIGHTS = [0.65, 0.35]

SENIORITY = ["Senior Secured", "Senior Unsecured", "Subordinated"]
SENIORITY_WEIGHTS = [0.55, 0.35, 0.10]
# Base LGD floor by seniority before any collateral offset.
SENIORITY_LGD_FLOOR = {"Senior Secured": 0.35, "Senior Unsecured": 0.45, "Subordinated": 0.65}

COLLATERAL_TYPES = ["Unsecured", "Receivables", "Plant & Machinery", "Real Estate", "Cash / Marketable Securities"]
# How much of the collateral's coverage ratio actually offsets LGD (liquidity/volatility of the asset).
COLLATERAL_EFFECTIVENESS = {
    "Unsecured": 0.0, "Receivables": 0.50, "Plant & Machinery": 0.40,
    "Real Estate": 0.60, "Cash / Marketable Securities": 0.90,
}


def sample_ratings(n):
    return rng.choice(RATINGS, size=n, p=RATING_WEIGHTS)


def sample_collateral(n, seniority):
    # Secured tranches get a real collateral type; unsecured tranches get none.
    ctype = np.where(
        seniority == "Senior Secured",
        rng.choice(["Receivables", "Plant & Machinery", "Real Estate", "Cash / Marketable Securities"], size=n),
        np.where(rng.random(n) < 0.20, rng.choice(["Receivables", "Plant & Machinery"], size=n), "Unsecured"),
    )
    coverage = np.where(
        ctype == "Unsecured", 0.0,
        np.clip(rng.normal(0.75, 0.20, size=n), 0.10, 1.30),
    )
    return ctype, coverage


def build_portfolio():
    n = N_LOANS
    ratings = sample_ratings(n)
    base_pd = np.array([RATING_PD[r] for r in ratings]) * rng.uniform(0.85, 1.15, size=n)  # idiosyncratic jitter

    sectors = rng.choice(SECTORS, size=n)
    loan_types = rng.choice(LOAN_TYPES, size=n, p=LOAN_TYPE_WEIGHTS)
    seniority = rng.choice(SENIORITY, size=n, p=SENIORITY_WEIGHTS)

    # Loan sizes: heavy-tailed, in INR crore (1 crore = 10 million INR).
    committed_cr = np.round(rng.lognormal(mean=1.5, sigma=1.0, size=n), 2)
    committed_cr = np.clip(committed_cr, 0.25, 250.0)

    # Term loans are fully drawn; revolving facilities carry an undrawn portion.
    utilization = np.where(loan_types == "Term Loan", 1.0, np.clip(rng.normal(0.55, 0.15, size=n), 0.10, 0.95))
    drawn_cr = np.round(committed_cr * utilization, 2)
    undrawn_cr = np.round(committed_cr - drawn_cr, 2)
    ccf = np.where(loan_types == "Term Loan", 0.0, 0.75)  # standard commitment CCF

    maturity_years = np.where(
        loan_types == "Term Loan",
        np.round(rng.uniform(3, 10, size=n), 1),
        1.0,  # revolving facilities renewed annually
    )
    origination_offset_days = rng.integers(30, 1500, size=n)
    origination_date = AS_OF_DATE - pd.to_timedelta(origination_offset_days, unit="D")
    maturity_date = origination_date + pd.to_timedelta((maturity_years * 365.25).astype(int), unit="D")

    collateral_type, collateral_coverage = sample_collateral(n, seniority)
    effectiveness = np.array([COLLATERAL_EFFECTIVENESS[c] for c in collateral_type])
    lgd_floor = np.array([SENIORITY_LGD_FLOOR[s] for s in seniority])
    base_lgd = np.clip(
        lgd_floor * (1 - np.minimum(collateral_coverage, 1.0) * effectiveness),
        0.10, 0.95,
    )

    interest_rate = np.round(0.06 + base_pd * 8 + rng.normal(0, 0.005, size=n), 4)  # crude risk-based pricing

    df = pd.DataFrame({
        "loan_id": [f"LN{i+1:05d}" for i in range(n)],
        "borrower_id": [f"BRW{i+1:05d}" for i in range(n)],
        "sector": sectors,
        "loan_type": loan_types,
        "credit_rating": ratings,
        "base_pd": base_pd,
        "origination_date": origination_date,
        "maturity_date": maturity_date,
        "maturity_years": maturity_years,
        "committed_amount_inr_cr": committed_cr,
        "drawn_amount_inr_cr": drawn_cr,
        "undrawn_amount_inr_cr": undrawn_cr,
        "ccf": ccf,
        "seniority": seniority,
        "collateral_type": collateral_type,
        "collateral_coverage_ratio": np.round(collateral_coverage, 3),
        "base_lgd": np.round(base_lgd, 4),
        "interest_rate": interest_rate,
    })
    return df


if __name__ == "__main__":
    portfolio = build_portfolio()

    with pd.ExcelWriter("loan_portfolio.xlsx", engine="openpyxl") as writer:
        portfolio.to_excel(writer, sheet_name="Loans", index=False)
        pd.DataFrame(
            {"rating": RATINGS, "avg_1y_pd": [RATING_PD[r] for r in RATINGS]}
        ).to_excel(writer, sheet_name="Rating_PD_Reference", index=False)

    total_committed = portfolio["committed_amount_inr_cr"].sum()
    total_drawn = portfolio["drawn_amount_inr_cr"].sum()
    print(f"Generated {len(portfolio)} loans -> loan_portfolio.xlsx")
    print(f"Total committed exposure: INR {total_committed:,.1f} cr")
    print(f"Total drawn exposure:     INR {total_drawn:,.1f} cr")
    print(portfolio["credit_rating"].value_counts().reindex(RATINGS))
