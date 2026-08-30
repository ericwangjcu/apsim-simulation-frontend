from __future__ import annotations

import json
import math
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

ROOT = Path('phase3/walkamin')
YEAR = int(os.environ.get('SEASON_START_YEAR', '2008'))
START = pd.Timestamp(year=YEAR, month=4, day=1)
FINISH = pd.Timestamp(year=YEAR + 1, month=6, day=24)
DRYOFF = pd.Timestamp(year=YEAR + 1, month=5, day=14)
BASE_MODEL = ROOT / f'Walkamin_{YEAR}_{YEAR+1}_baseline.apsimx'
BASE_MET = ROOT / f'Walkamin_{YEAR}_{YEAR+1}.met'
CACHE = ROOT / f'gefs_risk3_cache_{YEAR}'
CACHE.mkdir(parents=True, exist_ok=True)

LAT = -17.13
LON = 145.43
FORECAST_DAYS = 3
RETURN_DAYS = 10
DEPLETION_FRACTION = 0.50
TARGET_AVAILABLE_FRACTION = 0.75
SAFE_SWDEF = 0.90
SAFE_PROB = 0.80
CANDIDATE_STEP_MM = 10.0
MEMBERS = [0, 1, 2, 3, 4]

# Reuse the tested APSIM model-writing and reporting helpers without executing the
# single-season experiment at the bottom of that file.
helper_path = Path('phase3/run_walkamin_consequence_experiment.py')
helper_source = helper_path.read_text()
helper_prefix = helper_source.split('\nstrategies = [', 1)[0]
ns: dict = {'__name__': 'clover_walkamin_helpers'}
exec(compile(helper_prefix, str(helper_path), 'exec'), ns)

if not BASE_MODEL.exists() or not BASE_MET.exists():
    raise RuntimeError(f'Missing prepared Walkamin season files for {YEAR}')

MET_PREFIX, ACTUAL_WEATHER = ns['parse_met'](BASE_MET)
ns.update({
    'ROOT': ROOT,
    'BASE_MODEL': BASE_MODEL,
    'BASE_MET': BASE_MET,
    'START': START,
    'FINISH': FINISH,
    'DRYOFF': DRYOFF,
    'CACHE': CACHE,
    'MET_PREFIX': MET_PREFIX,
    'ACTUAL_WEATHER': ACTUAL_WEATHER,
    'RETURN_DAYS': RETURN_DAYS,
    'DEPLETION_FRACTION': DEPLETION_FRACTION,
    'TARGET_AVAILABLE_FRACTION': TARGET_AVAILABLE_FRACTION,
    'SAFE_SWDEF': SAFE_SWDEF,
    'SAFE_PROB': SAFE_PROB,
})

# The shared scheduled-manager helper was originally written for the 2008-09 crop.
# Replace that output-only dry-off date for the season being run here.
_original_schedule_manager_code = ns['schedule_manager_code']
def schedule_manager_code_dynamic(schedule):
    code = _original_schedule_manager_code(schedule)
    return code.replace(
        'new DateTime(2009, 5, 14)',
        f'new DateTime({DRYOFF.year}, {DRYOFF.month}, {DRYOFF.day})',
    )
ns['schedule_manager_code'] = schedule_manager_code_dynamic


def baseline_refill_amount(row) -> float:
    """Variable refill amount to the 75% available-water target, with no event cap."""
    pawc = float(row['RootPAWC'])
    deficit = float(row['RootDeficit'])
    target_deficit = (1.0 - TARGET_AVAILABLE_FRACTION) * pawc
    return max(0.0, deficit - target_deficit)


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


def fetch_member_3d(issue: pd.Timestamp, member: int) -> list[float]:
    label = 'c00' if member == 0 else f'p{member:02d}'
    cache_json = CACHE / f'{issue:%Y%m%d}_{label}_3d.json'
    if cache_json.exists():
        return json.loads(cache_json.read_text())['daily_mm']

    member_cache = CACHE / issue.strftime('%Y%m%d') / label
    last_error = None
    for attempt in range(1, 4):
        try:
            member_cache.mkdir(parents=True, exist_ok=True)
            H = Herbie(issue.strftime('%Y-%m-%d'), model='gefs_reforecast', fxx=72,
                       member=member, variable_level='apcp_sfc', save_dir=str(member_cache))
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
            six_hourly = [values_by_hour[h] for h in needed]
            daily = [float(sum(six_hourly[i:i+4])) for i in range(0, 12, 4)]
            cache_json.write_text(json.dumps({
                'issue_date': str(issue.date()),
                'member': label,
                'daily_mm': daily,
            }, indent=2))
            return daily
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                shutil.rmtree(member_cache, ignore_errors=True)
                time.sleep(attempt * 2)
    raise RuntimeError(f'GEFS failed for {issue.date()} {label}: {last_error}')


