from __future__ import annotations

import json
import math
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

ROOT = Path('phase3/walkamin')
BASE_MODEL = ROOT / 'Walkamin_2008_2009_baseline.apsimx'
BASE_MET = ROOT / 'Walkamin_2008_2009.met'
START = pd.Timestamp('2008-04-01')
DRYOFF = pd.Timestamp('2009-05-14')
FINISH = pd.Timestamp('2009-06-24')
LAT = -17.13
LON = 145.43
MEMBERS = [0, 1, 2, 3, 4]
FORECAST_DAYS = 3
MAX_HOLD_DAYS = 2
RAIN_THRESHOLDS = [10, 20, 30, 40]
PROB_THRESHOLDS = [0.20, 0.40, 0.60, 0.80, 1.00]
MAX_EVENT_MM = 50.0
CACHE = ROOT / 'gefs_simple_rule_cache_2008'
CACHE.mkdir(parents=True, exist_ok=True)

# Reuse the tested Walkamin scheduled-APSIM helper functions without running the
# consequence experiment at the bottom of that file.
helper_path = Path('phase3/run_walkamin_consequence_experiment.py')
helper_source = helper_path.read_text()
helper_prefix = helper_source.split('\nstrategies = [', 1)[0]
ns: dict = {'__name__': 'clover_walkamin_simple_rule_helpers'}
exec(compile(helper_prefix, str(helper_path), 'exec'), ns)

if not BASE_MODEL.exists() or not BASE_MET.exists():
    raise RuntimeError('Walkamin 2008-09 baseline files have not been prepared')


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


def fetch_member_3d(issue: pd.Timestamp, member: int) -> float:
    label = 'c00' if member == 0 else f'p{member:02d}'
    cache_json = CACHE / f'{issue:%Y%m%d}_{label}_3d.json'
    if cache_json.exists():
        return float(json.loads(cache_json.read_text())['rain72_mm'])

    member_cache = CACHE / issue.strftime('%Y%m%d') / label
    last_error = None
    for attempt in range(1, 4):
        try:
            member_cache.mkdir(parents=True, exist_ok=True)
            H = Herbie(
                issue.strftime('%Y-%m-%d'),
                model='gefs_reforecast',
                fxx=72,
                member=member,
                variable_level='apcp_sfc',
                save_dir=str(member_cache),
            )
            inv = H.inventory().copy()
            inv['search_this'] = inv['search_this'].astype(str)
            windows = inv['search_this'].apply(parse_window)
            inv['start_hour'] = [w[0] for w in windows]
            inv['end_hour'] = [w[1] for w in windows]
            inv['duration_hour'] = inv['end_hour'] - inv['start_hour']
            selected = inv[
                inv['end_hour'].notna()
                & (inv['end_hour'] <= 72)
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
                    if 0 < hour <= 72:
                        values_by_hour[hour] = float(value)
            needed = [6 * i for i in range(1, 13)]
            missing = [h for h in needed if h not in values_by_hour]
            if missing:
                raise RuntimeError(f'{issue.date()} {label}: missing lead hours {missing}')
            total = float(sum(values_by_hour[h] for h in needed))
            cache_json.write_text(json.dumps({
                'issue_date': str(issue.date()),
                'member': label,
                'rain72_mm': total,
            }, indent=2))
            return total
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                shutil.rmtree(member_cache, ignore_errors=True)
                time.sleep(attempt * 2)
    raise RuntimeError(f'GEFS failed for {issue.date()} {label}: {last_error}')


def fetch_ensemble_3d(issue: pd.Timestamp) -> dict[str, float]:
    out = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_member_3d, issue, m): m for m in MEMBERS}
        for fut in as_completed(futures):
            m = futures[fut]
            label = 'c00' if m == 0 else f'p{m:02d}'
            out[label] = float(fut.result())
    return dict(sorted(out.items()))


def observed_3d(issue: pd.Timestamp) -> float:
    weather = ns['ACTUAL_WEATHER']
    total = 0.0
    for i in range(FORECAST_DAYS):
        d = issue + pd.Timedelta(days=i)
        if d in weather.index:
            total += float(weather.loc[d, 'rain'])
    return float(total)


def ordinary_amount(row) -> float:
    # Match the autonomous baseline manager used by the original Walkamin forecast POC:
    # irrigate up to 50 mm, limited only by the current root-zone deficit.
    return max(0.0, min(MAX_EVENT_MM, float(row['RootDeficit'])))


