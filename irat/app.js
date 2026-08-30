const state={manifest:null,summaries:[],timeseries:[],current:null};
const $=id=>document.getElementById(id);
const unique=a=>[...new Set(a.filter(v=>v!==undefined&&v!==null))];
const n=(v,d=0)=>Number(v||0).toLocaleString('en-AU',{maximumFractionDigits:d,minimumFractionDigits:d});
const pct=v=>`${Number(v)>=0?'+':''}${n(v,1)}%`;
const niceDate=s=>new Date(`${s}T00:00:00`).toLocaleDateString('en-AU',{day:'numeric',month:'short'});

async function load(){
  try{
    const [manifest,summaries,timeseries]=await Promise.all([
      fetch('data/cube_manifest.json').then(r=>r.json()),
      fetch('data/scenario_summary.json').then(r=>r.json()),
      fetch('data/scenario_timeseries.json').then(r=>r.json())
    ]);
    Object.assign(state,{manifest,summaries,timeseries});
    buildControls();
    selectScenario(summaries[0]);
    renderDataset();
  }catch(e){
    $('matchHint').textContent='CLOVER scenario data could not be loaded.';
    $('applyButton').disabled=true;
    console.error(e);
  }
}

function fill(id,values,label=v=>v){
  $(id).innerHTML=values.map(v=>`<option value="${String(v).replace(/"/g,'&quot;')}">${label(v)}</option>`).join('');
}

function buildControls(){
  fill('forecastSource',unique(state.summaries.map(s=>s.forecast_source)));
  fill('forecastHorizon',unique(state.summaries.map(s=>s.forecast_horizon_days)),v=>`${v} days`);
  fill('rainThreshold',unique(state.summaries.map(s=>s.rain_threshold_mm)),v=>`${v} mm`);
  fill('probabilityThreshold',unique(state.summaries.map(s=>s.probability_threshold)),v=>`${Math.round(v*100)}%`);
  ['forecastSource','forecastHorizon','rainThreshold','probabilityThreshold'].forEach(id=>$(id).addEventListener('change',checkMatch));
  $('applyButton').onclick=()=>{const s=findScenario();if(s)selectScenario(s)};
  checkMatch();
}

function findScenario(){
  return state.summaries.find(s=>
    s.forecast_source===$('forecastSource').value&&
    +s.forecast_horizon_days===+$('forecastHorizon').value&&
    +s.rain_threshold_mm===+$('rainThreshold').value&&
    +s.probability_threshold===+$('probabilityThreshold').value
  );
}

function checkMatch(){
  const s=findScenario();
  $('applyButton').disabled=!s;
  $('matchHint').textContent=s?'This completed scenario is available.':'This combination has not been simulated yet.';
}

function setControls(s){
  $('forecastSource').value=s.forecast_source;
  $('forecastHorizon').value=s.forecast_horizon_days;
  $('rainThreshold').value=s.rain_threshold_mm;
  $('probabilityThreshold').value=s.probability_threshold;
  checkMatch();
}

function selectScenario(s){
  state.current=s;
  setControls(s);
  $('sitePeriod').textContent=`${s.site} · ${s.period}`;
  renderSummary();
  renderCharts();
  renderTable();
}

function rows(){
  if(!state.current)return[];
  return state.timeseries.filter(r=>r.scenario_id===state.current.scenario_id).sort((a,b)=>a.date.localeCompare(b.date));
}

function renderDataset(){
  const historical=state.manifest?.dataset_status==='historical_simulation';
  $('datasetBadge').textContent=historical?'Historical APSIM + forecast result':'Prototype data';
  $('dataNotice').style.display='block';
  $('dataNotice').textContent=historical
    ?'Only completed forecast combinations are shown. At present the cube contains the completed Walkamin 2008–09 GEFS scenario; additional forecast types and thresholds will appear here as they are simulated.'
    :'Prototype values only — not a CLOVER research finding.';
}

