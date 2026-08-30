from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path('phase3/walkamin')
ROOT.mkdir(parents=True, exist_ok=True)
POWER = json.loads((ROOT / 'nasa_power_walkamin_2000_2010.json').read_text())
BASE = json.loads((ROOT / 'Sugarcane.apsimx').read_text())

LAT = -17.13
LON = 145.43
SEASONS = range(2000, 2010)
PAWC_PER_M = 110.0  # mm/m Tablelands krasnozem proxy, ~1.10 cm/10 cm available water
DUL_MINUS_LL = PAWC_PER_M / 1000.0
MAX_ROOT_DEPTH = 1800.0
DEPLETION_FRACTION = 0.50
MAX_EVENT_MM = 50.0
RETURN_DAYS = 10.0


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


def find_type(tree, type_fragment):
    for node in walk(tree):
        if type_fragment in str(node.get('$type', '')):
            return node
    raise KeyError(type_fragment)


def power_dataframe():
    p = POWER['properties']['parameter']
    required = ['ALLSKY_SFC_SW_DWN', 'T2M_MAX', 'T2M_MIN', 'PRECTOTCORR']
    dates = sorted(set(p['PRECTOTCORR']))
    rows = []
    for ds in dates:
        vals = {k: float(p[k][ds]) for k in required}
        if any(v <= -900 for v in vals.values()):
            continue
        d = pd.to_datetime(ds, format='%Y%m%d')
        rows.append({
            'date': d,
            'year': d.year,
            'day': d.dayofyear,
            'radn': vals['ALLSKY_SFC_SW_DWN'],
            'maxt': vals['T2M_MAX'],
            'mint': vals['T2M_MIN'],
            'rain': max(0.0, vals['PRECTOTCORR']),
        })
    return pd.DataFrame(rows).set_index('date')


POWER_DF = power_dataframe()


def write_met(start: pd.Timestamp, finish: pd.Timestamp, path: Path):
    df = POWER_DF.loc[start:finish].copy().reset_index()
    expected = (finish - start).days + 1
    if len(df) != expected:
        raise RuntimeError(f'{start.date()} season expected {expected} weather days, got {len(df)}')
    tmean = (df['maxt'] + df['mint']) / 2
    monthly = pd.DataFrame({'date': df['date'], 'tmean': tmean}).set_index('date')['tmean'].resample('MS').mean()
    tav = float(tmean.mean())
    amp = float((monthly.max() - monthly.min()) / 2)
    lines = [
        '[weather.met.weather]',
        '! NASA POWER daily weather at Walkamin, Atherton Tablelands; proof-of-concept',
        f'latitude = {LAT:.2f} (DECIMAL DEGREES)',
        f'longitude = {LON:.2f} (DECIMAL DEGREES)',
        f'tav = {tav:.2f} (oC)',
        f'amp = {amp:.2f} (oC)',
        'year day radn maxt mint rain',
        ' ()  () (MJ/m^2) (oC) (oC) (mm)',
    ]
    for r in df.itertuples(index=False):
        lines.append(f'{r.year:4d} {r.day:3d} {r.radn:6.2f} {r.maxt:6.2f} {r.mint:6.2f} {r.rain:7.2f}')
    path.write_text('\n'.join(lines) + '\n')
    return float(df['rain'].sum())


def manager_code(start: pd.Timestamp, dryoff: pd.Timestamp):
    return f'''using Models.Interfaces;
using Models.Soils;
using Models.PMF;
using Models.Core;
using System;

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
        private int nLayers;

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {{
            nLayers = soilPhysical.Thickness.Length;
            DaysSinceIrrigation = 999.0;
            TotalIrrigation = 0.0;
        }}

        private void CalculateRootZone()
        {{
            RootDepthUsed = Math.Max(minimumRootDepth, Math.Min(maximumRootDepth, Sugarcane.root_depth));
            RootSWC = 0.0; RootDUL = 0.0; RootLL = 0.0;
            double depthFromSurface = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {{
                double fracLayer = Math.Min(1.0, (RootDepthUsed - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0) break;
                RootSWC += waterBalance.SWmm[layer] * fracLayer;
                RootDUL += soilPhysical.DULmm[layer] * fracLayer;
                RootLL += soilPhysical.LL15mm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= RootDepthUsed) break;
            }}
            RootPAWC = Math.Max(0.0, RootDUL - RootLL);
            RootDeficit = Math.Max(0.0, RootDUL - RootSWC);
            FractionAvailable = RootPAWC > 0 ? Math.Max(0.0, Math.Min(1.0, (RootSWC - RootLL) / RootPAWC)) : 0.0;
        }}

        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {{
            AppliedToday = 0.0;
            DaysSinceIrrigation += 1.0;
            InDryOff = Clock.Today >= new DateTime({dryoff.year}, {dryoff.month}, {dryoff.day}) ? 1.0 : 0.0;
            if (Sugarcane.crop_status != "alive") return;
            CalculateRootZone();
            bool irrigationPeriod = Clock.Today >= new DateTime({start.year}, {start.month}, {start.day})
                                  && Clock.Today < new DateTime({dryoff.year}, {dryoff.month}, {dryoff.day});
            bool soilIsDry = RootDeficit >= RootPAWC * depletionFraction;
            bool irrigatorAvailable = DaysSinceIrrigation >= returnDays;
            if (irrigationPeriod && soilIsDry && irrigatorAvailable)
            {{
                double amount = Math.Max(0.0, Math.Min(maximumAmount, RootDeficit));
                if (amount > 0.0)
                {{
                    Irrigation.Apply(amount);
                    AppliedToday = amount;
                    TotalIrrigation += amount;
                    DaysSinceIrrigation = 0.0;
                }}
            }}
        }}
    }}
}}'''


