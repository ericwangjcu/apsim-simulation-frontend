from __future__ import annotations

import copy
import json
from io import StringIO
from pathlib import Path

import pandas as pd

ROOT = Path('phase3/run')
BASE = json.loads((ROOT / 'Sugarcane_phase3_baseline.apsimx').read_text())
FORECAST = pd.read_csv(ROOT / 'gefs_season_decision_forecasts.csv')
FORECAST['issue_date'] = pd.to_datetime(FORECAST['issue_date'])
START = pd.Timestamp('2000-07-01')
END = pd.Timestamp('2000-09-07')
PROB_THRESHOLD = 0.60
RAIN_THRESHOLD_MM = 20.0
MAX_HOLD_DAYS = 2


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


def date_dict_lines(df, value_col):
    lines = []
    for _, r in df.sort_values('issue_date').iterrows():
        d = pd.Timestamp(r['issue_date'])
        lines.append(f'            {{ new DateTime({d.year}, {d.month}, {d.day}), {float(r[value_col]):.8f} }},')
    return '\n'.join(lines)


def manager_code(strategy: str, values: pd.DataFrame) -> str:
    if strategy == 'forecast':
        prob_lines = date_dict_lines(values, 'prob_ge_20mm')
        mean_lines = date_dict_lines(values, 'ensemble_mean_mm')
        decision_block = f'''
            ForecastProb = ForecastProbByDate.ContainsKey(Clock.Today.Date) ? ForecastProbByDate[Clock.Today.Date] : -1.0;
            ForecastMean = ForecastMeanByDate.ContainsKey(Clock.Today.Date) ? ForecastMeanByDate[Clock.Today.Date] : -1.0;
            bool forecastSaysWait = ForecastProb >= {PROB_THRESHOLD:.2f};
            bool mayHold = forecastSaysWait && HoldDays < {MAX_HOLD_DAYS};
            if (mayHold)
            {{
                HoldToday = 1.0;
                DecisionCode = 1.0;
                HoldDays += 1;
            }}
            else
            {{
                ApplyIrrigation();
                HoldDays = 0;
            }}'''
        extra_fields = f'''
        public double ForecastProb {{ get; set; }}
        public double ForecastMean {{ get; set; }}
        public double HoldToday {{ get; set; }}
        public double DecisionCode {{ get; set; }}
        private int HoldDays = 0;
        private readonly Dictionary<DateTime, double> ForecastProbByDate = new Dictionary<DateTime, double>
        {{
{prob_lines}
        }};
        private readonly Dictionary<DateTime, double> ForecastMeanByDate = new Dictionary<DateTime, double>
        {{
{mean_lines}
        }};'''
        reset_fields = 'ForecastProb = -1.0; ForecastMean = -1.0; HoldToday = 0.0; DecisionCode = 0.0;'
    elif strategy == 'perfect':
        obs_lines = date_dict_lines(values, 'observed_72h_mm')
        decision_block = f'''
            FutureObservedRain = FutureObservedRainByDate.ContainsKey(Clock.Today.Date) ? FutureObservedRainByDate[Clock.Today.Date] : 0.0;
            bool perfectSaysWait = FutureObservedRain >= {RAIN_THRESHOLD_MM:.1f};
            bool mayHold = perfectSaysWait && HoldDays < {MAX_HOLD_DAYS};
            if (mayHold)
            {{
                HoldToday = 1.0;
                DecisionCode = 1.0;
                HoldDays += 1;
            }}
            else
            {{
                ApplyIrrigation();
                HoldDays = 0;
            }}'''
        extra_fields = f'''
        public double FutureObservedRain {{ get; set; }}
        public double HoldToday {{ get; set; }}
        public double DecisionCode {{ get; set; }}
        private int HoldDays = 0;
        private readonly Dictionary<DateTime, double> FutureObservedRainByDate = new Dictionary<DateTime, double>
        {{
{obs_lines}
        }};'''
        reset_fields = 'FutureObservedRain = 0.0; HoldToday = 0.0; DecisionCode = 0.0;'
    else:
        raise ValueError(strategy)

    return f'''using Models.Interfaces;
using Models.Soils;
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

        public double triggerDeficit {{ get; set; }}
        public double targetDeficit {{ get; set; }}
        public double returndays {{ get; set; }}
        public double maximumAmount {{ get; set; }}
        public double depthPAWC {{ get; set; }}

        public double TopSWC {{ get; set; }}
        public double TopSWdeficit {{ get; set; }}
        public double AppliedToday {{ get; set; }}
        public double TotalIrrigation {{ get; set; }}
        public double DaysSinceIrrigation {{ get; set; }}
{extra_fields}
        private double TopDUL;
        private double TopLL;
        private int nLayers;

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {{
            double depthFromSurface = 0.0;
            nLayers = soilPhysical.Thickness.Length;
            TopDUL = 0.0;
            TopLL = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {{
                double fracLayer = Math.Min(1.0, (depthPAWC - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0) break;
                TopLL += soilPhysical.LL15mm[layer] * fracLayer;
                TopDUL += soilPhysical.DULmm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= depthPAWC) break;
            }}
            DaysSinceIrrigation = 999.0;
            TotalIrrigation = 0.0;
        }}

        private void ApplyIrrigation()
        {{
            double amount = TopDUL * targetDeficit / 100.0 - TopSWC;
            amount = Math.Max(0.0, Math.Min(amount, maximumAmount));
            if (amount > 0.0)
            {{
                Irrigation.Apply(amount);
                AppliedToday = amount;
                TotalIrrigation += amount;
                DaysSinceIrrigation = 0.0;
                DecisionCode = 2.0;
            }}
        }}

        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {{
            AppliedToday = 0.0;
            DaysSinceIrrigation += 1.0;
            {reset_fields}

            double depthFromSurface = 0.0;
            TopSWC = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {{
                double fracLayer = Math.Min(1.0, (depthPAWC - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0) break;
                TopSWC += waterBalance.SWmm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= depthPAWC) break;
            }}
            TopSWdeficit = TopSWC - TopDUL;
            bool inDecisionSeason = Clock.Today >= new DateTime(2000, 7, 1) && Clock.Today <= new DateTime(2000, 9, 7);
            bool soilIsDry = Math.Max(0.0, -TopSWdeficit) >= (TopDUL - TopLL) * (100.0 - triggerDeficit) / 100.0;
            bool irrigatorAvailable = DaysSinceIrrigation >= returndays;

            if (inDecisionSeason && soilIsDry && irrigatorAvailable)
            {{
{decision_block}
            }}
            else if (!soilIsDry)
            {{
                HoldDays = 0;
            }}
        }}
    }}
}}'''


