# Basel III Credit Risk & Stress Testing

750 synthetic corporate loans, ₹5,300 cr of exposure, put through two recessions to see how much capital the bank would be short. It's the core calculation of an ICAAP capital plan without the multi-year balance sheet projection around it, and it runs in a few seconds.

| Scenario | Avg. PD | Avg. LGD | RWA (₹ cr) | Capital ratio after stress | Shortfall vs 10.5% (₹ cr) |
|---|---|---|---|---|---|
| Baseline | 1.50% | 32.4% | 3,259.8 | 13.00% | 0 |
| Adverse | 4.78% | 37.3% | 3,812.0 | 9.31% | 45.3 |
| Severely Adverse | 9.63% | 42.2% | 4,313.8 | 5.43% | 218.6 |

The bank starts with ₹423.8 cr of capital (13% of baseline RWA). Adverse eats into the conservation buffer and leaves it ₹45 cr short. Severely Adverse is much worse: 5.43% is below the 8% hard minimum, never mind the buffer.

PD and LGD averages are weighted by EAD. All of this comes from one seeded run, so you'll get exactly these numbers too.

![Capital ratio and shortfall by scenario](capital_shortfall.png)

## The loan book

`generate_portfolio.py` builds it, as of 31 March 2026. Ratings lean towards the middle (30% BBB, 25% BB, 5% CCC) and each loan's PD comes from a one-year default table, AAA at 0.02% up to CCC at 15%, with ±15% noise so two BB loans aren't identical. Sizes are lognormal, so a small number of loans dominate: the 30 biggest hold 24% of EAD, and the largest is ₹98 cr. (There's a ₹250 cr cap in the code. Nothing gets near it.)

65% are term loans, fully drawn, lasting 3 to 10 years. The rest are one-year revolvers drawn 10% to 95%, with a 75% credit conversion factor on the undrawn part. EAD is drawn plus CCF times undrawn.

LGD starts from seniority (35% senior secured, 45% senior unsecured, 65% subordinated) and falls with collateral coverage. Collateral doesn't count at face value. Cash counts at 90% and real estate at 60%, while receivables and plant & machinery sit at 50% and 40%, because nobody gets book value for a used production line in a downturn. LGD is kept between 10% and 95%.

## Stressing it

A recession enters through one number, Z, the state of the economy, drawn from a standard normal. It moves every borrower's PD together, via the Vasicek single-factor model that the Basel IRB formula is built on:

```
PD(Z) = Φ[ (Φ⁻¹(PD) − √ρ · Z) / √(1−ρ) ]
```

ρ is the Basel corporate asset correlation. It runs from 24% for the safest names to 12% for the riskiest, on the logic that a CCC borrower's problems are mostly its own.

| Scenario | Z | LGD multiplier |
|---|---|---|
| Baseline | 0.0 | 1.00x |
| Adverse | −1.5 | 1.15x |
| Severely Adverse | −2.5 | 1.30x |

Z = −2.5 is roughly a 1-in-160-year economy. The LGD multipliers are my assumption about how far recoveries fall; they aren't calibrated to anything.

## Capital

Each loan gets the Basel corporate IRB charge (BCBS, *An Explanatory Note on the Basel II IRB Risk Weight Functions*):

```
K   = LGD · [ Φ( (Φ⁻¹(PD) + √ρ · Φ⁻¹(0.999)) / √(1−ρ) ) − PD ] · MA(PD, M)
RWA = 12.5 · K · EAD
```

PD is floored at 0.05%, the Basel III corporate minimum, which catches 42 AAA and AA loans. M is time left to maturity at the as-of date, held between 1 and 5 years.

The obvious way to stress this is to put the recession PD into K. I did that in the first version and it was wrong. The IRB formula already takes a long-run PD out to a 1-in-1000-year economy, so handing it a PD that's already been through a recession stresses the same factor twice. RWA came out at 3.1 times baseline and the severe ratio at 2.10%. Most of that was the double counting.

The stressed PD now drives expected loss and nothing else. RWA moves through rating migration instead, CreditMetrics-style: each grade's row of a one-year transition matrix becomes a set of asset-return cut-offs, and the same Z shifts every borrower across them. By construction the chance of landing in default is exactly PD(Z). K is the probability-weighted charge over the grades a loan can end up in, at each grade's long-run PD. Loans that default drop out of RWA, because their loss has already come out of capital as expected loss.

| Scenario | EAD downgraded | EAD defaulted | RWA vs baseline |
|---|---|---|---|
| Baseline | 4.6% | 1.5% | 1.00x |
| Adverse | 15.3% | 4.8% | 1.17x |
| Severely Adverse | 26.5% | 9.6% | 1.32x |

Baseline isn't frozen, by the way. Z = 0 is a median year, milder than the long-run average (average PD 1.50% against 2.0% long-run, both EAD-weighted), so 4.6% of EAD still gets downgraded and 3.5% upgraded.

## Capital adequacy

Ratios here are total capital over RWA. The bank starts at 13% of baseline RWA, which is an assumption and not a real bank's number. Baseline expected loss is treated as already provisioned, so only the extra loss in a scenario comes out of capital.

The 10.5% line is the 8% Pillar 1 total capital minimum plus the 2.5% conservation buffer. Strictly, falling into the buffer limits dividends and bonuses rather than breaking a hard rule, so the Adverse "shortfall" is a buffer breach. For Indian banks the RBI line would be higher, 11.5% (9% plus the buffer). The CET1-only version of the minimum is 7%.

How much of the drop is losses and how much is RWA? If RWA stayed at baseline, the ratios would be 10.89% and 7.19%. So losses account for 57% of the fall in Adverse and 77% in Severely Adverse. Downgrades do the rest.

## What's simplified

The loans are synthetic and the transition matrix is invented to look like S&P's long-run averages. It isn't taken from their published tables, and a real bank would estimate its own from rating history.

Everything happens in one year on a fixed balance sheet. No new lending, no interest income, no management actions. CCF isn't stressed either, though in a real downturn borrowers draw down their revolvers right before they default.

One factor drives all ten sectors. That's the biggest gap, I think: a real book that's heavy in real estate gets hit harder than this model can show. A sector factor model, or a t copula for fatter tails, would be the next step.

## Power BI dashboard

`powerbi/` has an interactive version with every loan in every scenario, so you can slice capital and losses by sector, rating, seniority or collateral. Open `powerbi/BaselStressTest.pbip` in Power BI Desktop and hit Refresh. Details are in [`powerbi/README.md`](powerbi/README.md).

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 generate_portfolio.py           # -> loan_portfolio.xlsx
python3 basel_stress_test.py            # -> stress_test_results.xlsx, capital_shortfall.png
python3 powerbi/export_powerbi_data.py  # -> powerbi/data/*.csv, only needed for the dashboard
```

`stress_test_results.xlsx` has two sheets: the scenario summary, and one row per loan per scenario with stressed PD, LGD, K, RWA and expected loss. Neither xlsx is committed. Both are seeded, so they rebuild identically.

## Files

```
generate_portfolio.py   builds the loan book
basel_stress_test.py    PD/LGD/EAD, migration, IRB capital, scenarios, shortfall, chart
capital_shortfall.png   the chart above
powerbi/                Power BI project, its CSVs and the script that writes them
```
