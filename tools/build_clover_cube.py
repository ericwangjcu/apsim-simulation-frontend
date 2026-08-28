#!/usr/bin/env python3
"""Build the CLOVER front-end JSON cube from Phase 3 season outputs.

Expected source layout (created by apsim-phase3-walkamin-risk3-multiyear.yml):
  season_outputs/<year>/summary.csv
  season_outputs/<year>/*_daily.csv
  season_outputs/<year>/*_decisions.csv

The script intentionally keeps farm area, pump and price settings out of the cube;
those are calculated instantly in the browser.
"""
from __future__ import annotations
import argparse, csv, json, statistics
from pathlib import Path

ALIASES = {
    'baseline': ('baseline',),
    'forecast': ('gefs_3d','forecast','forecast_aware','gefs'),
    'perfect': ('perfect_3d','perfect_information','perfect'),
}

def num(v, default=0.0):
    try: return float(v)
    except (TypeError, ValueError): return default

def pick(row, *names, default=0.0):
    low={k.lower():v for k,v in row.items()}
    for n in names:
        if n.lower() in low: return num(low[n.lower()], default)
    return default

def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as f: return list(csv.DictReader(f))

def scenario_kind(name):
    n=(name or '').lower()
    for kind,tokens in ALIASES.items():
        if any(t in n for t in tokens): return kind
    return None

def find_summary_rows(folder):
    rows=read_csv(folder/'summary.csv')
    by={}
    for r in rows:
        k=scenario_kind(r.get('scenario') or r.get('Scenario') or '')
        if k: by[k]=r
    if 'baseline' not in by or 'forecast' not in by:
        raise ValueError(f'{folder}/summary.csv needs baseline and GEFS/forecast rows; found {list(by)}')
    return by

def daily_file(folder, kind):
    files=list(folder.glob('*_daily.csv'))
    tokens=ALIASES[kind]
    return next((p for p in files if any(t in p.stem.lower() for t in tokens)), None)

def decision_file(folder, kind):
    files=list(folder.glob('*_decisions.csv'))
    tokens=ALIASES[kind]
    return next((p for p in files if any(t in p.stem.lower() for t in tokens)), None)

def load_json(path, default):
    if not path.exists(): return default
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default

