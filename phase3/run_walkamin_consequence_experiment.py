from __future__ import annotations

import copy
import json
import math
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
from herbie import Herbie

ROOT = Path('phase3/walkamin')
TMP = ROOT / 'consequence_tmp'
CACHE = ROOT / 'gefs_consequence_cache'
TMP.mkdir(parents=True, exist_ok=True)
CACHE.mkdir(parents=True, exist_ok=True)

BASE_MODEL = ROOT / 'Walkamin_2008_2009_baseline.apsimx'
BASE_MET = ROOT / 'Walkamin_2008_2009.met'
START = pd.Timestamp('2008-04-01')
DRYOFF = pd.Timestamp('2009-05-14')
FINISH = pd.Timestamp('2009-06-24')
LAT = -17.13
LON = 145.43

DEPLETION_FRACTION = 0.50
TARGET_AVAILABLE_FRACTION = 0.75
MAX_EVENT_MM = 50.0
RETURN_DAYS = 10
SAFE_SWDEF = 0.90
SAFE_PROB = 0.80
MEMBERS = [0, 1, 2, 3, 4]

# This experiment deliberately keeps non-rain weather observed and substitutes only
# GEFS rainfall in the look-ahead simulations. It is a rainfall-forecast POC, not a
# full weather-forecast experiment.


def walk(node):
    if isinstance(node, dict):
        yield node
        for child in node.get('Children', []) if isinstance(node.get('Children'), list) else []:
            yield from walk(child)


def find_by_name(tree, name):
    for node in walk(tree):
        if node.get('Name') == name:
            return node
    raise KeyError(name)


def parse_met(path: Path) -> tuple[list[str], pd.DataFrame]:
    lines = path.read_text().splitlines()
    header_end = None
    for i, line in enumerate(lines):
        if line.strip().lower().startswith('year day'):
            header_end = i + 2
            break
    if header_end is None:
        raise RuntimeError(f'Could not locate met table in {path}')
    prefix = lines[:header_end]
    rows = []
    for line in lines[header_end:]:
        if not line.strip():
            continue
        p = line.split()
        if len(p) < 6:
            continue
        year, day = int(p[0]), int(p[1])
        date = pd.Timestamp(year=year, month=1, day=1) + pd.Timedelta(days=day - 1)
        rows.append({'date': date, 'year': year, 'day': day, 'radn': float(p[2]),
                     'maxt': float(p[3]), 'mint': float(p[4]), 'rain': float(p[5])})
    return prefix, pd.DataFrame(rows).set_index('date')


MET_PREFIX, ACTUAL_WEATHER = parse_met(BASE_MET)


def write_met(df: pd.DataFrame, path: Path):
    lines = list(MET_PREFIX)
    for date, r in df.iterrows():
        lines.append(f"{int(r['year']):4d} {int(r['day']):3d} {r['radn']:6.2f} {r['maxt']:6.2f} {r['mint']:6.2f} {r['rain']:7.2f}")
    path.write_text('\n'.join(lines) + '\n')