def fetch_ensemble_3d(issue: pd.Timestamp) -> dict[str, list[float]]:
    result = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(fetch_member_3d, issue, m): m for m in MEMBERS}
        for fut in as_completed(futs):
            m = futs[fut]
            label = 'c00' if m == 0 else f'p{m:02d}'
            result[label] = fut.result()
    return dict(sorted(result.items()))


def write_risk_met(issue: pd.Timestamp, rain3: list[float], label: str) -> Path:
    """Use the 3-day forecast, then assume zero rain until the 10-day return window ends.

    Temperature and radiation remain observed in this POC. The zero-rain extension is
    deliberately conservative so the optimiser does not use unknown day-4-to-day-10
    rainfall to justify a smaller irrigation application.
    """
    df = ACTUAL_WEATHER.copy()
    for i in range(RETURN_DAYS):
        d = issue + pd.Timedelta(days=i)
        if d not in df.index:
            continue
        df.loc[d, 'rain'] = float(rain3[i]) if i < FORECAST_DAYS else 0.0
    path = ROOT / f'Risk3_{YEAR}_{issue:%Y%m%d}_{label}.met'
    ns['write_met'](df, path)
    return path


def actual_first3(issue: pd.Timestamp) -> list[float]:
    vals = []
    for i in range(FORECAST_DAYS):
        d = issue + pd.Timedelta(days=i)
        vals.append(float(ACTUAL_WEATHER.loc[d, 'rain']) if d in ACTUAL_WEATHER.index else 0.0)
    return vals


def cleanup_model(model_path: Path):
    for p in [model_path, model_path.with_suffix('.db'), model_path.with_suffix('.Report.csv')]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def candidate_consequence(schedule: dict[pd.Timestamp, float], issue: pd.Timestamp,
                          amount: float, met_path: Path, label: str,
                          assessment_days: int) -> dict:
    candidate_schedule = {d: a for d, a in schedule.items() if d < issue}
    if amount > 1e-6:
        candidate_schedule[issue] = float(amount)
    amount_tag = int(round(amount * 10))
    model = ROOT / f'Risk3Look_{YEAR}_{issue:%Y%m%d}_{label}_{amount_tag}.apsimx'
    ns['make_scheduled_model'](candidate_schedule, met_path, model)
    df = ns['run_apsim'](model)
    period = df[
        (df['Clock.Today'] >= issue)
        & (df['Clock.Today'] < issue + pd.Timedelta(days=assessment_days))
    ].copy()
    if period.empty:
        cleanup_model(model)
        raise RuntimeError(f'Empty candidate lookahead for {issue.date()} amount={amount}')
    sw = pd.to_numeric(period['Sugarcane.swdef_photo'], errors='coerce').dropna()
    min_sw = float(sw.min()) if not sw.empty else 1.0
    end = period.iloc[-1]
    result = {
        'safe': bool(min_sw >= SAFE_SWDEF),
        'min_swdef': min_sw,
        'end_deficit': float(end['RootDeficit']),
        'end_pawc': float(end['RootPAWC']),
        'runoff_mm': float(pd.to_numeric(period['Runoff'], errors='coerce').fillna(0).sum()) if 'Runoff' in period else math.nan,
        'drainage_mm': float(pd.to_numeric(period['Drainage'], errors='coerce').fillna(0).sum()) if 'Drainage' in period else math.nan,
    }
    cleanup_model(model)
    return result


def candidate_grid(row) -> list[float]:
    full = baseline_refill_amount(row)
    if full <= 0:
        return []
    vals = []
    x = CANDIDATE_STEP_MM
    while x < full - 1e-6:
        vals.append(round(x, 6))
        x += CANDIDATE_STEP_MM
    vals.append(round(full, 6))
    return sorted(set(vals))


def evaluate_gefs_candidates(schedule, issue, row, records):
    ensemble = fetch_ensemble_3d(issue)
    met_paths = {
        label: write_risk_met(issue, rain3, f'gefs_{label}')
        for label, rain3 in ensemble.items()
    }

    # First ask the tactical question: can we safely wait one day and reassess?
    wait_results = []
    for label, met in met_paths.items():
        res = candidate_consequence(schedule, issue, 0.0, met, f'wait_{label}', FORECAST_DAYS)
        wait_results.append((label, res))
        records.append({
            'issue_date': issue,
            'candidate_mm': 0.0,
            'assessment_days': FORECAST_DAYS,
            'member': label,
            'forecast_rain_3d_mm': float(sum(ensemble[label])),
            **res,
        })
    p_wait = float(np.mean([r['safe'] for _, r in wait_results]))
    if p_wait >= SAFE_PROB:
        return 0.0, p_wait, 'WAIT'

    # If waiting is too risky, find the smallest application that remains safe until
    # the irrigation system is available again. Only the first three rain days are
    # forecast; days 4-10 assume zero rain.
    full = baseline_refill_amount(row)
    best = None
    best_prob = 0.0
    for amount in candidate_grid(row):
        results = []
        for label, met in met_paths.items():
            res = candidate_consequence(schedule, issue, amount, met, f'amt_{label}', RETURN_DAYS)
            results.append((label, res))
            records.append({
                'issue_date': issue,
                'candidate_mm': amount,
                'assessment_days': RETURN_DAYS,
                'member': label,
                'forecast_rain_3d_mm': float(sum(ensemble[label])),
                **res,
            })
        p_safe = float(np.mean([r['safe'] for _, r in results]))
        if p_safe >= SAFE_PROB:
            best = amount
            best_prob = p_safe
            break
    if best is None:
        return full, best_prob, 'FALLBACK_BASELINE_REFILL'
    return float(best), best_prob, 'RISK_MINIMUM'


