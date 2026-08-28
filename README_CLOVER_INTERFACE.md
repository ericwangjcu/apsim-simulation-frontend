# CLOVER Forecast-informed Irrigation Explorer

Farmer-facing interface for exploring **precomputed** APSIM + historical forecast scenarios. APSIM is not run when a farmer changes a setting; the browser looks up a scenario in the CLOVER cube and recalculates farm-scale water, pump, energy and cost values instantly.

## Prototype branch

`clover-farmer-interface`

The interface contains an embedded **illustrative-data fallback** so the design can be reviewed before the research cube is loaded. The UI labels that data clearly and says it must not be used as a CLOVER finding.

When `data/cube_manifest.json`, `data/scenario_summary.json`, `data/scenario_year.json` and `data/scenario_timeseries.json` exist, the interface automatically loads those instead of the illustrative fallback.

## Existing CLOVER simulation source

The cube builder is designed around the existing Phase 3 outputs from:

- `.github/workflows/apsim-phase3-walkamin-risk3-multiyear.yml`
- `phase3/run_walkamin_3d_risk_season.py`

The existing workflow produces annual `summary.csv`, daily outputs and decision logs for each season. Those are converted into three frontend tables:

1. `scenario_summary.json` — one row per selectable configuration; drives instant KPIs and overview.
2. `scenario_year.json` — one row per configuration × season; drives annual charts and tables.
3. `scenario_timeseries.json` — daily rows; loaded for detailed season/decision views.

## Build the real 2000–2009 cube

After extracting the workflow artifact so the folders look like `phase3/season_outputs/2000/`, `2001/`, etc.:

```bash
python tools/build_clover_cube.py \
  --input phase3/season_outputs \
  --output data \
  --site Walkamin \
  --soil "Walkamin reference soil" \
  --crop "Sugarcane – plant crop" \
  --irrigation-amount 60 \
  --forecast-horizon 3 \
  --rain-threshold 20 \
  --prob-threshold 0.5
```

Run the builder again with other horizon / rainfall / confidence combinations. It **appends or replaces that scenario** in the same cube, so the front-end selectors grow as scenarios are added.

Once the real cube exists, the manifest is set to `historical_simulation` and the illustrative-data warning disappears automatically.

## Run locally

```bash
python -m http.server 8000
```

Open `http://localhost:8000/`.

## Views implemented

- **Overview** — average water saving, ML saving, irrigation events, pump hours, operating saving, yield effect, runoff/drainage and crop water stress.
- **Year by year** — baseline vs forecast-informed irrigation for every historical season with water saving and yield trade-off.
- **Season timeline** — rainfall, forecast probability, crop water stress, baseline irrigation, forecast-informed irrigation and individual changed decisions.
- **Water & cost** — converts mm saving into ML, pump hours, kWh and dollar savings from farmer-entered farm/pump/pricing settings.
- **Compare** — pin two forecast strategies and compare water saving, avoided irrigations, yield effect and risk across years.

## Scaling to the full CLOVER data cube

For a large multi-site cube, keep the same frontend schema but store the simulation source in partitioned Parquet/S3. A lightweight API can return only the selected summary, annual rows and one season of daily data. The farmer interface itself does not need to run APSIM or change materially.
