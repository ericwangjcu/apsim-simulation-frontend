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


def parse_end_hour(search_text: str) -> int | None:
    """Return forecast accumulation end-hour from an APCP inventory string."""
    # Examples anticipated: 0-3 hour acc fcst, 3-6 hour acc fcst,
    # or 6 hour acc fcst. In all cases the final hour before 'hour' is used.
    m = re.search(r"(?:(\d+)-)?(\d+) hour acc fcst", search_text)
    if m:
        return int(m.group(2))
    return None


def parse_start_hour(search_text: str, end_hour: int) -> int:
    m = re.search(r"(?:(\d+)-)?(\d+) hour acc fcst", search_text)
    if not m:
        return max(0, end_hour - 3)
    if m.group(1) is not None:
        return int(m.group(1))
    # If the inventory only states e.g. '3 hour acc fcst', interpret it as
    # a 0-to-end accumulation. We retain this information explicitly below.
    return 0


def nearest_point(ds, lat: float, lon: float):
    lon_name = "longitude" if "longitude" in ds.coords else "lon"
    lat_name = "latitude" if "latitude" in ds.coords else "lat"
    grid_lon = lon if lon >= 0 else lon % 360
    return ds.sel({lat_name: lat, lon_name: grid_lon}, method="nearest")


all_rows: list[dict] = []
inventory_rows: list[dict] = []

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

    inv = H.inventory()
    if inv is None or len(inv) == 0:
        raise RuntimeError(f"No GEFS inventory returned for {label}")

    inv = inv.copy()
    inv["search_this"] = inv["search_this"].astype(str)
    inv["end_hour"] = inv["search_this"].map(parse_end_hour)
    inv_sel = inv[inv["end_hour"].notna() & (inv["end_hour"] <= MAX_HOUR)].copy()

    print("First APCP inventory rows:")
    for s in inv["search_this"].head(12):
        print(" ", s)

    if len(inv_sel) == 0:
        raise RuntimeError("Could not identify APCP accumulation messages <=72 h")

    # Record inventory/audit trail.
    for _, r in inv_sel.iterrows():
        end_h = int(r["end_hour"])
        inventory_rows.append(
            {
                "member": label,
                "search_this": r["search_this"],
                "start_hour": parse_start_hour(r["search_this"], end_h),
                "end_hour": end_h,
            }
        )

    # Herbie accepts a regex. Join the exact inventory strings so we download
    # only the messages needed for the first 72 forecast hours.
    pattern = "|".join(re.escape(s) for s in inv_sel["search_this"].tolist())
    ds = H.xarray(pattern, remove_grib=False)
    if isinstance(ds, list):
        if len(ds) != 1:
            raise RuntimeError(f"Expected one APCP dataset, got {len(ds)}")
        ds = ds[0]

    data_vars = [v for v in ds.data_vars if "gribfile_projection" not in v.lower()]
    if not data_vars:
        raise RuntimeError(f"No rainfall data variable found. Dataset: {ds}")
    var = data_vars[0]
    pt = nearest_point(ds, LAT, LON)

    # Support scalar or step-dimensional datasets.
    if "step" in pt.dims or "step" in pt.coords:
        steps = pd.to_timedelta(np.atleast_1d(pt["step"].values))
        values = np.atleast_1d(pt[var].values).astype(float).reshape(-1)
        valid = np.atleast_1d(pt["valid_time"].values) if "valid_time" in pt.coords else [pd.Timestamp(ISSUE_DATE) + s for s in steps]
        for step, value, vt in zip(steps, values, valid):
            all_rows.append(
                {
                    "issue_date": ISSUE_DATE,
                    "member": label,
                    "lead_hour": int(step / pd.Timedelta(hours=1)),
                    "valid_time_utc": pd.Timestamp(vt).isoformat(),
                    "apcp_mm": float(value),
                    "grid_lat": float(np.asarray(pt["latitude"].values).reshape(-1)[0]),
                    "grid_lon": float(np.asarray(pt["longitude"].values).reshape(-1)[0]),
                }
            )
    else:
        step = pd.to_timedelta(pt["step"].item()) if "step" in pt.coords else pd.Timedelta(hours=MAX_HOUR)
        all_rows.append(
            {
                "issue_date": ISSUE_DATE,
                "member": label,
                "lead_hour": int(step / pd.Timedelta(hours=1)),
                "valid_time_utc": pd.Timestamp(pt["valid_time"].item()).isoformat() if "valid_time" in pt.coords else "",
                "apcp_mm": float(pt[var].item()),
                "grid_lat": float(pt["latitude"].item()),
                "grid_lon": float(pt["longitude"].item()),
            }
        )

raw = pd.DataFrame(all_rows).sort_values(["member", "lead_hour"])
raw.to_csv(OUT / "gefs_apcp_ingham_raw.csv", index=False)

inventory = pd.DataFrame(inventory_rows).sort_values(["member", "end_hour"])
inventory.to_csv(OUT / "gefs_apcp_inventory.csv", index=False)

# Merge extracted values with accumulation-window metadata by member/end hour.
merged = raw.merge(
    inventory.rename(columns={"end_hour": "lead_hour"}),
    on=["member", "lead_hour"],
    how="left",
)

# Build a conservative 72-h total. If inventory contains explicit non-overlap
# ranges (e.g. 0-3, 3-6, ...), sum them. If values are cumulative from hour 0,
# use the largest lead value instead of summing cumulative fields.
summary = []
for member, g in merged.groupby("member"):
    g = g.sort_values("lead_hour")
    explicit_ranges = g["start_hour"].notna() & (g["start_hour"] > 0)
    if explicit_ranges.any():
        total = float(g["apcp_mm"].sum())
        method = "sum_of_accumulation_windows"
    else:
        total = float(g.iloc[-1]["apcp_mm"])
        method = "final_cumulative_accumulation"
    summary.append({"member": member, "forecast_72h_rain_mm": total, "method": method})

summary_df = pd.DataFrame(summary)
summary_df.to_csv(OUT / "gefs_72h_summary.csv", index=False)

meta = {
    "issue_date_utc": ISSUE_DATE + "T00:00:00",
    "requested_location": {"lat": LAT, "lon": LON, "name": "Ingham"},
    "forecast_horizon_hours": MAX_HOUR,
    "ensemble_members": ["c00", "p01", "p02", "p03", "p04"],
    "source": "NOAA GEFSv12 reforecast, noaa-gefs-retrospective AWS open-data bucket",
    "variable": "APCP surface accumulated precipitation",
}
(OUT / "phase2_metadata.json").write_text(json.dumps(meta, indent=2))

print("\n=== Phase 2 extraction complete ===")
print(summary_df.to_string(index=False))
print("\nRaw rows:", len(raw))
print("Output directory:", OUT)