function renderSummary(){
  const s=state.current;
  $('baselineWater').textContent=`${n(s.mean_baseline_irrigation_mm)} mm`;
  $('forecastWater').textContent=`${n(s.mean_forecast_irrigation_mm)} mm`;
  $('baselineEvents').textContent=n(s.mean_baseline_events,0);
  $('forecastEvents').textContent=n(s.mean_forecast_events,0);
  $('waterSaved').textContent=`${n(s.mean_water_saved_mm)} mm`;
  $('forecastHolds').textContent=n(s.mean_hold_days||0,0);
  $('eventsAvoided').textContent=n(s.mean_irrigations_avoided||0,0);
  $('yieldChange').textContent=pct(s.mean_yield_change_pct||0);

  if(s.mean_water_saved_mm>0){
    $('resultSentence').innerHTML=`Using the forecast reduced irrigation by <b>${n(s.mean_water_saved_mm)} mm</b> (${n(s.mean_water_saved_pct,1)}%) with a simulated cane change of <b>${pct(s.mean_yield_change_pct)}</b>.`;
  }else if((s.mean_hold_days||0)>0){
    $('resultSentence').innerHTML=`The forecast changed <b>${n(s.mean_hold_days,0)} irrigation decision days</b>, but total seasonal irrigation stayed the same at <b>${n(s.mean_forecast_irrigation_mm)} mm</b>.`;
  }else{
    $('resultSentence').textContent='For this scenario the forecast did not change the irrigation outcome.';
  }
}

function renderCharts(){
  const s=state.current,r=rows(),cats=r.map(x=>niceDate(x.date));
  if(!window.Highcharts)return;

  Highcharts.chart('irrigationChart',{
    chart:{type:'column'},title:{text:null},credits:{enabled:false},legend:{align:'center',verticalAlign:'bottom'},
    xAxis:{categories:cats,title:{text:null}},
    yAxis:{min:0,title:{text:'Irrigation (mm)'}},
    tooltip:{shared:true,valueSuffix:' mm'},
    series:[
      {name:'Without forecast',data:r.map(x=>+x.baseline_irrigation_mm||0)},
      {name:'With forecast',data:r.map(x=>+x.forecast_irrigation_mm||0)}
    ]
  });

  Highcharts.chart('forecastObservationChart',{
    chart:{type:'column'},title:{text:null},credits:{enabled:false},legend:{align:'center',verticalAlign:'bottom'},
    xAxis:{categories:cats,title:{text:null}},
    yAxis:{min:0,title:{text:`Rainfall over ${s.forecast_horizon_days} days (mm)`}},
    tooltip:{shared:true,valueSuffix:' mm'},
    series:[
      {name:'Forecast mean rainfall',data:r.map(x=>x.forecast_mean_mm==null?null:+x.forecast_mean_mm)},
      {name:'Observed rainfall',data:r.map(x=>x.actual_next3d_rain_mm==null?null:+x.actual_next3d_rain_mm)}
    ]
  });
  $('forecastChartNote').textContent=`Forecast mean rainfall compared with observed rainfall over the next ${s.forecast_horizon_days} days. The irrigation rule holds when the probability of at least ${s.rain_threshold_mm} mm reaches ${Math.round(s.probability_threshold*100)}%.`;
}

function renderTable(){
  const s=state.current;
  $('decisionTable').innerHTML=rows().map(r=>{
    let action='No irrigation';
    if(r.decision_changed)action='Hold / delay';
    else if(+r.forecast_irrigation_mm>0)action='Irrigate';
    return `<tr><td>${niceDate(r.date)}</td><td>${r.forecast_mean_mm==null?'—':n(r.forecast_mean_mm,1)+' mm'}</td><td>${r.forecast_probability==null?'—':Math.round(r.forecast_probability*100)+'%'}</td><td>${r.actual_next3d_rain_mm==null?'—':n(r.actual_next3d_rain_mm,1)+' mm'}</td><td>${action}</td></tr>`;
  }).join('');
}

load();