def read_met(path: Path):
    lines = path.read_text().splitlines()
    header_i = next(i for i, line in enumerate(lines) if line.strip().startswith('year') and 'rain' in line)
    cols = lines[header_i].split()
    data = pd.read_csv(StringIO('\n'.join(lines[header_i + 2:])), sep=r'\s+', names=cols)
    data['date'] = pd.to_datetime(data['year'].astype(int).astype(str), format='%Y') + pd.to_timedelta(data['day'].astype(int) - 1, unit='D')
    return data


met = read_met(ROOT / 'AU_Ingham.met')
met = met.set_index('date')
obs_rows = []
for d in pd.date_range(START, END, freq='D'):
    # Approximate next 72 local hours with current day plus following two SILO days.
    dates = [d + pd.Timedelta(days=i) for i in range(3)]
    total = float(sum(float(met.loc[x, 'rain']) for x in dates if x in met.index))
    obs_rows.append({'issue_date': d, 'observed_72h_mm': total})
obs = pd.DataFrame(obs_rows)
obs.to_csv(ROOT / 'observed_72h_lookahead.csv', index=False)


def make_scenario(strategy: str, manager_name: str, values: pd.DataFrame, filename: str):
    tree = copy.deepcopy(BASE)
    manager = find_by_name(tree, 'BaselineIrrigation')
    manager['Name'] = manager_name
    manager['Code'] = manager_code(strategy, values)
    report = find_by_name(tree, 'Report')
    report['VariableNames'] = [
        v.replace('[BaselineIrrigation].Script.', f'[{manager_name}].Script.')
        for v in report['VariableNames']
    ]
    if strategy == 'forecast':
        report['VariableNames'] += [
            f'[{manager_name}].Script.ForecastProb as ForecastProb',
            f'[{manager_name}].Script.ForecastMean as ForecastMean',
            f'[{manager_name}].Script.HoldToday as HoldToday',
            f'[{manager_name}].Script.DecisionCode as DecisionCode',
        ]
    else:
        report['VariableNames'] += [
            f'[{manager_name}].Script.FutureObservedRain as FutureObservedRain',
            f'[{manager_name}].Script.HoldToday as HoldToday',
            f'[{manager_name}].Script.DecisionCode as DecisionCode',
        ]
    (ROOT / filename).write_text(json.dumps(tree, indent=2))
    print('Wrote', filename)

make_scenario('forecast', 'ForecastIrrigation', FORECAST, 'Sugarcane_phase3_forecast.apsimx')
make_scenario('perfect', 'PerfectIrrigation', obs, 'Sugarcane_phase3_perfect.apsimx')

manifest = {
    'decision_period': [START.strftime('%Y-%m-%d'), END.strftime('%Y-%m-%d')],
    'baseline_rule': {'depthPAWC_mm': 300, 'triggerDeficit': 75, 'targetDeficit': 95, 'maximumAmount_mm': 30, 'minimum_return_days': 3},
    'forecast_rule': {
        'rain_event_threshold_mm_72h': RAIN_THRESHOLD_MM,
        'probability_threshold': PROB_THRESHOLD,
        'ensemble_members': 5,
        'interpretation': 'defer when at least 3 of 5 members forecast >=20 mm in next 72h',
        'maximum_consecutive_hold_days': MAX_HOLD_DAYS,
        'missing_forecast_action': 'irrigate if baseline soil trigger is met',
    },
    'perfect_information_rule': {
        'hold_if_observed_next_72h_rain_mm_gte': RAIN_THRESHOLD_MM,
        'maximum_consecutive_hold_days': MAX_HOLD_DAYS,
    },
    'important_note': 'Thresholds are proof-of-concept parameters, not final CLOVER agronomic recommendations. Forecast/local-day alignment is approximate.',
}
(ROOT / 'phase3_decision_manifest.json').write_text(json.dumps(manifest, indent=2))
