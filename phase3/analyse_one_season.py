from __future__ import annotations

from pathlib import Path
import pandas as pd

ROOT = Path('phase3/run')
START = pd.Timestamp('2000-07-01')
END = pd.Timestamp('2000-09-07')

FILES = {
    'baseline': ROOT / 'Sugarcane_phase3_baseline.Report.csv',
    'forecast_aware': ROOT / 'Sugarcane_phase3_forecast.Report.csv',
    'perfect_information': ROOT / 'Sugarcane_phase3_perfect.Report.csv',
}

summaries = []
daily_parts = []
for strategy, path in FILES.items():
    df = pd.read_csv(path)
    df['Clock.Today'] = pd.to_datetime(df['Clock.Today'])
    s = df[(df['Clock.Today'] >= START) & (df['Clock.Today'] <= END)].copy()

    # The official Sugarcane example harvest/reset occurs on 7 Sep 2000, so
    # the final row has zero crop mass. Use the last positive-biomass row as
    # the crop endpoint to report the immediately pre-harvest state.
    positive = s[s['Sugarcane.biomass'] > 0]
    crop_endrow = positive.iloc[-1] if len(positive) else s.iloc[-1]
    events = s[s['AppliedToday'] > 0].copy()
    holds = int((s['HoldToday'] > 0).sum()) if 'HoldToday' in s.columns else 0

    row = {
        'strategy': strategy,
        'irrigation_events': int(len(events)),
        'total_irrigation_mm': float(s['AppliedToday'].sum()),
        'hold_days': holds,
        'min_swdef_photo': float(s['Sugarcane.swdef_photo'].min()),
        'stress_days_swdef_lt_0_9': int((s['Sugarcane.swdef_photo'] < 0.9).sum()),
        'stress_days_swdef_lt_0_7': int((s['Sugarcane.swdef_photo'] < 0.7).sum()),
        'crop_endpoint_date': crop_endrow['Clock.Today'].date().isoformat(),
        'preharvest_biomass': float(crop_endrow['Sugarcane.biomass']),
        'preharvest_cane_wt': float(crop_endrow['Sugarcane.cane_wt']),
        'preharvest_sucrose_wt': float(crop_endrow['Sugarcane.sucrose_wt']),
    }
    summaries.append(row)

    event_cols = ['Clock.Today', 'AppliedToday', 'TopSWC', 'TopSWdeficit', 'Sugarcane.swdef_photo']
    for c in ['ForecastProb', 'ForecastMean', 'FutureObservedRain', 'HoldToday', 'DecisionCode']:
        if c in s.columns:
            event_cols.append(c)
    interesting = s[(s['AppliedToday'] > 0) | ((s['HoldToday'] > 0) if 'HoldToday' in s.columns else False)].copy()
    interesting[event_cols].to_csv(ROOT / f'{strategy}_decision_days.csv', index=False)

    keep = ['Clock.Today', 'AppliedToday', 'Sugarcane.swdef_photo', 'Sugarcane.biomass', 'Sugarcane.cane_wt', 'Sugarcane.sucrose_wt']
    part = s[keep].copy()
    part.columns = ['date'] + [f'{strategy}_{c}' for c in keep[1:]]
    daily_parts.append(part)

summary = pd.DataFrame(summaries)
baseline_water = float(summary.loc[summary['strategy'] == 'baseline', 'total_irrigation_mm'].iloc[0])
baseline_sucrose = float(summary.loc[summary['strategy'] == 'baseline', 'preharvest_sucrose_wt'].iloc[0])
summary['water_saved_vs_baseline_mm'] = baseline_water - summary['total_irrigation_mm']
summary['water_saved_vs_baseline_pct'] = 100.0 * summary['water_saved_vs_baseline_mm'] / baseline_water
summary['sucrose_change_vs_baseline'] = summary['preharvest_sucrose_wt'] - baseline_sucrose
summary['sucrose_change_vs_baseline_pct'] = 100.0 * summary['sucrose_change_vs_baseline'] / baseline_sucrose
summary.to_csv(ROOT / 'phase3_one_season_summary.csv', index=False)

daily = daily_parts[0]
for p in daily_parts[1:]:
    daily = daily.merge(p, on='date', how='outer')
daily.to_csv(ROOT / 'phase3_daily_strategy_comparison.csv', index=False)

forecast = pd.read_csv(ROOT / 'gefs_season_decision_forecasts.csv')
forecast.to_csv(ROOT / 'phase3_forecast_audit.csv', index=False)

print('\n=== Phase 3 one-season comparison ===')
print(summary.to_string(index=False))
print('\nForecast-aware decision days:')
print(pd.read_csv(ROOT / 'forecast_aware_decision_days.csv').to_string(index=False))
print('\nPerfect-information decision days:')
print(pd.read_csv(ROOT / 'perfect_information_decision_days.csv').to_string(index=False))
