from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path('phase3/walkamin')
manifest = json.loads((ROOT / 'walkamin_screen_manifest.json').read_text())
rows = []
all_events = []

for item in manifest:
    year = int(item['season_start_year'])
    csv_path = ROOT / item['apsimx'].replace('.apsimx', '.Report.csv')
    df = pd.read_csv(csv_path)
    df['Clock.Today'] = pd.to_datetime(df['Clock.Today'])
    events = df[df['AppliedToday'] > 0].copy()
    events['season_start_year'] = year
    events['interval_days'] = events['Clock.Today'].diff().dt.days

    rain_by_date = df.set_index('Clock.Today')['Rain'] if 'Rain' in df.columns else pd.Series(dtype=float)
    future_rain = []
    for d in events['Clock.Today']:
        total = 0.0
        for offset in range(0, 3):
            x = d + pd.Timedelta(days=offset)
            if x in rain_by_date.index:
                total += float(rain_by_date.loc[x])
        future_rain.append(total)
    events['observed_72h_rain_mm'] = future_rain
    events['rain_opportunity_20mm'] = events['observed_72h_rain_mm'] >= 20.0
    all_events.append(events)

    count = len(events)
    opps = int(events['rain_opportunity_20mm'].sum()) if count else 0
    total_irr = float(events['AppliedToday'].sum()) if count else 0.0
    min_stress = float(df['Sugarcane.swdef_photo'].min()) if 'Sugarcane.swdef_photo' in df.columns else float('nan')
    stress09 = int((df['Sugarcane.swdef_photo'] < 0.9).sum()) if 'Sugarcane.swdef_photo' in df.columns else 0
    rain_total = float(df['Rain'].sum()) if 'Rain' in df.columns else float('nan')

    # Prefer a season with multiple genuine irrigation decisions and at least
    # one observed rainfall opportunity near a trigger. 8-15 events is the
    # desired demonstration range, but the best available season is still selected.
    target_penalty = 0 if 8 <= count <= 15 else min(abs(count - 8), abs(count - 15))
    score = count + 4 * opps - 2 * target_penalty
    rows.append({
        'season_start_year': year,
        'irrigation_events': count,
        'total_irrigation_mm': total_irr,
        'observed_rain_mm': rain_total,
        'rain_opportunities_ge20mm_72h': opps,
        'min_swdef_photo': min_stress,
        'stress_days_lt_0_9': stress09,
        'score': score,
    })

summary = pd.DataFrame(rows).sort_values(['score','rain_opportunities_ge20mm_72h','irrigation_events'], ascending=False)
summary.to_csv(ROOT / 'walkamin_season_screen_summary.csv', index=False)
events_all = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
events_all.to_csv(ROOT / 'walkamin_all_baseline_irrigation_events.csv', index=False)

best = summary.iloc[0].to_dict()
year = int(best['season_start_year'])
selected_events = events_all[events_all['season_start_year'] == year].copy()
selected_events.to_csv(ROOT / 'walkamin_selected_baseline_events.csv', index=False)

selected = {
    'season_start_year': year,
    'planting_date': f'{year}-04-01',
    'preharvest_end': f'{year+1}-06-24',
    'dryoff_start': f'{year+1}-05-14',
    'baseline_apsimx': f'Walkamin_{year}_{year+1}_baseline.apsimx',
    'baseline_csv': f'Walkamin_{year}_{year+1}_baseline.Report.csv',
    'weather_met': f'Walkamin_{year}_{year+1}.met',
    'irrigation_events': int(best['irrigation_events']),
    'total_irrigation_mm': float(best['total_irrigation_mm']),
    'observed_rain_mm': float(best['observed_rain_mm']),
    'rain_opportunities_ge20mm_72h': int(best['rain_opportunities_ge20mm_72h']),
    'soil_proxy': 'Walkamin/Tablelands krasnozem-Ferrosol proxy, ~110 mm PAWC per metre, max scheduling root depth 1800 mm',
    'irrigation_rule': '50% PAWC depletion; 50 mm max event; minimum 10-day return; 42-day dry-off',
}
(ROOT / 'walkamin_selected_season.json').write_text(json.dumps(selected, indent=2))

print('\n=== Walkamin season screen ===')
print(summary.to_string(index=False))
print('\n=== Selected season ===')
print(json.dumps(selected, indent=2))
print('\n=== Selected irrigation events ===')
cols = ['Clock.Today','AppliedToday','interval_days','RootDepthUsed','RootPAWC','RootDeficit','FractionAvailable','observed_72h_rain_mm','Sugarcane.swdef_photo']
cols = [c for c in cols if c in selected_events.columns]
print(selected_events[cols].to_string(index=False))
