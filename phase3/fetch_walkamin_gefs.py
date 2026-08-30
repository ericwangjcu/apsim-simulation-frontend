from __future__ import annotations

import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

ROOT = Path('phase3/walkamin')
CACHE = Path('phase3/herbie_cache_walkamin')
CACHE.mkdir(parents=True, exist_ok=True)
LAT = -17.13
LON = 145.43
MEMBERS = [0, 1, 2, 3, 4]
MAX_HOUR = 72
RAIN_THRESHOLD_MM = 20.0
PROB_THRESHOLD = 0.60
IRRIGATION_END = pd.Timestamp('2009-05-13')


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
    member_cache = CACHE / issue.strftime('%Y%m%d') / label
    last_error = None
    for attempt in range(1, 4):
        if attempt > 1:
            shutil.rmtree(member_cache, ignore_errors=True)
            time.sleep(attempt)
        member_cache.mkdir(parents=True, exist_ok=True)
        try:
            H = Herbie(issue.strftime('%Y-%m-%d'), model='gefs_reforecast', fxx=72,
                       member=member, variable_level='apcp_sfc', save_dir=str(member_cache))
            inv = H.inventory().copy()
            inv['search_this'] = inv['search_this'].astype(str)
            windows = inv['search_this'].apply(parse_window)
            inv['start_hour'] = [w[0] for w in windows]
            inv['end_hour'] = [w[1] for w in windows]
            inv['duration_hour'] = inv['end_hour'] - inv['start_hour']
            selected = inv[inv['end_hour'].notna() & (inv['end_hour'] <= MAX_HOUR) & (inv['duration_hour'] == 6)].copy()
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
    print(issue.date(), 'mean=', round(row['ensemble_mean_mm'], 2), 'P>=20=', row['prob_ge_20mm'])
    return issue, row


def fetch_dates(dates):
    out = {}
    dates = [pd.Timestamp(d) for d in sorted(set(dates))]
    if not dates:
        return out
    # Every issue/member has a separate cache directory, so date-level parallelism
    # is safe and much faster than the earlier sequential proof-of-concept.
    with ThreadPoolExecutor(max_workers=min(8, len(dates))) as pool:
        futures = {pool.submit(one_date, d): d for d in dates}
        for fut in as_completed(futures):
            d, row = fut.result()
            out[d] = row
    return out


base = pd.read_csv(ROOT / 'Walkamin_2008_2009_baseline.Report.csv')
base['Clock.Today'] = pd.to_datetime(base['Clock.Today'])
events = base[base['AppliedToday'] > 0].copy()
events.to_csv(ROOT / 'walkamin_2008_baseline_events.csv', index=False)

event_dates = [pd.Timestamp(d) for d in events['Clock.Today'].dt.normalize().unique()]
rows_by_date = fetch_dates(event_dates)

followups = set()
for d, row in rows_by_date.items():
    if row['prob_ge_20mm'] >= PROB_THRESHOLD:
        for offset in (1, 2):
            x = d + pd.Timedelta(days=offset)
            if x <= IRRIGATION_END:
                followups.add(x)
rows_by_date.update(fetch_dates([d for d in followups if d not in rows_by_date]))

forecast = pd.DataFrame(rows_by_date.values()).sort_values('issue_date')
forecast.to_csv(ROOT / 'walkamin_2008_gefs_forecasts.csv', index=False)
meta = {
    'site': 'Walkamin', 'lat': LAT, 'lon': LON, 'season': '2008-04-01 to 2009-06-24',
    'forecast_horizon_hours': 72, 'members': ['c00','p01','p02','p03','p04'],
    'rain_threshold_mm': RAIN_THRESHOLD_MM, 'probability_threshold': PROB_THRESHOLD,
    'source': 'NOAA GEFSv12 reforecast via Herbie / NOAA AWS open-data archive',
    'retrieval': 'parallel baseline decision dates; +1/+2 days only for decisions meeting hold threshold',
    'note': 'Proof-of-concept. 00 UTC issue date mapped directly to APSIM decision date; timezone alignment not yet refined.'
}
(ROOT / 'walkamin_2008_gefs_metadata.json').write_text(json.dumps(meta, indent=2))
print(f'Completed {len(forecast)} Walkamin forecast issue dates')
print(forecast[['issue_date','ensemble_mean_mm','ensemble_median_mm','prob_ge_20mm']].to_string(index=False))