def evaluate_perfect_candidates(schedule, issue, row, records):
    rain3 = actual_first3(issue)
    met = write_risk_met(issue, rain3, 'perfect')
    wait_res = candidate_consequence(schedule, issue, 0.0, met, 'perfect_wait', FORECAST_DAYS)
    records.append({
        'issue_date': issue,
        'candidate_mm': 0.0,
        'assessment_days': FORECAST_DAYS,
        'member': 'perfect',
        'forecast_rain_3d_mm': float(sum(rain3)),
        **wait_res,
    })
    if wait_res['safe']:
        return 0.0, 1.0, 'WAIT'

    full = baseline_refill_amount(row)
    for amount in candidate_grid(row):
        res = candidate_consequence(schedule, issue, amount, met, 'perfect_amt', RETURN_DAYS)
        records.append({
            'issue_date': issue,
            'candidate_mm': amount,
            'assessment_days': RETURN_DAYS,
            'member': 'perfect',
            'forecast_rain_3d_mm': float(sum(rain3)),
            **res,
        })
        if res['safe']:
            return float(amount), 1.0, 'RISK_MINIMUM'
    return full, 0.0, 'FALLBACK_BASELINE_REFILL'


def run_strategy(name: str, mode: str):
    schedule: dict[pd.Timestamp, float] = {}
    search = START
    decisions = []
    candidate_records = []
    iteration = 0
    while search < DRYOFF:
        iteration += 1
        if iteration > 120:
            raise RuntimeError(f'{name}: too many decision iterations')
        current = ns['actual_strategy_run'](schedule, f'risk3_{YEAR}_{name}_state')
        d, row = ns['next_trigger'](current, schedule, search)
        if d is None:
            break
        full = baseline_refill_amount(row)
        if mode == 'baseline':
            amount, p_safe, reason = full, 0.0, 'BASELINE_REFILL'
        elif mode == 'perfect':
            amount, p_safe, reason = evaluate_perfect_candidates(schedule, d, row, candidate_records)
        elif mode == 'gefs':
            amount, p_safe, reason = evaluate_gefs_candidates(schedule, d, row, candidate_records)
        else:
            raise ValueError(mode)

        action = 'WAIT' if amount <= 1e-6 else 'IRRIGATE'
        decisions.append({
            'season_start_year': YEAR,
            'strategy': name,
            'date': d,
            'root_deficit_mm': float(row['RootDeficit']),
            'root_pawc_mm': float(row['RootPAWC']),
            'fraction_available': float(row['FractionAvailable']),
            'swdef_photo': float(row['Sugarcane.swdef_photo']),
            'baseline_refill_mm': full,
            'selected_irrigation_mm': amount,
            'safe_probability': p_safe,
            'decision': action,
            'reason': reason,
            'days_since_irrigation': ns['days_since_last_irrigation'](schedule, d),
        })
        if amount > 1e-6:
            schedule[d] = float(amount)
        print(
            f'{YEAR} {name} {d.date()} {action} {amount:.1f} mm '
            f'(baseline refill {full:.1f}, P-safe={p_safe:.2f}, {reason})',
            flush=True,
        )
        search = d + pd.Timedelta(days=1)

    final = ns['actual_strategy_run'](schedule, f'risk3_{YEAR}_{name}_final')
    pd.DataFrame(decisions).to_csv(ROOT / f'risk3_{YEAR}_{name}_decisions.csv', index=False)
    if candidate_records:
        pd.DataFrame(candidate_records).to_csv(ROOT / f'risk3_{YEAR}_{name}_candidates.csv', index=False)
    final.to_csv(ROOT / f'risk3_{YEAR}_{name}_daily.csv', index=False)
    return schedule, final, decisions


def sumcol(df, col):
    if col not in df:
        return math.nan
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0).sum())


