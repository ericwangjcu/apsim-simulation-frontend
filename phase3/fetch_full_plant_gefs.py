from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

LAT = -18.65
LON = 146.18
MEMBERS = [0, 1, 2, 3, 4]
MAX_HOUR = 72
RAIN_THRESHOLD_MM = 20.0
START = pd.Timestamp('2000-04-01')
IRRIGATION_END = pd.Timestamp('2001-05-13')
OUT = Path('phase3/run')
CACHE = Path('phase3/herbie_cache_full_plant')
OUT.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)


def parse_window(text: str):
    m = re.search(r'(?:(\d+)-)?(\d+) hour acc fcst', text)
    if not m:
        return None, None
    return int(m.group(1) or 0), int(m.group(2))


def nearest_point(ds, lat: float, lon: float):
    lon_name = 'longitude' if 'longitude' in ds.coords else 'lon'
    lat_name = 'latitude' if 'latitude' in ds.coords else 'lat'
    grid_lon = lon if lon >= 0 else lon % 360
    return ds.sel({lat_name: lat, lon_name: grid_lon}, method='nearest')


def one_member(issue: pd.Timestamp, member: int) -> float:
    label = 'c00' if member == 0 else f'p{member:02d}'
    last_error = None
    for attempt in range(1, 4):
        try:
            H = Herbie(
                issue.strftime('%Y-%m-%d'),
                model='gefs_reforecast',
                fxx=72,
                member=member,
                variable_level='apcp_sfc',
                save_dir=str(CACHE),
            )
            inv = H.inventory().copy()
            inv['search_this'] = inv['search_this'].astype(str)
            windows = inv['search_this'].apply(parse_window)
            inv['start_hour'] = [w[0] for w in windows]
            inv['end_hour'] = [w[1] for w in windows]
            inv['duration_hour'] = inv['end_hour'] - inv['start_hour']
            selected = inv[
                inv['end_hour'].notna()
                & (inv['end_hour'] <= MAX_HOUR)
                & (inv['duration_hour'] == 6)
            ].copy()
            if len(selected) != 12:
                raise RuntimeError(f'{issue.date()} {label}: expected 12 six-hour windows, got {len(selected)}')

            pattern = '|'.join(re.escape(s) for s in selected['search_this'].tolist())
            opened = H.xarray(pattern, remove_grib=False)
            datasets = opened if isinstance(opened, list) else [opened]
            values_by_hour = {}
            for ds in datasets:
                vars_ = [v for v in ds.data_vars if 'gribfile_projection' not in v.lower()]
                if not vars_:
                    continue
                pt = nearest_point(ds, LAT, LON)
                var = vars_[0]
                steps = pd.to_timedelta(np.atleast_1d(pt['step'].values))
                vals = np.atleast_1d(pt[var].values).astype(float).reshape(-1)
                for step, value in zip(steps, vals):
                    hour = int(step / pd.Timedelta(hours=1))
                    if hour <= MAX_HOUR:
                        values_by_hour.setdefault(hour, float(value))
            needed = [6 * i for i in range(1, 13)]
            missing = [h for h in needed if h not in values_by_hour]
            if missing:
                raise RuntimeError(f'{issue.date()} {label}: missing lead hours {missing}')
            return float(sum(values_by_hour[h] for h in needed))
        except Exception as e:
            last_error = e
            if attempt < 3:
                time.sleep(attempt * 2)
    raise RuntimeError(f'GEFS failed for {issue.date()} {label}: {last_error}')


def one_date(issue: pd.Timestamp):
    vals = {}
    for member in MEMBERS:
        label = 'c00' if member == 0 else f'p{member:02d}'
        vals[label] = one_member(issue, member)
    arr = np.array(list(vals.values()), dtype=float)
    row = {
        'issue_date': issue.strftime('%Y-%m-%d'),
        **{f'{k}_rain72_mm': v for k, v in vals.items()},
        'ensemble_mean_mm': float(arr.mean()),
        'ensemble_median_mm': float(np.median(arr)),
        'ensemble_min_mm': float(arr.min()),
        'ensemble_max_mm': float(arr.max()),
        'prob_ge_20mm': float(np.mean(arr >= RAIN_THRESHOLD_MM)),
    }
    print(issue.date(), 'mean=', round(row['ensemble_mean_mm'], 1),
          'median=', round(row['ensemble_median_mm'], 1),
          'P>=20=', row['prob_ge_20mm'])
    return row


events = pd.read_csv(OUT / 'full_plant_irrigation_events.csv')
events['Clock.Today'] = pd.to_datetime(events['Clock.Today'])
candidates = set()
for date in events['Clock.Today']:
    for offset in range(0, 8):
        d = date + pd.Timedelta(days=offset)
        if START <= d <= IRRIGATION_END:
            candidates.add(d)
dates = sorted(candidates)
if not dates:
    raise RuntimeError('No baseline irrigation events, so no forecast decision dates were generated')
print(f'Forecast candidate dates: {len(dates)} ({dates[0].date()} to {dates[-1].date()})')

_ = Herbie(
    dates[0].strftime('%Y-%m-%d'), model='gefs_reforecast', fxx=72,
    member=0, variable_level='apcp_sfc', save_dir=str(CACHE)
).inventory()

rows = []
with ThreadPoolExecutor(max_workers=6) as pool:
    futures = {pool.submit(one_date, d): d for d in dates}
    for fut in as_completed(futures):
        rows.append(fut.result())

forecast = pd.DataFrame(rows).sort_values('issue_date')
forecast.to_csv(OUT / 'gefs_full_plant_decision_forecasts.csv', index=False)

meta = {
    'site': 'Ingham',
    'requested_lat': LAT,
    'requested_lon': LON,
    'crop_period': ['2000-04-01', '2001-06-24'],
    'irrigation_period_end': str(IRRIGATION_END.date()),
    'forecast_horizon_hours': 72,
    'ensemble_members': ['c00', 'p01', 'p02', 'p03', 'p04'],
    'rain_event_threshold_mm': RAIN_THRESHOLD_MM,
    'source': 'NOAA GEFSv12 reforecast via Herbie / NOAA AWS open-data archive',
    'candidate_selection': 'baseline irrigation dates plus 0-7 day buffer',
    'note': 'Proof-of-concept. 00 UTC issue date mapped to APSIM local decision date without sub-daily timezone correction.',
}
(OUT / 'gefs_full_plant_metadata.json').write_text(json.dumps(meta, indent=2))
print('\nCompleted full plant-crop forecast retrieval')
print(forecast[['issue_date','ensemble_mean_mm','ensemble_median_mm','prob_ge_20mm']].to_string(index=False))
