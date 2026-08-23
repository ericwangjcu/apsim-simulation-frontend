from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('phase3/run')
ROOT.mkdir(parents=True, exist_ok=True)

PLANT_DATE = pd.Timestamp('2000-04-01')
PREHARVEST_END = pd.Timestamp('2001-06-24')  # official example harvests at 450 DAS on 25 Jun
DRY_OFF_START = pd.Timestamp('2001-05-14')   # 42 days before scheduled harvest
WEATHER_END = pd.Timestamp('2001-06-30')


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


def power_to_met(power_json: Path, out_met: Path):
    obj = json.loads(power_json.read_text())
    p = obj['properties']['parameter']
    required = ['ALLSKY_SFC_SW_DWN', 'T2M_MAX', 'T2M_MIN', 'PRECTOTCORR']
    for key in required:
        if key not in p:
            raise KeyError(f'Missing NASA POWER parameter {key}')

    dates = sorted(set(p['PRECTOTCORR']))
    rows = []
    for ds in dates:
        d = pd.to_datetime(ds, format='%Y%m%d')
        if not (PLANT_DATE <= d <= WEATHER_END):
            continue
        vals = {k: float(p[k][ds]) for k in required}
        if any(v <= -900 for v in vals.values()):
            raise RuntimeError(f'Missing NASA POWER data on {ds}: {vals}')
        rows.append({
            'date': d,
            'year': d.year,
            'day': d.dayofyear,
            # POWER agroclimatology daily shortwave is supplied as MJ/m2/day.
            'radn': vals['ALLSKY_SFC_SW_DWN'],
            'maxt': vals['T2M_MAX'],
            'mint': vals['T2M_MIN'],
            'rain': max(0.0, vals['PRECTOTCORR']),
        })

    df = pd.DataFrame(rows)
    expected = (WEATHER_END - PLANT_DATE).days + 1
    if len(df) != expected:
        raise RuntimeError(f'Expected {expected} weather days, got {len(df)}')

    tmean = (df['maxt'] + df['mint']) / 2.0
    monthly = pd.DataFrame({'date': df['date'], 'tmean': tmean}).set_index('date')['tmean'].resample('MS').mean()
    tav = float(tmean.mean())
    amp = float((monthly.max() - monthly.min()) / 2.0)

    lines = [
        '[weather.met.weather]',
        '! NASA POWER daily point weather for full plant-crop proof-of-concept',
        'latitude = -18.65  (DECIMAL DEGREES)',
        'longitude = 146.18  (DECIMAL DEGREES)',
        f'tav = {tav:.2f} (oC)',
        f'amp = {amp:.2f} (oC)',
        'year day radn maxt mint rain',
        ' ()  () (MJ/m^2) (oC) (oC) (mm)',
    ]
    for r in df.itertuples(index=False):
        lines.append(f'{r.year:4d} {r.day:3d} {r.radn:6.2f} {r.maxt:6.2f} {r.mint:6.2f} {r.rain:7.2f}')
    out_met.write_text('\n'.join(lines) + '\n')
    print(f'Wrote {out_met} with {len(df)} daily records')


power_to_met(ROOT / 'nasa_power_ingam_2000_2001.json', ROOT / 'Ingham_full_plant.met')

sugar = json.loads((ROOT / 'Sugarcane.apsimx').read_text())
field = find_by_name(sugar, 'Field')
report = find_by_name(sugar, 'Report')
weather = find_by_name(sugar, 'Weather')
clock = find_by_name(sugar, 'clock')

clock['Start'] = PLANT_DATE.strftime('%Y-%m-%dT00:00:00')
clock['End'] = PREHARVEST_END.strftime('%Y-%m-%dT00:00:00')
weather['FileName'] = '/test-run/Ingham_full_plant.met'

# Keep the official example's 1-Apr planting and 450-day plant crop.
sugar_manager = find_by_name(sugar, 'SUGAR management')
params = {x['Key']: x for x in sugar_manager['Parameters']}
params['planting_day']['Value'] = '1-apr'
params['plantlen']['Value'] = '450'

