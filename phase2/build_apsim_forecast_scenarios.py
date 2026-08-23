from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

FORECAST_6H = Path("phase2/output/gefs_apcp_ingham_6hour.csv")
BASE_MET = Path("phase2/scenarios/AU_Ingham_observed.met")
BASE_APSIMX = Path("phase2/scenarios/Sugarcane_observed.apsimx")
SCENARIO_DIR = Path("phase2/scenarios")
ISSUE_DATE = date(2000, 2, 5)
# The GEFS issue is 00 UTC (~10:00 AEST). For this proof-of-concept we map
# forecast hours 0-24, 24-48 and 48-72 to the following SILO/APSIM daily rows.
TARGET_DATES = [ISSUE_DATE + timedelta(days=i) for i in (1, 2, 3)]

SCENARIO_DIR.mkdir(parents=True, exist_ok=True)


def replace_met_rain(source_text: str, replacements: dict[tuple[int, int], float]) -> str:
    lines = source_text.splitlines()
    out = []
    for line in lines:
        stripped = line.strip()
        parts = stripped.split()
        if len(parts) >= 9 and parts[0].isdigit() and parts[1].isdigit():
            year = int(parts[0])
            doy = int(parts[1])
            key = (year, doy)
            if key in replacements:
                # APSIM .met data fields:
                # year day radn maxt mint rain pan vp code
                parts[5] = f"{replacements[key]:.3f}"
                line = " ".join(parts)
        out.append(line)
    return "\n".join(out) + "\n"


def set_weather_path(model: dict, weather_path: str) -> None:
    if isinstance(model, dict):
        if str(model.get("$type", "")).startswith("Models.Climate.Weather"):
            model["FileName"] = weather_path
        for value in model.values():
            set_weather_path(value, weather_path)
    elif isinstance(model, list):
        for value in model:
            set_weather_path(value, weather_path)


def extract_observed_rain(met_text: str, dates: list[date]) -> dict[str, float]:
    wanted = {(d.year, d.timetuple().tm_yday): d.isoformat() for d in dates}
    result = {}
    for line in met_text.splitlines():
        p = line.strip().split()
        if len(p) >= 9 and p[0].isdigit() and p[1].isdigit():
            key = (int(p[0]), int(p[1]))
            if key in wanted:
                result[wanted[key]] = float(p[5])
    return result


forecast = pd.read_csv(FORECAST_6H)
forecast["day_index"] = ((forecast["lead_hour"] - 1) // 24) + 1
daily = (
    forecast.groupby(["member", "day_index"], as_index=False)["apcp_mm"]
    .sum()
    .rename(columns={"apcp_mm": "rain_mm"})
)

met_text = BASE_MET.read_text(encoding="utf-8-sig")
base_model = json.loads(BASE_APSIMX.read_text())
observed_daily = extract_observed_rain(met_text, TARGET_DATES)

manifest = {
    "issue_date_utc": ISSUE_DATE.isoformat() + "T00:00:00",
    "alignment_note": (
        "Proof-of-concept rainfall-only substitution: GEFS 0-24 h is mapped to "
        "APSIM/SILO day 2000-02-06, 24-48 h to 2000-02-07, and 48-72 h to "
        "2000-02-08. Non-rain weather variables remain observed SILO values."
    ),
    "target_dates": [d.isoformat() for d in TARGET_DATES],
    "observed_rain_mm": observed_daily,
    "scenarios": {},
}

# Keep a clean observed baseline under an explicit name/path.
obs_model = json.loads(json.dumps(base_model))
set_weather_path(obs_model, "/test-run/AU_Ingham_observed.met")
BASE_APSIMX.write_text(json.dumps(obs_model, indent=2))

for member in sorted(daily["member"].unique()):
    g = daily[daily["member"] == member].set_index("day_index")
    replacements = {}
    daily_values = {}
    for i, target_date in enumerate(TARGET_DATES, start=1):
        rain = float(g.loc[i, "rain_mm"])
        replacements[(target_date.year, target_date.timetuple().tm_yday)] = rain
        daily_values[target_date.isoformat()] = rain

    met_name = f"AU_Ingham_GEFS_{member}.met"
    model_name = f"Sugarcane_GEFS_{member}.apsimx"
    (SCENARIO_DIR / met_name).write_text(replace_met_rain(met_text, replacements))

    model = json.loads(json.dumps(base_model))
    set_weather_path(model, f"/test-run/{met_name}")
    (SCENARIO_DIR / model_name).write_text(json.dumps(model, indent=2))

    manifest["scenarios"][member] = {
        "weather_file": met_name,
        "apsimx_file": model_name,
        "forecast_daily_rain_mm": daily_values,
        "forecast_72h_rain_mm": sum(daily_values.values()),
    }

Path("phase2/output/apsim_scenario_manifest.json").write_text(json.dumps(manifest, indent=2))

print("=== APSIM forecast scenarios built ===")
print("Observed rain for mapped 72 h:", observed_daily)
for member, info in manifest["scenarios"].items():
    print(member, info["forecast_daily_rain_mm"], "72h=", round(info["forecast_72h_rain_mm"], 3))