def schedule_manager_code(schedule: dict[pd.Timestamp, float]) -> str:
    schedule_lines = []
    for d, amount in sorted(schedule.items()):
        schedule_lines.append(f'            {{ new DateTime({d.year}, {d.month}, {d.day}), {amount:.8f} }},')
    schedule_block = '\n'.join(schedule_lines)
    return f'''using Models.Interfaces;
using Models.Soils;
using Models.PMF;
using Models.Core;
using System;
using System.Collections.Generic;

namespace Models
{{
    [Serializable]
    [System.Xml.Serialization.XmlInclude(typeof(Model))]
    public class Script : Model
    {{
        [Link] private Clock Clock;
        [Link] private Irrigation Irrigation;
        [Link] private IPhysical soilPhysical;
        [Link] private ISoilWater waterBalance;
        [Link] private Sugarcane Sugarcane;

        public double minimumRootDepth {{ get; set; }}
        public double maximumRootDepth {{ get; set; }}
        public double RootDepthUsed {{ get; set; }}
        public double RootSWC {{ get; set; }}
        public double RootDUL {{ get; set; }}
        public double RootLL {{ get; set; }}
        public double RootPAWC {{ get; set; }}
        public double RootDeficit {{ get; set; }}
        public double FractionAvailable {{ get; set; }}
        public double AppliedToday {{ get; set; }}
        public double TotalIrrigation {{ get; set; }}
        public double InDryOff {{ get; set; }}
        private int nLayers;

        private readonly Dictionary<DateTime, double> Schedule = new Dictionary<DateTime, double>
        {{
{schedule_block}
        }};

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {{
            nLayers = soilPhysical.Thickness.Length;
            TotalIrrigation = 0.0;
        }}

        private void CalculateRootZone()
        {{
            RootDepthUsed = Math.Max(minimumRootDepth, Math.Min(maximumRootDepth, Sugarcane.root_depth));
            RootSWC = 0.0; RootDUL = 0.0; RootLL = 0.0;
            double depth = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {{
                double frac = Math.Min(1.0, (RootDepthUsed - depth) / soilPhysical.Thickness[layer]);
                if (frac <= 0.0) break;
                RootSWC += waterBalance.SWmm[layer] * frac;
                RootDUL += soilPhysical.DULmm[layer] * frac;
                RootLL += soilPhysical.LL15mm[layer] * frac;
                depth += soilPhysical.Thickness[layer];
                if (depth >= RootDepthUsed) break;
            }}
            RootPAWC = Math.Max(0.0, RootDUL - RootLL);
            RootDeficit = Math.Max(0.0, RootDUL - RootSWC);
            FractionAvailable = RootPAWC > 0 ? Math.Max(0.0, Math.Min(1.0, (RootSWC - RootLL) / RootPAWC)) : 0.0;
        }}

        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {{
            AppliedToday = 0.0;
            InDryOff = Clock.Today >= new DateTime(2009, 5, 14) ? 1.0 : 0.0;
            if (Sugarcane.crop_status != "alive") return;
            CalculateRootZone();
            double amount;
            if (Schedule.TryGetValue(Clock.Today.Date, out amount) && amount > 0.0)
            {{
                Irrigation.Apply(amount);
                AppliedToday = amount;
                TotalIrrigation += amount;
            }}
        }}
    }}
}}'''


def make_scheduled_model(schedule: dict[pd.Timestamp, float], met_path: Path, out_model: Path):
    tree = json.loads(BASE_MODEL.read_text())
    weather = find_by_name(tree, 'Weather')
    weather['FileName'] = f'/test-run/{met_path.name}'
    manager = find_by_name(tree, 'WalkaminIrrigation')
    manager['Name'] = 'AdaptiveIrrigation'
    manager['Code'] = schedule_manager_code(schedule)
    manager['Parameters'] = [
        {'Key': 'minimumRootDepth', 'Value': '300'},
        {'Key': 'maximumRootDepth', 'Value': '1800'},
    ]
    report = find_by_name(tree, 'Report')
    new_vars = []
    for v in report['VariableNames']:
        v = v.replace('[WalkaminIrrigation].Script.', '[AdaptiveIrrigation].Script.')
        # Old autonomous-manager fields are not present in the scheduled manager.
        if 'DaysSinceIrrigation' in v:
            continue
        new_vars.append(v)
    report['VariableNames'] = new_vars
    needed = [
        '[AdaptiveIrrigation].Script.AppliedToday as AppliedToday',
        '[AdaptiveIrrigation].Script.TotalIrrigation as TotalIrrigation',
        '[AdaptiveIrrigation].Script.RootDepthUsed as RootDepthUsed',
        '[AdaptiveIrrigation].Script.RootSWC as RootSWC',
        '[AdaptiveIrrigation].Script.RootDUL as RootDUL',
        '[AdaptiveIrrigation].Script.RootLL as RootLL',
        '[AdaptiveIrrigation].Script.RootPAWC as RootPAWC',
        '[AdaptiveIrrigation].Script.RootDeficit as RootDeficit',
        '[AdaptiveIrrigation].Script.FractionAvailable as FractionAvailable',
        '[AdaptiveIrrigation].Script.InDryOff as InDryOff',
        '[Weather].Rain as Rain',
        '[SoilWater].Runoff as Runoff',
        '[SoilWater].Drainage as Drainage',
        '[SoilWater].Es as SoilEvaporation',
        '[SoilWater].Eo as PotentialET',
        '[SoilWater].PotentialInfiltration as PotentialInfiltration',
    ]
    for v in needed:
        if v not in report['VariableNames']:
            report['VariableNames'].append(v)
    out_model.write_text(json.dumps(tree, indent=2))


