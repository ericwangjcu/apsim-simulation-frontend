from __future__ import annotations

from pathlib import Path
import pandas as pd

ROOT = Path('phase3/run')
FILES = {
    'baseline': ROOT / 'Sugarcane_full_plant_baseline.Report.csv',
    'forecast_aware': ROOT / 'Sugarcane_full_plant_forecast.Report.csv',
    'perfect_information': ROOT / 'Sugarcane_full_plant_perfect.Report.csv',
}

summaries = []
daily_parts = []
for strategy, path in FILES.items():
    df = pd.read_csv(path)
    df['Clock.Today'] = pd.to_datetime(df['Clock.Today'])
    endrow = df.iloc[-1]
    events = df[df['AppliedToday'] > 0].copy()
    holds = int((df['HoldToday'] > 0).sum()) if 'HoldToday' in df.columns else 0

    summaries.append({
        'strategy': strategy,
        'irrigation_events': int(len(events)),
        'total_irrigation_mm': float(df['AppliedToday'].sum()),
        'hold_days': holds,
        'last_irrigation_date': events['Clock.Today'].max().date().isoformat() if len(events) else None,
        'min_swdef_photo': float(df['Sugarcane.swdef_photo'].min()),
        'stress_days_swdef_lt_0_9': int((df['Sugarcane.swdef_photo'] < 0.9).sum()),
        'stress_days_swdef_lt_0_7': int((df['Sugarcane.swdef_photo'] < 0.7).sum()),
        'end_biomass': float(endrow['Sugarcane.biomass']),
        'end_cane_wt': float(endrow['Sugarcane.cane_wt']),
        'end_sucrose_wt': float(endrow['Sugarcane.sucrose_wt']),
    })

    event_cols = ['Clock.Today','AppliedToday','RootDepthUsed','RootPAWC','RootDeficit','Sugarcane.swdef_photo']
    for c in ['ForecastProb','ForecastMean','FutureObservedRain','HoldToday','DecisionCode']:
        if c in df.columns:
            event_cols.append(c)
    mask = df['AppliedToday'] > 0
    if 'HoldToday' in df.columns:
        mask = mask | (df['HoldToday'] > 0)
    df.loc[mask, event_cols].to_csv(ROOT / f'full_plant_{strategy}_decision_days.csv', index=False)

    keep = ['Clock.Today','AppliedToday','Sugarcane.swdef_photo','Sugarcane.biomass','Sugarcane.cane_wt','Sugarcane.sucrose_wt']
    part = df[keep].copy()
    part.columns = ['date'] + [f'{strategy}_{x}' for x in keep[1:]]
    daily_parts.append(part)

summary = pd.DataFrame(summaries)
baseline_water = float(summary.loc[summary['strategy'] == 'baseline', 'total_irrigation_mm'].iloc[0])
baseline_sucrose = float(summary.loc[summary['strategy'] == 'baseline', 'end_sucrose_wt'].iloc[0])
summary['water_saved_vs_baseline_mm'] = baseline_water - summary['total_irrigation_mm']
summary['water_saved_vs_baseline_pct'] = 100.0 * summary['water_saved_vs_baseline_mm'] / baseline_water if baseline_water else 0.0
summary['sucrose_change_vs_baseline'] = summary['end_sucrose_wt'] - baseline_sucrose
summary['sucrose_change_vs_baseline_pct'] = 100.0 * summary['sucrose_change_vs_baseline'] / baseline_sucrose if baseline_sucrose else 0.0
summary.to_csv(ROOT / 'full_plant_strategy_summary.csv', index=False)

daily = daily_parts[0]
for p in daily_parts[1:]:
    daily = daily.merge(p, on='date', how='outer')
daily.to_csv(ROOT / 'full_plant_daily_strategy_comparison.csv', index=False)

forecast = pd.read_csv(ROOT / 'gefs_full_plant_decision_forecasts.csv')
forecast.to_csv(ROOT / 'full_plant_forecast_audit.csv', index=False)

print('\n=== Full plant-crop forecast comparison ===')
print(summary.to_string(index=False))
print('\nForecast-aware decision days:')
print(pd.read_csv(ROOT / 'full_plant_forecast_aware_decision_days.csv').to_string(index=False))
print('\nPerfect-information decision days:')
print(pd.read_csv(ROOT / 'full_plant_perfect_information_decision_days.csv').to_string(index=False))
