from pathlib import Path
import pandas as pd

ROOT = Path('phase3/walkamin')
files = {
    'baseline': ROOT / 'Walkamin_2008_2009_baseline.Report.csv',
    'forecast_aware': ROOT / 'Walkamin_2008_2009_forecast.Report.csv',
    'perfect_information': ROOT / 'Walkamin_2008_2009_perfect.Report.csv',
}
rows = []
daily = []
for strategy, path in files.items():
    df = pd.read_csv(path)
    df['Clock.Today'] = pd.to_datetime(df['Clock.Today'])
    events = df[df['AppliedToday'] > 0].copy()
    final = df.iloc[-1]
    row = {
        'strategy': strategy,
        'irrigation_events': int(len(events)),
        'total_irrigation_mm': float(events['AppliedToday'].sum()),
        'last_irrigation_date': str(events['Clock.Today'].max().date()) if len(events) else '',
        'min_swdef_photo': float(df['Sugarcane.swdef_photo'].min()),
        'stress_days_swdef_lt_0_9': int((df['Sugarcane.swdef_photo'] < 0.9).sum()),
        'stress_days_swdef_lt_0_7': int((df['Sugarcane.swdef_photo'] < 0.7).sum()),
        'end_biomass': float(final['Sugarcane.biomass']),
        'end_cane_wt': float(final['Sugarcane.cane_wt']),
        'end_sucrose_wt': float(final['Sugarcane.sucrose_wt']),
        'hold_days': int((df['HoldToday'] > 0).sum()) if 'HoldToday' in df.columns else 0,
    }
    rows.append(row)
    keep = ['Clock.Today','Rain','AppliedToday','RootDeficit','RootPAWC','FractionAvailable','Sugarcane.swdef_photo','Sugarcane.biomass','Sugarcane.cane_wt','Sugarcane.sucrose_wt']
    keep = [c for c in keep if c in df.columns]
    x = df[keep].copy(); x['strategy'] = strategy; daily.append(x)

summary = pd.DataFrame(rows)
base = summary.loc[summary['strategy']=='baseline'].iloc[0]
summary['water_saved_vs_baseline_mm'] = base['total_irrigation_mm'] - summary['total_irrigation_mm']
summary['water_saved_vs_baseline_pct'] = 100 * summary['water_saved_vs_baseline_mm'] / base['total_irrigation_mm']
summary['sucrose_change_vs_baseline'] = summary['end_sucrose_wt'] - base['end_sucrose_wt']
summary['sucrose_change_vs_baseline_pct'] = 100 * summary['sucrose_change_vs_baseline'] / base['end_sucrose_wt']
summary.to_csv(ROOT / 'walkamin_2008_strategy_summary.csv', index=False)
pd.concat(daily, ignore_index=True).to_csv(ROOT / 'walkamin_2008_daily_comparison.csv', index=False)

forecast = pd.read_csv(files['forecast_aware'])
forecast['Clock.Today'] = pd.to_datetime(forecast['Clock.Today'])
fd = forecast[(forecast.get('AppliedToday',0) > 0) | (forecast.get('HoldToday',0) > 0)].copy()
fd.to_csv(ROOT / 'walkamin_2008_forecast_decision_days.csv', index=False)
perfect = pd.read_csv(files['perfect_information'])
perfect['Clock.Today'] = pd.to_datetime(perfect['Clock.Today'])
pd_ = perfect[(perfect.get('AppliedToday',0) > 0) | (perfect.get('HoldToday',0) > 0)].copy()
pd_.to_csv(ROOT / 'walkamin_2008_perfect_decision_days.csv', index=False)

print('\n=== Walkamin 2008-09 strategy summary ===')
print(summary.to_string(index=False))
print('\n=== Forecast-aware decision days ===')
cols = ['Clock.Today','AppliedToday','RootDeficit','RootPAWC','FractionAvailable','ForecastProb','ForecastMean','HoldToday','DecisionCode','Sugarcane.swdef_photo']
cols = [c for c in cols if c in fd.columns]
print(fd[cols].to_string(index=False))
print('\n=== Perfect-information decision days ===')
cols = ['Clock.Today','AppliedToday','RootDeficit','RootPAWC','FractionAvailable','FutureObservedRain','HoldToday','DecisionCode','Sugarcane.swdef_photo']
cols = [c for c in cols if c in pd_.columns]
print(pd_[cols].to_string(index=False))