def summarise(name, schedule, df, decisions):
    end = df[df['Clock.Today'] <= FINISH].iloc[-1]
    sw = pd.to_numeric(df['Sugarcane.swdef_photo'], errors='coerce')
    reductions = [
        max(0.0, float(d['baseline_refill_mm']) - float(d['selected_irrigation_mm']))
        for d in decisions if d['decision'] == 'IRRIGATE'
    ]
    return {
        'season_start_year': YEAR,
        'strategy': name,
        'irrigation_events': len(schedule),
        'total_irrigation_mm': float(sum(schedule.values())),
        'rain_mm': sumcol(df, 'Rain'),
        'runoff_mm': sumcol(df, 'Runoff'),
        'drainage_mm': sumcol(df, 'Drainage'),
        'soil_evaporation_mm': sumcol(df, 'SoilEvaporation'),
        'crop_transpiration_mm': sumcol(df, 'Sugarcane.ep'),
        'min_swdef_photo': float(sw.min()),
        'stress_days_swdef_lt_0_9': int((sw < 0.9).sum()),
        'stress_days_swdef_lt_0_7': int((sw < 0.7).sum()),
        'end_biomass': float(end['Sugarcane.biomass']),
        'end_cane_wt': float(end['Sugarcane.cane_wt']),
        'end_sucrose_wt': float(end['Sugarcane.sucrose_wt']),
        'wait_decisions': int(sum(1 for d in decisions if d['decision'] == 'WAIT')),
        'partial_irrigation_decisions': int(sum(1 for x in reductions if x > 0.01)),
        'sum_immediate_amount_reductions_mm': float(sum(reductions)),
        'last_irrigation_date': max(schedule).date().isoformat() if schedule else '',
    }


strategies = [
    ('baseline_variable', 'baseline'),
    ('perfect3_risk', 'perfect'),
    ('gefs3_risk', 'gefs'),
]

rows = []
schedules = {}
for name, mode in strategies:
    print(f'\n=== {YEAR} {name} ===', flush=True)
    schedule, df, decisions = run_strategy(name, mode)
    schedules[name] = {d.date().isoformat(): float(a) for d, a in sorted(schedule.items())}
    rows.append(summarise(name, schedule, df, decisions))

summary = pd.DataFrame(rows)
base = summary.loc[summary['strategy'] == 'baseline_variable'].iloc[0]
for metric in [
    'total_irrigation_mm', 'runoff_mm', 'drainage_mm', 'soil_evaporation_mm',
    'crop_transpiration_mm', 'end_biomass', 'end_cane_wt', 'end_sucrose_wt',
]:
    summary[f'{metric}_change_vs_baseline'] = summary[metric] - float(base[metric])
summary['irrigation_saved_mm'] = float(base['total_irrigation_mm']) - summary['total_irrigation_mm']
summary['irrigation_saved_pct'] = np.where(
    float(base['total_irrigation_mm']) > 0,
    100.0 * summary['irrigation_saved_mm'] / float(base['total_irrigation_mm']),
    0.0,
)

summary_path = ROOT / f'risk3_{YEAR}_summary.csv'
summary.to_csv(summary_path, index=False)
(ROOT / f'risk3_{YEAR}_schedules.json').write_text(json.dumps(schedules, indent=2))
(ROOT / f'risk3_{YEAR}_method.json').write_text(json.dumps({
    'site': 'Walkamin Tablelands',
    'season': f'{YEAR}-04-01 to {YEAR+1}-06-24',
    'soil': 'Krasnozem/Ferrosol proxy from existing Walkamin POC',
    'baseline_trigger': f'root-zone deficit >= {DEPLETION_FRACTION:.2f} x PAWC',
    'baseline_refill_target': f'{TARGET_AVAILABLE_FRACTION:.2f} fraction available water',
    'forecast_product': 'GEFSv12 reforecast rainfall, 5 members, first 72 hours',
    'risk_rule': f'choose smallest candidate irrigation keeping swdef_photo >= {SAFE_SWDEF} in >= {SAFE_PROB:.0%} of members',
    'wait_rule': f'0 mm accepted when >= {SAFE_PROB:.0%} of members remain safe for the 3-day forecast, then reassess next day',
    'amount_rule': f'candidate irrigation in {CANDIDATE_STEP_MM:.0f} mm increments up to the ordinary variable refill amount',
    'return_interval': f'{RETURN_DAYS} days',
    'uncertainty_after_day3': 'zero rainfall assumed on days 4-10 when sizing a non-zero irrigation amount; conservative operational check',
    'non_rain_forcing': 'observed NASA POWER temperature/radiation retained in this POC',
    'timezone_note': 'GEFS 00 UTC forecast day is mapped directly to the APSIM decision date; local-time alignment remains provisional',
}, indent=2))

print('\n=== 3-DAY RISK STRATEGY SUMMARY ===')
print(summary.to_string(index=False))
