from __future__ import annotations

from pathlib import Path

import pandas as pd

SCENARIO_DIR = Path("phase2/scenarios")
OUT = Path("phase2/output")
OUT.mkdir(parents=True, exist_ok=True)

FILES = {
    "observed": SCENARIO_DIR / "Sugarcane_observed.Report.csv",
    "c00": SCENARIO_DIR / "Sugarcane_GEFS_c00.Report.csv",
    "p01": SCENARIO_DIR / "Sugarcane_GEFS_p01.Report.csv",
    "p02": SCENARIO_DIR / "Sugarcane_GEFS_p02.Report.csv",
    "p03": SCENARIO_DIR / "Sugarcane_GEFS_p03.Report.csv",
    "p04": SCENARIO_DIR / "Sugarcane_GEFS_p04.Report.csv",
}

METRICS = [
    "Sugarcane.biomass",
    "Sugarcane.cane_wt",
    "Sugarcane.sucrose_wt",
    "Sugarcane.lai",
    "Sugarcane.root_depth",
    "Sugarcane.swdef_photo",
    "Sugarcane.swdef_pheno",
    "Sugarcane.swdef_expan",
    "Sugarcane.swdef_stalk",
    "Sugarcane.ep",
]

frames = []
for scenario, path in FILES.items():
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    df["Clock.Today"] = pd.to_datetime(df["Clock.Today"])
    keep = ["Clock.Today", "Sugarcane.crop_status"] + [m for m in METRICS if m in df.columns]
    df = df[keep].copy()
    df.insert(0, "scenario", scenario)
    frames.append(df)

combined = pd.concat(frames, ignore_index=True)
window = combined[
    (combined["Clock.Today"] >= "2000-02-01") &
    (combined["Clock.Today"] <= "2000-04-01")
].copy()
window.to_csv(OUT / "apsim_phase2_daily_comparison.csv", index=False)

baseline = combined[combined["scenario"] == "observed"].set_index("Clock.Today")
compare_dates = pd.to_datetime(["2000-02-08", "2000-02-15", "2000-03-01", "2000-04-01"])
rows = []
for scenario in ["c00", "p01", "p02", "p03", "p04"]:
    s = combined[combined["scenario"] == scenario].set_index("Clock.Today")
    for d in compare_dates:
        row = {"scenario": scenario, "date": d.date().isoformat()}
        for metric in ["Sugarcane.biomass", "Sugarcane.cane_wt", "Sugarcane.sucrose_wt", "Sugarcane.lai", "Sugarcane.ep"]:
            if metric in s.columns:
                value = float(s.loc[d, metric])
                obs = float(baseline.loc[d, metric])
                short = metric.split(".", 1)[1]
                row[short] = value
                row[f"delta_{short}_vs_observed"] = value - obs
        rows.append(row)

summary = pd.DataFrame(rows)
summary.to_csv(OUT / "apsim_phase2_summary.csv", index=False)

# Ensemble spread for biomass/cane/sucrose on comparison dates.
ensemble = combined[combined["scenario"].isin(["c00", "p01", "p02", "p03", "p04"])].copy()
ensemble_date = ensemble[ensemble["Clock.Today"].isin(compare_dates)]
spread_rows = []
for d, g in ensemble_date.groupby("Clock.Today"):
    r = {"date": d.date().isoformat()}
    for metric in ["Sugarcane.biomass", "Sugarcane.cane_wt", "Sugarcane.sucrose_wt"]:
        vals = g[metric].astype(float)
        short = metric.split(".", 1)[1]
        r[f"{short}_ensemble_mean"] = vals.mean()
        r[f"{short}_ensemble_min"] = vals.min()
        r[f"{short}_ensemble_max"] = vals.max()
        r[f"{short}_observed"] = float(baseline.loc[d, metric])
    spread_rows.append(r)

pd.DataFrame(spread_rows).to_csv(OUT / "apsim_phase2_ensemble_spread.csv", index=False)

print("=== APSIM Phase 2 scenario comparison ===")
print(summary.to_string(index=False))
