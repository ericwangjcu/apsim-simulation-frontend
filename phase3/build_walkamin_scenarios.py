from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd

ROOT = Path('phase3/walkamin')
BASE = json.loads((ROOT / 'Walkamin_2008_2009_baseline.apsimx').read_text())
FORECAST = pd.read_csv(ROOT / 'walkamin_2008_gefs_forecasts.csv')
FORECAST['issue_date'] = pd.to_datetime(FORECAST['issue_date'])
PROB_THRESHOLD = 0.60
RAIN_THRESHOLD_MM = 20.0
MAX_HOLD_DAYS = 2
START = pd.Timestamp('2008-04-01')
DRYOFF = pd.Timestamp('2009-05-14')


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


base_report = pd.read_csv(ROOT / 'Walkamin_2008_2009_baseline.Report.csv')
base_report['Clock.Today'] = pd.to_datetime(base_report['Clock.Today'])
rain = base_report.set_index('Clock.Today')['Rain']
obs_rows = []
for d in pd.date_range(START, DRYOFF - pd.Timedelta(days=1), freq='D'):
    total = sum(float(rain.loc[d + pd.Timedelta(days=i)]) for i in range(3) if d + pd.Timedelta(days=i) in rain.index)
    obs_rows.append({'issue_date': d, 'observed_72h_mm': total})
OBS = pd.DataFrame(obs_rows)
OBS.to_csv(ROOT / 'walkamin_2008_observed_72h.csv', index=False)


def manager_code(strategy: str, values: pd.DataFrame) -> str:
    if strategy == 'forecast':
        prob_lines = date_dict_lines(values, 'prob_ge_20mm')
        mean_lines = date_dict_lines(values, 'ensemble_mean_mm')
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
        reset = 'ForecastProb = -1.0; ForecastMean = -1.0; HoldToday = 0.0; DecisionCode = 0.0;'
        decision = f'''
                ForecastProb = ForecastProbByDate.ContainsKey(Clock.Today.Date) ? ForecastProbByDate[Clock.Today.Date] : -1.0;
                ForecastMean = ForecastMeanByDate.ContainsKey(Clock.Today.Date) ? ForecastMeanByDate[Clock.Today.Date] : -1.0;
                bool saysWait = ForecastProb >= {PROB_THRESHOLD:.2f};
                if (saysWait && HoldDays < {MAX_HOLD_DAYS})
                {{
                    HoldToday = 1.0; DecisionCode = 1.0; HoldDays += 1;
                }}
                else
                {{
                    ApplyIrrigation(); HoldDays = 0;
                }}'''
    else:
        obs_lines = date_dict_lines(values, 'observed_72h_mm')
        extra_fields = f'''
        public double FutureObservedRain {{ get; set; }}
        public double HoldToday {{ get; set; }}
        public double DecisionCode {{ get; set; }}
        private int HoldDays = 0;
        private readonly Dictionary<DateTime, double> FutureObservedRainByDate = new Dictionary<DateTime, double>
        {{
{obs_lines}
        }};'''
        reset = 'FutureObservedRain = 0.0; HoldToday = 0.0; DecisionCode = 0.0;'
        decision = f'''
                FutureObservedRain = FutureObservedRainByDate.ContainsKey(Clock.Today.Date) ? FutureObservedRainByDate[Clock.Today.Date] : 0.0;
                bool saysWait = FutureObservedRain >= {RAIN_THRESHOLD_MM:.1f};
                if (saysWait && HoldDays < {MAX_HOLD_DAYS})
                {{
                    HoldToday = 1.0; DecisionCode = 1.0; HoldDays += 1;
                }}
                else
                {{
                    ApplyIrrigation(); HoldDays = 0;
                }}'''

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
        public double depletionFraction {{ get; set; }}
        public double minimumRootDepth {{ get; set; }}
        public double maximumRootDepth {{ get; set; }}
        public double maximumAmount {{ get; set; }}
        public double returnDays {{ get; set; }}
        public double RootDepthUsed {{ get; set; }}
        public double RootSWC {{ get; set; }}
        public double RootDUL {{ get; set; }}
        public double RootLL {{ get; set; }}
        public double RootPAWC {{ get; set; }}
        public double RootDeficit {{ get; set; }}
        public double FractionAvailable {{ get; set; }}
        public double AppliedToday {{ get; set; }}
        public double TotalIrrigation {{ get; set; }}
        public double DaysSinceIrrigation {{ get; set; }}
        public double InDryOff {{ get; set; }}
{extra_fields}
        private int nLayers;

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {{
            nLayers = soilPhysical.Thickness.Length; DaysSinceIrrigation = 999.0; TotalIrrigation = 0.0;
        }}
        private void CalculateRootZone()
        {{
            RootDepthUsed = Math.Max(minimumRootDepth, Math.Min(maximumRootDepth, Sugarcane.root_depth));
            RootSWC = 0.0; RootDUL = 0.0; RootLL = 0.0; double depth = 0.0;
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
        private void ApplyIrrigation()
        {{
            double amount = Math.Max(0.0, Math.Min(maximumAmount, RootDeficit));
            if (amount > 0.0)
            {{
                Irrigation.Apply(amount); AppliedToday = amount; TotalIrrigation += amount; DaysSinceIrrigation = 0.0; DecisionCode = 2.0;
            }}
        }}
        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {{
            AppliedToday = 0.0; DaysSinceIrrigation += 1.0;
            InDryOff = Clock.Today >= new DateTime(2009, 5, 14) ? 1.0 : 0.0;
            {reset}
            if (Sugarcane.crop_status != "alive") return;
            CalculateRootZone();
            bool irrigationPeriod = Clock.Today >= new DateTime(2008, 4, 1) && Clock.Today < new DateTime(2009, 5, 14);
            bool soilIsDry = RootDeficit >= RootPAWC * depletionFraction;
            bool irrigatorAvailable = DaysSinceIrrigation >= returnDays;
            if (irrigationPeriod && soilIsDry && irrigatorAvailable)
            {{
{decision}
            }}
            else if (!soilIsDry) HoldDays = 0;
        }}
    }}
}}'''


def make(strategy, manager_name, values, filename):
    tree = copy.deepcopy(BASE)
    manager = find_by_name(tree, 'WalkaminIrrigation')
    manager['Name'] = manager_name
    manager['Code'] = manager_code(strategy, values)
    report = find_by_name(tree, 'Report')
    report['VariableNames'] = [v.replace('[WalkaminIrrigation].Script.', f'[{manager_name}].Script.') for v in report['VariableNames']]
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

make('forecast', 'WalkaminForecastIrrigation', FORECAST, 'Walkamin_2008_2009_forecast.apsimx')
make('perfect', 'WalkaminPerfectIrrigation', OBS, 'Walkamin_2008_2009_perfect.apsimx')
