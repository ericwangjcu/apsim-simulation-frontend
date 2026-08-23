from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

ISSUE_DATE = "2000-02-05"
LAT = -18.65
LON = 146.18
MAX_HOUR = 72
MEMBERS = [0, 1, 2, 3, 4]
OUT = Path("phase2/output")
OUT.mkdir(parents=True, exist_ok=True)


def parse_window(search_text: str):
    """Parse APCP accumulation window from an inventory string."""
    m = re.search(r"(?:(\d+)-)?(\d+) hour acc fcst", search_text)
    if not m:
        return None, None
    end = int(m.group(2))
    start = int(m.group(1)) if m.group(1) is not None else 0
    return start, end


def nearest_point(ds, lat: float, lon: float):
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    grid_lon = lon if lon >= 0 else lon % 360
    return ds.sel({lat_name: lat, lon_name: grid_lon}, method="nearest")


all_rows = []
inventory_rows = []

for member in MEMBERS:
    label = "c00" if member == 0 else f"p{member:02d}"
    print(f"\n=== GEFS member {label} ===")

    H = Herbie(
        ISSUE_DATE,
        model="gefs_reforecast",
        fxx=72,
        member=member,
        variable_level="apcp_sfc",
        save_dir="phase2/herbie_cache",
    )

    inv = H.inventory().copy()
    inv["search_this"] = inv["search_this"].astype(str)
    inv[["start_hour", "end_hour"]] = inv["search_this"].apply(
        lambda s: pd.Series(parse_window(s))
    )
    inv["duration_hour"] = inv["end_hour"] - inv["start_hour"]

    print("First APCP inventory rows:")
    for s in inv["search_this"].head(12):
        print(" ", s)

    # GEFS APCP contains overlapping 3-h and 6-h accumulations, e.g.
    # 0-3 and 0-6, then 6-9 and 6-12. Use only the 6-h windows so the
    # resulting values are non-overlapping and can be summed directly.
    inv_sel = inv[
        inv["end_hour"].notna()
        & (inv["end_hour"] <= MAX_HOUR)
        & (inv["duration_hour"] == 6)
    ].copy()

    if len(inv_sel) != MAX_HOUR // 6:
        raise RuntimeError(
            f"Expected {MAX_HOUR // 6} six-hour APCP windows for {label}, got {len(inv_sel)}"
        )

    for _, r in inv_sel.iterrows():
        inventory_rows.append(
            {
                "member": label,
                "search_this": r["search_this"],
                "start_hour": int(r["start_hour"]),
                "end_hour": int(r["end_hour"]),
                "duration_hour": int(r["duration_hour"]),
            }
        )

    pattern = "|".join(re.escape(s) for s in inv_sel["search_this"].tolist())
    opened = H.xarray(pattern, remove_grib=False)
    datasets = opened if isinstance(opened, list) else [opened]
    print(f"cfgrib datasets returned: {len(datasets)}")

    for ds in datasets:
        data_vars = [v for v in ds.data_vars if "gribfile_projection" not in v.lower()]
        if not data_vars:
            continue
        var = data_vars[0]
        pt = nearest_point(ds, LAT, LON)

        steps = pd.to_timedelta(np.atleast_1d(pt["step"].values))
        values = np.atleast_1d(pt[var].values).astype(float).reshape(-1)
        valid = (
            np.atleast_1d(pt["valid_time"].values)
            if "valid_time" in pt.coords
            else [pd.Timestamp(ISSUE_DATE) + s for s in steps]
        )

        grid_lat = float(np.asarray(pt["latitude"].values).reshape(-1)[0])
        grid_lon = float(np.asarray(pt["longitude"].values).reshape(-1)[0])

        for step, value, vt in zip(steps, values, valid):
            lead = int(step / pd.Timedelta(hours=1))
            if lead <= MAX_HOUR:
                all_rows.append(
                    {
                        "issue_date": ISSUE_DATE,
                        "member": label,
                        "lead_hour": lead,
                        "valid_time_utc": pd.Timestamp(vt).isoformat(),
                        "apcp_mm": float(value),
                        "grid_lat": grid_lat,
                        "grid_lon": grid_lon,
                    }
                )

raw = pd.DataFrame(all_rows)
raw = raw.drop_duplicates(["member", "lead_hour"], keep="first")
raw = raw.sort_values(["member", "lead_hour"])

inventory = pd.DataFrame(inventory_rows).sort_values(["member", "end_hour"])
merged = raw.merge(
    inventory.rename(columns={"end_hour": "lead_hour"}),
    on=["member", "lead_hour"],
    how="inner",
)

expected = len(MEMBERS) * (MAX_HOUR // 6)
if len(merged) != expected:
    raise RuntimeError(f"Expected {expected} extracted six-hour values, got {len(merged)}")

merged.to_csv(OUT / "gefs_apcp_ingham_6hour.csv", index=False)
inventory.to_csv(OUT / "gefs_apcp_inventory.csv", index=False)

summary = (
    merged.groupby("member", as_index=False)["apcp_mm"]
    .sum()
    .rename(columns={"apcp_mm": "forecast_72h_rain_mm"})
)
summary["method"] = "sum_of_12_non_overlapping_6h_accumulations"
summary.to_csv(OUT / "gefs_72h_summary.csv", index=False)

meta = {
    "issue_date_utc": ISSUE_DATE + "T00:00:00",
    "requested_location": {"lat": LAT, "lon": LON, "name": "Ingham"},
    "forecast_horizon_hours": MAX_HOUR,
    "ensemble_members": ["c00", "p01", "p02", "p03", "p04"],
    "source": "NOAA GEFSv12 reforecast, noaa-gefs-retrospective AWS open-data bucket",
    "variable": "APCP surface accumulated precipitation",
    "selection": "non-overlapping six-hour accumulation windows only",
}
(OUT / "phase2_metadata.json").write_text(json.dumps(meta, indent=2))

print("\n=== Phase 2 extraction complete ===")
print(summary.to_string(index=False))
print("\nExtracted six-hour values:", len(merged))
print("Output directory:", OUT)