def report_path_for(model_path: Path) -> Path:
    return model_path.with_suffix('.Report.csv')


def run_apsim(model_path: Path) -> pd.DataFrame:
    report_path = report_path_for(model_path)
    db_path = model_path.with_suffix('.db')
    if report_path.exists():
        report_path.unlink()
    if db_path.exists():
        db_path.unlink()
    cmd = [
        'docker', 'run', '--rm', '-v', f'{ROOT.resolve()}:/test-run',
        'apsiminitiative/apsimng:latest', f'/test-run/{model_path.name}', '--csv'
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    if not report_path.exists():
        raise RuntimeError(f'APSIM did not create {report_path}')
    df = pd.read_csv(report_path)
    df['Clock.Today'] = pd.to_datetime(df['Clock.Today'])
    return df


def actual_strategy_run(schedule: dict[pd.Timestamp, float], tag: str) -> pd.DataFrame:
    model = ROOT / f'Consequence_{tag}.apsimx'
    make_scheduled_model(schedule, BASE_MET, model)
    return run_apsim(model)


def days_since_last_irrigation(schedule: dict[pd.Timestamp, float], d: pd.Timestamp) -> int:
    prior = [x for x in schedule if x < d]
    return 9999 if not prior else (d - max(prior)).days


def next_trigger(df: pd.DataFrame, schedule: dict[pd.Timestamp, float], search_start: pd.Timestamp):
    x = df[(df['Clock.Today'] >= search_start) & (df['Clock.Today'] < DRYOFF)].copy()
    if 'Sugarcane.crop_status' in x:
        x = x[x['Sugarcane.crop_status'] == 'alive']
    for _, r in x.iterrows():
        d = pd.Timestamp(r['Clock.Today']).normalize()
        if days_since_last_irrigation(schedule, d) < RETURN_DAYS:
            continue
        pawc = float(r['RootPAWC'])
        deficit = float(r['RootDeficit'])
        if pawc > 0 and deficit >= DEPLETION_FRACTION * pawc:
            return d, r
    return None, None


def irrigation_amount(row) -> float:
    pawc = float(row['RootPAWC'])
    deficit = float(row['RootDeficit'])
    target_deficit = (1.0 - TARGET_AVAILABLE_FRACTION) * pawc
    return max(0.0, min(MAX_EVENT_MM, deficit - target_deficit))


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


def fetch_member_7d(issue: pd.Timestamp, member: int) -> list[float]:
    label = 'c00' if member == 0 else f'p{member:02d}'
    cache_json = CACHE / f'{issue:%Y%m%d}_{label}_7d.json'
    if cache_json.exists():
        return json.loads(cache_json.read_text())['daily_mm']
    member_cache = CACHE / issue.strftime('%Y%m%d') / label
    last_error = None
    for attempt in range(1, 4):
        try:
            member_cache.mkdir(parents=True, exist_ok=True)
            H = Herbie(issue.strftime('%Y-%m-%d'), model='gefs_reforecast', fxx=168,
                       member=member, variable_level='apcp_sfc', save_dir=str(member_cache))
            inv = H.inventory().copy()
            inv['search_this'] = inv['search_this'].astype(str)
            windows = inv['search_this'].apply(parse_window)
            inv['start_hour'] = [w[0] for w in windows]
            inv['end_hour'] = [w[1] for w in windows]
            inv['duration_hour'] = inv['end_hour'] - inv['start_hour']
            selected = inv[inv['end_hour'].notna() & (inv['end_hour'] <= 168) & (inv['duration_hour'] == 6)].copy()
            if len(selected) != 28:
                raise RuntimeError(f'{issue.date()} {label}: expected 28 six-hour windows, got {len(selected)}')
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
                    if 0 < hour <= 168:
                        values_by_hour[hour] = float(value)
            needed = [6 * i for i in range(1, 29)]
            missing = [h for h in needed if h not in values_by_hour]
            if missing:
                raise RuntimeError(f'{issue.date()} {label}: missing lead hours {missing}')
            six_hourly = [values_by_hour[h] for h in needed]
            daily = [float(sum(six_hourly[i:i+4])) for i in range(0, 28, 4)]
            cache_json.write_text(json.dumps({'issue_date': str(issue.date()), 'member': label, 'daily_mm': daily}, indent=2))
            return daily
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                shutil.rmtree(member_cache, ignore_errors=True)
                time.sleep(attempt * 2)
    raise RuntimeError(f'GEFS failed for {issue.date()} {label}: {last_error}')


def fetch_ensemble_7d(issue: pd.Timestamp) -> dict[str, list[float]]:
    result = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(fetch_member_7d, issue, m): m for m in MEMBERS}
        for fut in as_completed(futs):
            m = futs[fut]
            label = 'c00' if m == 0 else f'p{m:02d}'
            result[label] = fut.result()
    return dict(sorted(result.items()))