def run_strategy(name: str, rain_threshold: float | None, prob_threshold: float | None):
    schedule: dict[pd.Timestamp, float] = {}
    decisions = []
    search = START
    hold_days = 0
    iterations = 0

    while search < DRYOFF:
        iterations += 1
        if iterations > 160:
            raise RuntimeError(f'{name}: too many decision iterations')
        current = ns['actual_strategy_run'](schedule, f'simple_{name}_state')
        d, row = ns['next_trigger'](current, schedule, search)
        if d is None:
            break

        baseline_amount = ordinary_amount(row)
        forecast_probability = None
        ensemble_mean = None
        member_totals = None
        decision = 'IRRIGATE'
        reason = 'BASELINE_TRIGGER'
        amount = baseline_amount

        if rain_threshold is not None and prob_threshold is not None:
            ensemble = fetch_ensemble_3d(d)
            member_totals = list(ensemble.values())
            arr = np.array(member_totals, dtype=float)
            ensemble_mean = float(arr.mean())
            forecast_probability = float(np.mean(arr >= float(rain_threshold)))
            says_wait = forecast_probability >= float(prob_threshold)
            if says_wait and hold_days < MAX_HOLD_DAYS:
                amount = 0.0
                decision = 'HOLD'
                reason = 'FORECAST_RAIN_RULE'
                hold_days += 1
            else:
                hold_days = 0
        else:
            hold_days = 0

        if amount > 1e-9:
            schedule[d] = float(amount)

        decisions.append({
            'scenario': name,
            'date': d.date().isoformat(),
            'rain_threshold_mm': rain_threshold,
            'probability_threshold': prob_threshold,
            'forecast_probability': forecast_probability,
            'forecast_mean_mm': ensemble_mean,
            'observed_next3d_rain_mm': observed_3d(d),
            'member_rain72_mm': member_totals,
            'root_deficit_mm': float(row['RootDeficit']),
            'root_pawc_mm': float(row['RootPAWC']),
            'baseline_amount_mm': baseline_amount,
            'selected_irrigation_mm': float(amount),
            'decision': decision,
            'reason': reason,
        })
        print(
            f'{name} {d.date()} {decision} amount={amount:.1f} '
            f'P={forecast_probability if forecast_probability is not None else "-"}',
            flush=True,
        )
        search = d + pd.Timedelta(days=1)

    final = ns['actual_strategy_run'](schedule, f'simple_{name}_final')
    final.to_csv(ROOT / f'simple_{name}_daily.csv', index=False)
    pd.DataFrame(decisions).to_csv(ROOT / f'simple_{name}_decisions.csv', index=False)
    return schedule, final, decisions


def sumcol(df: pd.DataFrame, col: str) -> float:
    if col not in df:
        return math.nan
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0).sum())


def final_value(df: pd.DataFrame, col: str) -> float:
    x = df[df['Clock.Today'] <= FINISH]
    if x.empty or col not in x:
        return math.nan
    return float(x.iloc[-1][col])


def summary_row(name, schedule, df, decisions, rain_threshold=None, prob_threshold=None):
    sw = pd.to_numeric(df.get('Sugarcane.swdef_photo'), errors='coerce') if 'Sugarcane.swdef_photo' in df else pd.Series(dtype=float)
    return {
        'scenario': name,
        'forecast_source': 'GEFS historical reforecast' if rain_threshold is not None else 'None',
        'forecast_horizon_days': FORECAST_DAYS if rain_threshold is not None else 0,
        'rain_threshold_mm': rain_threshold,
        'probability_threshold': prob_threshold,
        'irrigation_events': len(schedule),
        'total_irrigation_mm': float(sum(schedule.values())),
        'hold_decisions': int(sum(1 for d in decisions if d['decision'] == 'HOLD')),
        'rain_mm': sumcol(df, 'Rain'),
        'runoff_mm': sumcol(df, 'Runoff'),
        'drainage_mm': sumcol(df, 'Drainage'),
        'min_swdef_photo': float(sw.min()) if not sw.empty else math.nan,
        'stress_days_swdef_lt_0_9': int((sw < 0.9).sum()) if not sw.empty else 0,
        'end_cane_wt': final_value(df, 'Sugarcane.cane_wt'),
        'end_sucrose_wt': final_value(df, 'Sugarcane.sucrose_wt'),
    }


# Run one reconstructed non-forecast baseline first.
all_results = {}
base_schedule, base_df, base_decisions = run_strategy('baseline', None, None)
all_results['baseline'] = (base_schedule, base_df, base_decisions)
rows = [summary_row('baseline', base_schedule, base_df, base_decisions)]