manager_code = r'''using Models.Interfaces;
using Models.Soils;
using Models.PMF;
using Models.Core;
using System;

namespace Models
{
    [Serializable]
    [System.Xml.Serialization.XmlInclude(typeof(Model))]
    public class Script : Model
    {
        [Link] private Clock Clock;
        [Link] private Irrigation Irrigation;
        [Link] private IPhysical soilPhysical;
        [Link] private ISoilWater waterBalance;
        [Link] private Sugarcane Sugarcane;

        [Description("Fraction of current root-zone PAWC depleted before irrigation")]
        public double depletionFraction { get; set; }
        [Description("Minimum root-zone depth used for scheduling (mm)")]
        public double minimumRootDepth { get; set; }
        [Description("Maximum root-zone depth used for scheduling (mm)")]
        public double maximumRootDepth { get; set; }
        [Description("Maximum irrigation application (mm/event)")]
        public double maximumAmount { get; set; }
        [Description("Minimum days between irrigation events")]
        public double returnDays { get; set; }

        public double RootDepthUsed { get; set; }
        public double RootSWC { get; set; }
        public double RootDUL { get; set; }
        public double RootLL { get; set; }
        public double RootPAWC { get; set; }
        public double RootDeficit { get; set; }
        public double AppliedToday { get; set; }
        public double TotalIrrigation { get; set; }
        public double DaysSinceIrrigation { get; set; }
        public double InDryOff { get; set; }

        private int nLayers;

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {
            nLayers = soilPhysical.Thickness.Length;
            DaysSinceIrrigation = 999.0;
            TotalIrrigation = 0.0;
        }

        private void CalculateRootZone()
        {
            RootDepthUsed = Math.Max(minimumRootDepth, Math.Min(maximumRootDepth, Sugarcane.root_depth));
            RootSWC = 0.0;
            RootDUL = 0.0;
            RootLL = 0.0;
            double depthFromSurface = 0.0;

            for (int layer = 0; layer < nLayers; layer++)
            {
                double fracLayer = Math.Min(1.0, (RootDepthUsed - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0)
                    break;
                RootSWC += waterBalance.SWmm[layer] * fracLayer;
                RootDUL += soilPhysical.DULmm[layer] * fracLayer;
                RootLL += soilPhysical.LL15mm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= RootDepthUsed)
                    break;
            }
            RootPAWC = Math.Max(0.0, RootDUL - RootLL);
            RootDeficit = Math.Max(0.0, RootDUL - RootSWC);
        }

        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {
            AppliedToday = 0.0;
            DaysSinceIrrigation += 1.0;
            InDryOff = Clock.Today >= new DateTime(2001, 5, 14) ? 1.0 : 0.0;

            if (Sugarcane.crop_status != "alive")
                return;

            CalculateRootZone();
            bool irrigationPeriod = Clock.Today >= new DateTime(2000, 4, 1)
                                    && Clock.Today < new DateTime(2001, 5, 14);
            bool soilIsDry = RootDeficit >= RootPAWC * depletionFraction;
            bool irrigatorAvailable = DaysSinceIrrigation >= returnDays;

            if (irrigationPeriod && soilIsDry && irrigatorAvailable)
            {
                // Refill the current effective root-zone deficit, capped at one realistic event size.
                double amount = Math.Max(0.0, Math.Min(RootDeficit, maximumAmount));
                if (amount > 0.0)
                {
                    Irrigation.Apply(amount);
                    AppliedToday = amount;
                    TotalIrrigation += amount;
                    DaysSinceIrrigation = 0.0;
                }
            }
        }
    }
}'''

manager = {
    '$type': 'Models.Manager, Models',
    'Code': manager_code,
    'Parameters': [
        {'Key': 'depletionFraction', 'Value': '0.50'},
        {'Key': 'minimumRootDepth', 'Value': '300'},
        {'Key': 'maximumRootDepth', 'Value': '1200'},
        {'Key': 'maximumAmount', 'Value': '60'},
        {'Key': 'returnDays', 'Value': '5'},
    ],
    'Name': 'FullSeasonIrrigation',
    'IncludeInDocumentation': False,
    'Enabled': True,
    'ReadOnly': False,
}

irrigation = {
    '$type': 'Models.Irrigation, Models',
    'Name': 'Irrigation',
    'Children': [],
    'Enabled': True,
    'ReadOnly': False,
}

field['Children'] = [c for c in field['Children'] if c.get('Name') not in {'Irrigation', 'BaselineIrrigation', 'FullSeasonIrrigation'}]
field['Children'].append(irrigation)
field['Children'].append(manager)

extra = [
    '[FullSeasonIrrigation].Script.DaysSinceIrrigation as DaysSinceIrrigation',
    '[FullSeasonIrrigation].Script.AppliedToday as AppliedToday',
    '[FullSeasonIrrigation].Script.TotalIrrigation as TotalIrrigation',
    '[FullSeasonIrrigation].Script.RootDepthUsed as RootDepthUsed',
    '[FullSeasonIrrigation].Script.RootSWC as RootSWC',
    '[FullSeasonIrrigation].Script.RootDUL as RootDUL',
    '[FullSeasonIrrigation].Script.RootLL as RootLL',
    '[FullSeasonIrrigation].Script.RootPAWC as RootPAWC',
    '[FullSeasonIrrigation].Script.RootDeficit as RootDeficit',
    '[FullSeasonIrrigation].Script.InDryOff as InDryOff',
]
for v in extra:
    if v not in report['VariableNames']:
        report['VariableNames'].append(v)

out = ROOT / 'Sugarcane_full_plant_baseline.apsimx'
out.write_text(json.dumps(sugar, indent=2))

meta = {
    'site': 'Ingham',
    'planting_date': str(PLANT_DATE.date()),
    'analysis_end_preharvest': str(PREHARVEST_END.date()),
    'scheduled_harvest': '2001-06-25 (450 days after sowing in official example)',
    'dry_off_start': str(DRY_OFF_START.date()),
    'weather_source': 'NASA POWER point daily data for -18.65, 146.18',
    'irrigation_rule': {
        'root_zone': 'dynamic crop root depth, constrained to 300-1200 mm',
        'trigger': '50% depletion of current root-zone PAWC',
        'application': 'refill root-zone deficit, capped at 60 mm/event',
        'minimum_return_days': 5,
        'dry_off': 'no irrigation from 14 May 2001 to harvest',
    },
    'note': 'Prototype baseline for validating timing/frequency. Parameters are not final CLOVER agronomic recommendations.',
}
(ROOT / 'full_plant_baseline_metadata.json').write_text(json.dumps(meta, indent=2))

print(f'Wrote {out}')
print('Plant crop: 1 Apr 2000 -> 24 Jun 2001 pre-harvest endpoint')
print('Dry-off: no irrigation from 14 May 2001')
print('Rule: dynamic root zone 300-1200 mm; trigger at 50% PAWC depletion; refill deficit up to 60 mm; min return 5 d')
