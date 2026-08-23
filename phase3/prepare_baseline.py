from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path('phase3/run')
ROOT.mkdir(parents=True, exist_ok=True)

sugar = json.loads((ROOT / 'Sugarcane.apsimx').read_text())
pasture = json.loads((ROOT / 'AgPasture.apsimx').read_text())


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

# Reuse APSIM's own AutomaticIrrigation example manager and irrigation model.
auto = copy.deepcopy(find_by_name(pasture, 'AutomaticIrrigation'))
irrigation = copy.deepcopy(find_by_name(pasture, 'Irrigation'))
auto['Name'] = 'BaselineIrrigation'

# Use the standard APSIM example settings as a proof-of-concept starting rule.
settings = {
    'allowIrrigation': 'True',
    'seasonStart': '1-jul',
    'seasonEnd': '30-sep',
    'seasonsAllocation': '10000',
    'triggerDeficit': '75',
    'targetDeficit': '95',
    'returndays': '3',
    'maximumAmount': '30',
    'depthPAWC': '300',
}
for p in auto.get('Parameters', []):
    if p.get('Key') in settings:
        p['Value'] = settings[p['Key']]

# Restrict irrigation to the 2000 dry-season decision window only. This keeps
# the preceding 1990-1999 state identical to the official Sugarcane example.
code = auto.get('CodeArray')
if code is None and 'Code' in auto:
    code = auto['Code'].splitlines()
    auto.pop('Code', None)
    auto['CodeArray'] = code
for i, line in enumerate(code):
    if 'SeasonIsOpen = isBetween(Clock.Today, StartDate, EndDate);' in line:
        code[i] = line.replace(
            'SeasonIsOpen = isBetween(Clock.Today, StartDate, EndDate);',
            'SeasonIsOpen = Clock.Today.Year == 2000 && isBetween(Clock.Today, StartDate, EndDate);'
        )

# Ensure only one irrigation model/manager with these names exists.
field['Children'] = [c for c in field['Children'] if c.get('Name') not in {'Irrigation', 'BaselineIrrigation'}]
field['Children'].append(irrigation)
field['Children'].append(auto)

# Point the model at the local weather file.
weather['FileName'] = '/test-run/AU_Ingham.met'

# Add transparent daily diagnostics so irrigation dates can be reconstructed.
extra = [
    '[BaselineIrrigation].DaysSinceIrrigation',
    '[BaselineIrrigation].AmountToApply',
    '[BaselineIrrigation].SeasonAppliedAmount',
    '[BaselineIrrigation].TopSWC',
    '[BaselineIrrigation].TopSWdeficit',
]
for v in extra:
    if v not in report['VariableNames']:
        report['VariableNames'].append(v)

out = ROOT / 'Sugarcane_phase3_baseline.apsimx'
out.write_text(json.dumps(sugar, indent=2))
print(f'Wrote {out}')
print('Phase 3 proof-of-concept decision window: 1 Jul to 30 Sep 2000')
print('Irrigation settings:', settings)