# Then run the transparent rainfall-rule grid.
for rain_threshold in RAIN_THRESHOLDS:
    for prob_threshold in PROB_THRESHOLDS:
        pct = int(round(prob_threshold * 100))
        name = f'gefs3_{int(rain_threshold)}mm_{pct}pct'
        schedule, df, decisions = run_strategy(name, rain_threshold, prob_threshold)
        all_results[name] = (schedule, df, decisions)
        rows.append(summary_row(name, schedule, df, decisions, rain_threshold, prob_threshold))

summary = pd.DataFrame(rows)
base = summary.loc[summary['scenario'] == 'baseline'].iloc[0]
summary['water_saved_mm'] = float(base['total_irrigation_mm']) - summary['total_irrigation_mm']
summary['water_saved_pct'] = np.where(
    float(base['total_irrigation_mm']) > 0,
    100.0 * summary['water_saved_mm'] / float(base['total_irrigation_mm']),
    0.0,
)
summary['events_avoided'] = int(base['irrigation_events']) - summary['irrigation_events']
summary['cane_change_pct'] = np.where(
    float(base['end_cane_wt']) != 0,
    100.0 * (summary['end_cane_wt'] - float(base['end_cane_wt'])) / float(base['end_cane_wt']),
    0.0,
)
summary['sucrose_change_pct'] = np.where(
    float(base['end_sucrose_wt']) != 0,
    100.0 * (summary['end_sucrose_wt'] - float(base['end_sucrose_wt'])) / float(base['end_sucrose_wt']),
    0.0,
)
summary.to_csv(ROOT / 'walkamin_2008_simple_rule_grid_summary.csv', index=False)

# Combined decision table.
all_decisions = []
for name, (_, _, decisions) in all_results.items():
    all_decisions.extend(decisions)
(ROOT / 'walkamin_2008_simple_rule_grid_decisions.json').write_text(json.dumps(all_decisions, indent=2))
pd.DataFrame([{k: v for k, v in d.items() if k != 'member_rain72_mm'} for d in all_decisions]).to_csv(
    ROOT / 'walkamin_2008_simple_rule_grid_decisions.csv', index=False
)

# UI-ready summary and decision rows.
ui_summary = []
ui_timeseries = []
base_total = float(base['total_irrigation_mm'])
base_events = int(base['irrigation_events'])
base_cane = float(base['end_cane_wt'])
base_runoff = float(base['runoff_mm'])
base_drainage = float(base['drainage_mm'])
for _, r in summary[summary['scenario'] != 'baseline'].iterrows():
    scenario_id = f"walkamin_2008_09_gefs_3d_{int(r['rain_threshold_mm'])}mm_{int(round(r['probability_threshold']*100))}pct"
    ui_summary.append({
        'scenario_id': scenario_id,
        'site': 'Walkamin',
        'soil': 'Walkamin reference soil',
        'crop': 'Sugarcane – plant crop',
        'period': '2008–09',
        'irrigation_amount_mm': MAX_EVENT_MM,
        'irrigation_trigger': 'APSIM root-zone depletion rule',
        'forecast_horizon_days': FORECAST_DAYS,
        'rain_threshold_mm': float(r['rain_threshold_mm']),
        'probability_threshold': float(r['probability_threshold']),
        'forecast_source': 'GEFS historical reforecast',
        'n_years': 1,
        'mean_baseline_irrigation_mm': base_total,
        'mean_forecast_irrigation_mm': float(r['total_irrigation_mm']),
        'mean_water_saved_mm': float(r['water_saved_mm']),
        'mean_water_saved_pct': float(r['water_saved_pct']),
        'mean_baseline_events': base_events,
        'mean_forecast_events': int(r['irrigation_events']),
        'mean_irrigations_avoided': int(r['events_avoided']),
        'mean_hold_days': int(r['hold_decisions']),
        'mean_baseline_runoff_mm': base_runoff,
        'mean_forecast_runoff_mm': float(r['runoff_mm']),
        'mean_baseline_drainage_mm': base_drainage,
        'mean_forecast_drainage_mm': float(r['drainage_mm']),
        'mean_baseline_cane_yield_t_ha': base_cane / 100.0,
        'mean_forecast_cane_yield_t_ha': float(r['end_cane_wt']) / 100.0,
        'mean_yield_change_pct': float(r['cane_change_pct']),
        'baseline_end_cane_wt': base_cane,
        'forecast_end_cane_wt': float(r['end_cane_wt']),
        'baseline_end_sucrose_wt': float(base['end_sucrose_wt']),
        'forecast_end_sucrose_wt': float(r['end_sucrose_wt']),
        'sucrose_change_pct': float(r['sucrose_change_pct']),
        'years_with_saving': 1 if float(r['water_saved_mm']) > 0 else 0,
        'years_with_yield_penalty': 1 if float(r['cane_change_pct']) < -0.5 else 0,
    })

    name = str(r['scenario'])
    schedule, _, decisions = all_results[name]
    base_schedule = all_results['baseline'][0]
    for d in decisions:
        date = d['date']
        dt = pd.Timestamp(date)
        ui_timeseries.append({
            'scenario_id': scenario_id,
            'year': 2008 if dt.year == 2008 else 2009,
            'date': date,
            'rain_mm': float(ns['ACTUAL_WEATHER'].loc[dt, 'rain']) if dt in ns['ACTUAL_WEATHER'].index else 0.0,
            'forecast_probability': d['forecast_probability'],
            'forecast_mean_mm': d['forecast_mean_mm'],
            'baseline_irrigation_mm': float(base_schedule.get(dt, 0.0)),
            'forecast_irrigation_mm': float(schedule.get(dt, 0.0)),
            'decision_changed': d['decision'] == 'HOLD' or abs(float(base_schedule.get(dt, 0.0)) - float(schedule.get(dt, 0.0))) > 0.01,
            'actual_next3d_rain_mm': d['observed_next3d_rain_mm'],
            'decision_note': 'Forecast rainfall rule held irrigation.' if d['decision'] == 'HOLD' else '',
        })