def forecast_met(issue: pd.Timestamp, daily_rain: list[float], horizon: int, label: str) -> Path:
    df = ACTUAL_WEATHER.copy()
    for i in range(horizon):
        d = issue + pd.Timedelta(days=i)
        if d in df.index:
            df.loc[d, 'rain'] = float(daily_rain[i])
    path = ROOT / f'ConsequenceForecast_{issue:%Y%m%d}_{label}_{horizon}d.met'
    write_met(df, path)
    return path


def lookahead(schedule: dict[pd.Timestamp, float], issue: pd.Timestamp, horizon: int,
              met_path: Path, tag: str) -> dict:
    # No irrigation is scheduled on the issue date or within the look-ahead. Only
    # previously chosen irrigation events remain in schedule.
    prior_schedule = {d: a for d, a in schedule.items() if d < issue}
    model = ROOT / f'ConsequenceLook_{tag}_{issue:%Y%m%d}_{horizon}d.apsimx'
    make_scheduled_model(prior_schedule, met_path, model)
    df = run_apsim(model)
    period = df[(df['Clock.Today'] >= issue) & (df['Clock.Today'] < issue + pd.Timedelta(days=horizon))].copy()
    if period.empty:
        raise RuntimeError(f'Empty look-ahead period {issue} {horizon}')
    sw = pd.to_numeric(period['Sugarcane.swdef_photo'], errors='coerce').dropna()
    min_sw = float(sw.min()) if not sw.empty else 1.0
    trigger = period['RootDeficit'] >= DEPLETION_FRACTION * period['RootPAWC']
    # A forecast can substitute for irrigation when the trigger is relieved after
    # the decision date without irrigation, while crop stress remains acceptable.
    later = period[period['Clock.Today'] > issue]
    relieved = bool(((later['RootDeficit'] < DEPLETION_FRACTION * later['RootPAWC'])).any()) if not later.empty else False
    end = period.iloc[-1]
    safe = bool(min_sw >= SAFE_SWDEF and relieved)
    return {
        'safe': safe,
        'min_swdef': min_sw,
        'relieved': relieved,
        'end_deficit': float(end['RootDeficit']),
        'end_pawc': float(end['RootPAWC']),
        'runoff': float(pd.to_numeric(period.get('Runoff', 0.0), errors='coerce').fillna(0).sum()) if 'Runoff' in period else math.nan,
        'drainage': float(pd.to_numeric(period.get('Drainage', 0.0), errors='coerce').fillna(0).sum()) if 'Drainage' in period else math.nan,
    }


