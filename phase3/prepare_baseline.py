from __future__ import annotations

import json
from pathlib import Path

ROOT = Path('phase3/run')
ROOT.mkdir(parents=True, exist_ok=True)
sugar = json.loads((ROOT / 'Sugarcane.apsimx').read_text())


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

field = find_by_name(sugar, 'Field')
report = find_by_name(sugar, 'Report')
weather = find_by_name(sugar, 'Weather')

# Keep this manager in the same legacy JSON shape used by the official
# Sugarcane example (Version 138). APSIM will upgrade the complete file when
# it is loaded, avoiding mixed-version model nodes.
manager_code = r'''using Models.Interfaces;
using Models.Soils;
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

        [Description("Deficit threshold expressed as % PAWC remaining setting used by APSIM example")]
        public double triggerDeficit { get; set; }
        [Description("Target soil water as % of DUL")]
        public double targetDeficit { get; set; }
        [Description("Minimum days between irrigation events")]
        public double returndays { get; set; }
        [Description("Maximum irrigation application (mm/day)")]
        public double maximumAmount { get; set; }
        [Description("Depth used to calculate PAWC (mm)")]
        public double depthPAWC { get; set; }

        public double TopSWC { get; set; }
        public double TopSWdeficit { get; set; }
        public double AppliedToday { get; set; }
        public double TotalIrrigation { get; set; }
        public double DaysSinceIrrigation { get; set; }

        private double TopDUL;
        private double TopLL;
        private int nLayers;

        [EventSubscribe("StartOfSimulation")]
        private void OnStartOfSimulation(object sender, EventArgs e)
        {
            double depthFromSurface = 0.0;
            nLayers = soilPhysical.Thickness.Length;
            TopDUL = 0.0;
            TopLL = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {
                double fracLayer = Math.Min(1.0, (depthPAWC - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0)
                    break;
                TopLL += soilPhysical.LL15mm[layer] * fracLayer;
                TopDUL += soilPhysical.DULmm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= depthPAWC)
                    break;
            }
            DaysSinceIrrigation = 999.0;
            TotalIrrigation = 0.0;
        }

        [EventSubscribe("StartOfDay")]
        private void OnStartOfDay(object sender, EventArgs e)
        {
            AppliedToday = 0.0;
            DaysSinceIrrigation += 1.0;

            double depthFromSurface = 0.0;
            TopSWC = 0.0;
            for (int layer = 0; layer < nLayers; layer++)
            {
                double fracLayer = Math.Min(1.0, (depthPAWC - depthFromSurface) / soilPhysical.Thickness[layer]);
                if (fracLayer <= 0.0)
                    break;
                TopSWC += waterBalance.SWmm[layer] * fracLayer;
                depthFromSurface += soilPhysical.Thickness[layer];
                if (depthFromSurface >= depthPAWC)
                    break;
            }

            TopSWdeficit = TopSWC - TopDUL;
            bool inDecisionSeason = Clock.Today >= new DateTime(2000, 7, 1) && Clock.Today <= new DateTime(2000, 9, 30);
            bool soilIsDry = Math.Max(0.0, -TopSWdeficit) >= (TopDUL - TopLL) * (100.0 - triggerDeficit) / 100.0;
            bool irrigatorAvailable = DaysSinceIrrigation >= returndays;

            if (inDecisionSeason && soilIsDry && irrigatorAvailable)
            {
                double amount = TopDUL * targetDeficit / 100.0 - TopSWC;
                amount = Math.Max(0.0, Math.Min(amount, maximumAmount));
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
        {'Key': 'triggerDeficit', 'Value': '75'},
        {'Key': 'targetDeficit', 'Value': '95'},
        {'Key': 'returndays', 'Value': '3'},
        {'Key': 'maximumAmount', 'Value': '30'},
        {'Key': 'depthPAWC', 'Value': '300'},
    ],
    'Name': 'BaselineIrrigation',
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

field['Children'] = [c for c in field['Children'] if c.get('Name') not in {'Irrigation', 'BaselineIrrigation'}]
field['Children'].append(irrigation)
field['Children'].append(manager)
weather['FileName'] = '/test-run/AU_Ingham.met'

extra = [
    '[BaselineIrrigation].DaysSinceIrrigation',
    '[BaselineIrrigation].AppliedToday',
    '[BaselineIrrigation].TotalIrrigation',
    '[BaselineIrrigation].TopSWC',
    '[BaselineIrrigation].TopSWdeficit',
]
for v in extra:
    if v not in report['VariableNames']:
        report['VariableNames'].append(v)

out = ROOT / 'Sugarcane_phase3_baseline.apsimx'
out.write_text(json.dumps(sugar, indent=2))
print(f'Wrote {out}')
print('Decision season: 1 Jul to 30 Sep 2000')
print('Rule: top 300 mm; triggerDeficit=75; target=95% DUL; max 30 mm; min return 3 days')
