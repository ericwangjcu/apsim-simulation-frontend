from pathlib import Path
import json

ROOT = Path('phase3/walkamin')
TARGET = ROOT / 'Walkamin_2008_2009_baseline.apsimx'

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

tree = json.loads(TARGET.read_text())
report = find_by_name(tree, 'Report')
extra = [
    '[SoilWater].Runoff as Runoff',
    '[SoilWater].Drainage as Drainage',
    '[SoilWater].Es as SoilEvaporation',
    '[SoilWater].Eo as PotentialET',
    '[SoilWater].PotentialInfiltration as PotentialInfiltration',
]
for variable in extra:
    if variable not in report['VariableNames']:
        report['VariableNames'].append(variable)
TARGET.write_text(json.dumps(tree, indent=2))
print('Added water-balance outputs:', ', '.join(extra))