def forecast_decision(schedule, issue, horizon, forecast_cache_records):
    ensemble = fetch_ensemble_7d(issue)
    member_results = []
    for label, rain7 in ensemble.items():
        met = forecast_met(issue, rain7, horizon, label)
        res = lookahead(schedule, issue, horizon, met, f'gefs_{label}')
        res.update({'member': label, 'forecast_rain_mm': float(sum(rain7[:horizon]))})
        member_results.append(res)
    p_safe = float(np.mean([r['safe'] for r in member_results]))
    forecast_cache_records.extend([
        {'issue_date': issue, 'horizon_days': horizon, **r} for r in member_results
    ])
    return p_safe >= SAFE_PROB, p_safe, member_results


def perfect_decision(schedule, issue, horizon):
    res = lookahead(schedule, issue, horizon, BASE_MET, 'perfect')
    rain = float(ACTUAL_WEATHER.loc[issue:issue + pd.Timedelta(days=horizon-1), 'rain'].sum())
    return res['safe'], 1.0 if res['safe'] else 0.0, [{**res, 'member': 'perfect', 'forecast_rain_mm': rain}]


def run_strategy(name: str, mode: str, horizon: int | None):
    schedule: dict[pd.Timestamp, float] = {}
    search = START
    decisions = []
    member_records = []
    iteration = 0
    while search < DRYOFF:
        iteration += 1
        if iteration > 80:
            raise RuntimeError(f'{name}: too many decision iterations')
        current = actual_strategy_run(schedule, f'{name}_state')
        d, row = next_trigger(current, schedule, search)
        if d is None:
            break
        amount_if_irrigate = irrigation_amount(row)
        if mode == 'baseline':
            wait, p_safe, details = False, 0.0, []
        elif mode == 'perfect':
            wait, p_safe, details = perfect_decision(schedule, d, int(horizon))
        elif mode == 'gefs':
            wait, p_safe, details = forecast_decision(schedule, d, int(horizon), member_records)
        else:
            raise ValueError(mode)

        decisions.append({
            'strategy': name,
            'date': d,
            'mode': mode,
            'horizon_days': horizon or 0,
            'root_deficit_mm': float(row['RootDeficit']),
            'root_pawc_mm': float(row['RootPAWC']),
            'fraction_available': float(row['FractionAvailable']),
            'swdef_photo': float(row['Sugarcane.swdef_photo']),
            'safe_probability': p_safe,
            'decision': 'WAIT' if wait else 'IRRIGATE',
            'irrigation_amount_mm': 0.0 if wait else amount_if_irrigate,
            'days_since_irrigation': days_since_last_irrigation(schedule, d),
        })
        print(name, d.date(), 'WAIT' if wait else f'IRRIGATE {amount_if_irrigate:.1f} mm',
              f'P(safe)={p_safe:.2f}', flush=True)
        if not wait:
            schedule[d] = amount_if_irrigate
        search = d + pd.Timedelta(days=1)

    final = actual_strategy_run(schedule, f'{name}_final')
    pd.DataFrame(decisions).to_csv(ROOT / f'consequence_{name}_decisions.csv', index=False)
    if member_records:
        pd.DataFrame(member_records).to_csv(ROOT / f'consequence_{name}_member_lookahead.csv', index=False)
    # Preserve the final report with a stable strategy-specific filename.
    final.to_csv(ROOT / f'consequence_{name}_daily.csv', index=False)
    return schedule, final, decisions


def sumcol(df, col):
    if col not in df:
        return math.nan
    return float(pd.to_numeric(df[col], errors='coerce').fillna(0).sum())