(ROOT / 'walkamin_2008_ui_scenario_summary.json').write_text(json.dumps(ui_summary, indent=2))
(ROOT / 'walkamin_2008_ui_scenario_timeseries.json').write_text(json.dumps(ui_timeseries, indent=2))

# Full-season arrays for the UI irrigation chart. Forecast points are stored only
# on dates where that strategy actually faced an irrigation decision.
dates = pd.date_range(START, FINISH, freq='D')
weather = ns['ACTUAL_WEATHER']
observed72 = []
for d in dates:
    observed72.append(observed_3d(d))
base_irrigation = [float(base_schedule.get(d, 0.0)) for d in dates]
full = {
    'start': START.date().isoformat(),
    'end': FINISH.date().isoformat(),
    'observed72': observed72,
    'baseline_irrigation': base_irrigation,
    'scenarios': {},
}
for _, r in summary[summary['scenario'] != 'baseline'].iterrows():
    name = str(r['scenario'])
    scenario_id = f"walkamin_2008_09_gefs_3d_{int(r['rain_threshold_mm'])}mm_{int(round(r['probability_threshold']*100))}pct"
    schedule, _, decisions = all_results[name]
    full['scenarios'][scenario_id] = {
        'forecast_irrigation': [float(schedule.get(d, 0.0)) for d in dates],
        'gefs': {
            d['date']: [d['forecast_mean_mm'], d['forecast_probability']]
            for d in decisions if d['forecast_mean_mm'] is not None
        },
    }
(ROOT / 'walkamin_2008_ui_full_season.json').write_text(json.dumps(full))

method = {
    'site': 'Walkamin',
    'season': '2008-04-01 to 2009-06-24',
    'baseline': 'APSIM root-zone depletion trigger; maximum 50 mm application; 10-day return interval',
    'forecast_product': 'NOAA GEFSv12 historical reforecast; five members; next 72 h rainfall',
    'rule': 'At each ordinary irrigation trigger, hold irrigation when P(72 h rainfall >= rainfall threshold) >= probability threshold; otherwise irrigate normally.',
    'rain_thresholds_mm': RAIN_THRESHOLDS,
    'probability_thresholds': PROB_THRESHOLDS,
    'maximum_consecutive_hold_days': MAX_HOLD_DAYS,
    'forecast_retrieval': 'On-demand only when a strategy reaches an irrigation decision; cached and reused across grid scenarios.',
    'note': 'Proof-of-concept thresholds, not final agronomic recommendations. GEFS 00 UTC issue date is provisionally mapped to the APSIM local decision date.',
}
(ROOT / 'walkamin_2008_simple_rule_grid_method.json').write_text(json.dumps(method, indent=2))

print('\n=== WALKAMIN SIMPLE FORECAST RULE GRID ===')
print(summary.to_string(index=False))