def merge_by_scenario(existing, incoming, sid):
    return [r for r in existing if r.get("scenario_id") != sid] + incoming

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input', required=True, type=Path)
    ap.add_argument('--output', default=Path('data'), type=Path)
    ap.add_argument('--site', default='Walkamin')
    ap.add_argument('--soil', default='Walkamin reference soil')
    ap.add_argument('--crop', default='Sugarcane – plant crop')
    ap.add_argument('--irrigation-amount', type=float, default=60)
    ap.add_argument('--forecast-horizon', type=int, default=3)
    ap.add_argument('--rain-threshold', type=float, default=20)
    ap.add_argument('--prob-threshold', type=float, default=.5)
    ap.add_argument('--forecast-source', default='GEFS historical forecast')
    args=ap.parse_args(); args.output.mkdir(parents=True,exist_ok=True)

    year_dirs=sorted([p for p in args.input.iterdir() if p.is_dir() and p.name.isdigit()], key=lambda p:int(p.name))
    if not year_dirs: raise SystemExit(f'No year folders found in {args.input}')
    sid=f"{args.site.lower().replace(' ','_')}_{args.forecast_horizon}d_{args.rain_threshold:g}_{int(args.prob_threshold*100)}"
    annual=[]; timeline=[]
    for folder in year_dirs:
        year=int(folder.name); by=find_summary_rows(folder); b=by['baseline']; f=by['forecast']
        birr=pick(b,'irrigation_mm'); firr=pick(f,'irrigation_mm'); save=birr-firr
        byield=pick(b,'cane_yield_kg_ha')/1000; fyield=pick(f,'cane_yield_kg_ha')/1000
        ychg=100*(fyield-byield)/byield if byield else 0
        annual.append({
          'scenario_id':sid,'year':year,'rainfall_mm':pick(f,'rain_mm'),'baseline_irrigation_mm':birr,'forecast_irrigation_mm':firr,
          'water_saved_mm':save,'water_saved_pct':100*save/birr if birr else 0,
          'baseline_events':pick(b,'irrigation_events'),'forecast_events':pick(f,'irrigation_events'),'irrigations_avoided':pick(b,'irrigation_events')-pick(f,'irrigation_events'),
          'baseline_cane_yield_t_ha':byield,'forecast_cane_yield_t_ha':fyield,'yield_change_pct':ychg,
          'baseline_runoff_mm':pick(b,'runoff_mm'),'forecast_runoff_mm':pick(f,'runoff_mm'),'baseline_drainage_mm':pick(b,'drainage_mm'),'forecast_drainage_mm':pick(f,'drainage_mm'),
          'baseline_water_stress':pick(b,'mean_water_stress'),'forecast_water_stress':pick(f,'mean_water_stress')
        })

        # Daily data are merged conservatively by date. Column names vary between APSIM report versions,
        # so only known/common fields are emitted. Missing fields remain zero/null rather than fabricated.
        bfile=daily_file(folder,'baseline'); ffile=daily_file(folder,'forecast')
        decisions=decision_file(folder,'forecast')
        dec_by_date={}
        if decisions:
            for r in read_csv(decisions):
                d=r.get('date') or r.get('Date')
                if d: dec_by_date[d]=r
        if bfile and ffile:
            bd={r.get('date') or r.get('Date'):r for r in read_csv(bfile)}
            fd={r.get('date') or r.get('Date'):r for r in read_csv(ffile)}
            for d in sorted(set(bd)&set(fd)):
                br,fr=bd[d],fd[d]; dr=dec_by_date.get(d,{})
                bi=pick(br,'irrigation','irrigation_mm','Irrigation')
                fi=pick(fr,'irrigation','irrigation_mm','Irrigation')
                timeline.append({'scenario_id':sid,'year':year,'date':d,
                    'rain_mm':pick(fr,'rain','rain_mm','Rain'),'forecast_probability':pick(dr,'probability','forecast_probability','prob_ge_threshold',default=0),
                    'water_stress':pick(fr,'water_stress','crop_water_stress','WaterStress',default=0),'baseline_irrigation_mm':bi,'forecast_irrigation_mm':fi,
                    'decision_changed':abs(bi-fi)>1e-9,'actual_next3d_rain_mm':pick(dr,'actual_next3d_rain_mm','actual_3d_rain',default=0) if dr else None,
                    'decision_note':(dr.get('decision') or dr.get('reason') or '') if dr else ''})

    mean=lambda k: statistics.fmean(r[k] for r in annual)
    summary={
      'scenario_id':sid,'site':args.site,'soil':args.soil,'crop':args.crop,'period':f'{annual[0]["year"]}–{annual[-1]["year"]}','irrigation_amount_mm':args.irrigation_amount,
      'irrigation_trigger':'APSIM Phase 3 irrigation trigger','forecast_horizon_days':args.forecast_horizon,'rain_threshold_mm':args.rain_threshold,'probability_threshold':args.prob_threshold,'forecast_source':args.forecast_source,
      'n_years':len(annual),'mean_baseline_irrigation_mm':mean('baseline_irrigation_mm'),'mean_forecast_irrigation_mm':mean('forecast_irrigation_mm'),'mean_water_saved_mm':mean('water_saved_mm'),
      'mean_water_saved_pct':100*mean('water_saved_mm')/mean('baseline_irrigation_mm') if mean('baseline_irrigation_mm') else 0,
      'mean_baseline_events':mean('baseline_events'),'mean_forecast_events':mean('forecast_events'),'mean_irrigations_avoided':mean('irrigations_avoided'),
      'mean_baseline_runoff_mm':mean('baseline_runoff_mm'),'mean_forecast_runoff_mm':mean('forecast_runoff_mm'),'mean_baseline_drainage_mm':mean('baseline_drainage_mm'),'mean_forecast_drainage_mm':mean('forecast_drainage_mm'),
      'mean_baseline_water_stress':mean('baseline_water_stress'),'mean_forecast_water_stress':mean('forecast_water_stress'),'mean_baseline_cane_yield_t_ha':mean('baseline_cane_yield_t_ha'),'mean_forecast_cane_yield_t_ha':mean('forecast_cane_yield_t_ha'),
      'mean_yield_change_pct':mean('yield_change_pct'),'years_with_saving':sum(r['water_saved_mm']>0 for r in annual),'years_with_yield_penalty':sum(r['yield_change_pct']<-1 for r in annual)
    }
    manifest={'schema_version':'0.1.0','dataset_status':'historical_simulation','title':'CLOVER historical simulation cube','source_workflow':'.github/workflows/apsim-phase3-walkamin-risk3-multiyear.yml','source_runner':'phase3/run_walkamin_3d_risk_season.py','dimensions':['site','soil','crop','period','irrigation_amount_mm','forecast_horizon_days','rain_threshold_mm','probability_threshold'],'tables':['scenario_summary.json','scenario_year.json','scenario_timeseries.json']}
    # Append/replace this scenario so the builder can be run repeatedly for a full parameter cube.
    summaries = merge_by_scenario(load_json(args.output/'scenario_summary.json', []), [summary], sid)
    annual_all = merge_by_scenario(load_json(args.output/'scenario_year.json', []), annual, sid)
    timeline_all = merge_by_scenario(load_json(args.output/'scenario_timeseries.json', []), timeline, sid)
    outputs=[('cube_manifest.json',manifest),('scenario_summary.json',summaries),('scenario_year.json',annual_all),('scenario_timeseries.json',timeline_all)]
    for name,obj in outputs:
        (args.output/name).write_text(json.dumps(obj,separators=(',',':')),encoding='utf-8')
    print(f'Built {sid}: {len(annual)} seasons, {len(timeline)} daily rows; cube now has {len(summaries)} scenarios -> {args.output}')
if __name__=='__main__': main()