def strategy_summary(name, schedule, df, decisions):
    end = df[df['Clock.Today'] <= FINISH].iloc[-1]
    rain = sumcol(df, 'Rain')
    irrigation = float(sum(schedule.values()))
    runoff = sumcol(df, 'Runoff')
    drainage = sumcol(df, 'Drainage')
    soil_evap = sumcol(df, 'SoilEvaporation')
    transp = sumcol(df, 'Sugarcane.ep')
    sw = pd.to_numeric(df['Sugarcane.swdef_photo'], errors='coerce')
    return {
        'strategy': name,
        'irrigation_events': len(schedule),
        'total_irrigation_mm': irrigation,
        'rain_mm': rain,
        'runoff_mm': runoff,
        'drainage_mm': drainage,
        'soil_evaporation_mm': soil_evap,
        'crop_transpiration_mm': transp,
        'min_swdef_photo': float(sw.min()),
        'stress_days_swdef_lt_0_9': int((sw < 0.9).sum()),
        'stress_days_swdef_lt_0_7': int((sw < 0.7).sum()),
        'end_biomass': float(end['Sugarcane.biomass']),
        'end_cane_wt': float(end['Sugarcane.cane_wt']),
        'end_sucrose_wt': float(end['Sugarcane.sucrose_wt']),
        'wait_decisions': sum(1 for d in decisions if d['decision'] == 'WAIT'),
        'last_irrigation_date': max(schedule).date().isoformat() if schedule else '',
    }


strategies = [
    ('baseline_adaptive', 'baseline', None),
    ('perfect_3d', 'perfect', 3),
    ('perfect_7d', 'perfect', 7),
    ('gefs_3d', 'gefs', 3),
    ('gefs_7d', 'gefs', 7),
]

summaries = []
all_schedules = {}
for name, mode, horizon in strategies:
    print('\n===', name, '===', flush=True)
    schedule, df, decisions = run_strategy(name, mode, horizon)
    all_schedules[name] = {d.date().isoformat(): a for d, a in sorted(schedule.items())}
    summaries.append(strategy_summary(name, schedule, df, decisions))

summary = pd.DataFrame(summaries)
base = summary.loc[summary['strategy'] == 'baseline_adaptive'].iloc[0]
for metric in ['total_irrigation_mm', 'runoff_mm', 'drainage_mm', 'soil_evaporation_mm', 'crop_transpiration_mm',
               'end_biomass', 'end_cane_wt', 'end_sucrose_wt']:
    summary[f'{metric}_change_vs_baseline'] = summary[metric] - float(base[metric])
summary['irrigation_saved_mm'] = float(base['total_irrigation_mm']) - summary['total_irrigation_mm']
summary['irrigation_saved_pct'] = np.where(float(base['total_irrigation_mm']) > 0,
                                           100.0 * summary['irrigation_saved_mm'] / float(base['total_irrigation_mm']), 0.0)
summary.to_csv(ROOT / 'walkamin_consequence_strategy_summary.csv', index=False)
(ROOT / 'walkamin_consequence_schedules.json').write_text(json.dumps(all_schedules, indent=2))
(ROOT / 'walkamin_consequence_method.json').write_text(json.dumps({
    'site': 'Walkamin Tablelands',
    'season': '2008-04-01 to 2009-06-24',
    'soil': 'Krasnozem/Ferrosol proxy from existing Walkamin POC',
    'trigger': f'root-zone deficit >= {DEPLETION_FRACTION:.2f} x PAWC',
    'irrigation_amount': f'recalculate daily toward {TARGET_AVAILABLE_FRACTION:.2f} fraction available; cap {MAX_EVENT_MM} mm',
    'return_interval_days': RETURN_DAYS,
    'safe_member_definition': f'min swdef_photo >= {SAFE_SWDEF} AND irrigation trigger relieved during horizon without irrigation',
    'ensemble_safe_probability_threshold': SAFE_PROB,
    'forecast_horizons_days': [3, 7],
    'GEFS_members': ['c00','p01','p02','p03','p04'],
    'forecast_forcing': 'GEFS rainfall only; observed NASA POWER radiation/temperature retained in look-ahead',
    'timezone_note': 'GEFS 00 UTC forecast days mapped directly to APSIM decision dates in this POC; local 9am alignment still needs refinement.',
}, indent=2))

print('\n=== CONSEQUENCE-BASED STRATEGY SUMMARY ===')
print(summary.to_string(index=False))