def make_season(year: int):
    start = pd.Timestamp(year=year, month=4, day=1)
    finish = pd.Timestamp(year=year + 1, month=6, day=24)
    dryoff = pd.Timestamp(year=year + 1, month=5, day=14)
    met = ROOT / f'Walkamin_{year}_{year+1}.met'
    rain_total = write_met(start, pd.Timestamp(year=year + 1, month=6, day=30), met)

    tree = copy.deepcopy(BASE)
    clock = find_by_name(tree, 'clock')
    weather = find_by_name(tree, 'Weather')
    field = find_by_name(tree, 'Field')
    report = find_by_name(tree, 'Report')
    soil = find_type(tree, 'Models.Soils.Soil, Models')
    physical = find_type(soil, 'Models.Soils.Physical, Models')

    clock['Start'] = start.strftime('%Y-%m-%dT00:00:00')
    clock['End'] = finish.strftime('%Y-%m-%dT00:00:00')
    weather['FileName'] = f'/test-run/{met.name}'

    sugar_manager = find_by_name(tree, 'SUGAR management')
    params = {x['Key']: x for x in sugar_manager['Parameters']}
    params['planting_day']['Value'] = '1-apr'
    params['plantlen']['Value'] = '450'

    # Tablelands soil proxy: retain the official APSIM sugarcane profile structure,
    # but impose a krasnozem/Ferrosol available-water contrast of about 110 mm/m.
    # This is explicitly a proxy pending a site-specific APSoil characterisation.
    soil['ASCOrder'] = 'Ferrosol'
    soil['ASCSubOrder'] = 'Red'
    soil['SoilType'] = 'Krasnozem/Ferrosol proxy'
    soil['LocalName'] = 'Walkamin Tablelands proxy'
    soil['Site'] = 'Walkamin'
    soil['NearestTown'] = 'Walkamin'
    soil['Region'] = 'Atherton Tablelands'
    soil['State'] = 'Queensland'
    soil['Country'] = 'Australia'
    soil['Latitude'] = LAT
    soil['Longitude'] = LON
    soil['Comments'] = 'Proof-of-concept Tablelands proxy. DUL-LL15 targeted near 0.110 mm/mm from published krasnozem available-water value; not a site-calibrated APSoil.'

    ll = [float(x) for x in physical['LL15']]
    sat = [float(x) for x in physical['SAT']]
    new_dul = [min(s - 0.02, l + DUL_MINUS_LL) for l, s in zip(ll, sat)]
    physical['DUL'] = new_dul
    # Keep crop lower limit consistent with the revised physical LL15 where present.
    for child in physical.get('Children', []):
        if 'SoilCrop' in str(child.get('$type', '')) and len(child.get('LL', [])) == len(ll):
            child['LL'] = list(ll)

    irrigation = {
        '$type': 'Models.Irrigation, Models', 'Name': 'Irrigation', 'Children': [],
        'Enabled': True, 'ReadOnly': False
    }
    manager = {
        '$type': 'Models.Manager, Models',
        'Code': manager_code(start, dryoff),
        'Parameters': [
            {'Key': 'depletionFraction', 'Value': str(DEPLETION_FRACTION)},
            {'Key': 'minimumRootDepth', 'Value': '300'},
            {'Key': 'maximumRootDepth', 'Value': str(MAX_ROOT_DEPTH)},
            {'Key': 'maximumAmount', 'Value': str(MAX_EVENT_MM)},
            {'Key': 'returnDays', 'Value': str(RETURN_DAYS)},
        ],
        'Name': 'WalkaminIrrigation', 'IncludeInDocumentation': False,
        'Enabled': True, 'ReadOnly': False,
    }
    field['Children'] = [c for c in field['Children'] if c.get('Name') not in {'Irrigation','WalkaminIrrigation','FullSeasonIrrigation'}]
    field['Children'].append(irrigation)
    field['Children'].append(manager)

    extra = [
        '[WalkaminIrrigation].Script.DaysSinceIrrigation as DaysSinceIrrigation',
        '[WalkaminIrrigation].Script.AppliedToday as AppliedToday',
        '[WalkaminIrrigation].Script.TotalIrrigation as TotalIrrigation',
        '[WalkaminIrrigation].Script.RootDepthUsed as RootDepthUsed',
        '[WalkaminIrrigation].Script.RootSWC as RootSWC',
        '[WalkaminIrrigation].Script.RootDUL as RootDUL',
        '[WalkaminIrrigation].Script.RootLL as RootLL',
        '[WalkaminIrrigation].Script.RootPAWC as RootPAWC',
        '[WalkaminIrrigation].Script.RootDeficit as RootDeficit',
        '[WalkaminIrrigation].Script.FractionAvailable as FractionAvailable',
        '[WalkaminIrrigation].Script.InDryOff as InDryOff',
        '[Weather].Rain as Rain',
    ]
    for v in extra:
        if v not in report['VariableNames']:
            report['VariableNames'].append(v)

    out = ROOT / f'Walkamin_{year}_{year+1}_baseline.apsimx'
    out.write_text(json.dumps(tree, indent=2))
    return {'season_start_year': year, 'apsimx': out.name, 'met': met.name, 'weather_total_mm': rain_total}


manifest = [make_season(y) for y in SEASONS]
(ROOT / 'walkamin_screen_manifest.json').write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
