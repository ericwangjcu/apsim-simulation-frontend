const state={manifest:null,summaries:[],timeseries:[],fullSeason:null,current:null};
const $=id=>document.getElementById(id);
const unique=a=>[...new Set(a.filter(v=>v!==undefined&&v!==null))];
const n=(v,d=0)=>Number(v||0).toLocaleString('en-AU',{maximumFractionDigits:d,minimumFractionDigits:d});
const pct=v=>`${Number(v)>=0?'+':''}${n(v,2)}%`;
const niceDate=s=>new Date(`${s}T00:00:00`).toLocaleDateString('en-AU',{day:'numeric',month:'short',year:'numeric'});

async function decodeGridBundle(){
  const b64=await fetch('data/rule_grid_2008.b64').then(r=>r.text());
  const bytes=Uint8Array.from(atob(b64.trim()),c=>c.charCodeAt(0));
  const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(stream).text());
}

async function load(){
  try{
    const [manifest,bundle]=await Promise.all([
      fetch('data/cube_manifest.json').then(r=>r.json()),
      decodeGridBundle()
    ]);
    const {summaries,timeseries,fullSeason}=bundle;
    Object.assign(state,{manifest,summaries,timeseries,fullSeason});
    buildControls();
    const preferred=summaries.find(s=>+s.forecast_horizon_days===3&&+s.rain_threshold_mm===20&&+s.probability_threshold===0.6)||summaries[0];
    selectScenario(preferred);
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
  fill('forecastHorizon',unique(state.summaries.map(s=>s.forecast_horizon_days)).sort((a,b)=>a-b),v=>`${v} day${v===1?'':'s'}`);
  fill('rainThreshold',unique(state.summaries.map(s=>s.rain_threshold_mm)).sort((a,b)=>a-b),v=>`${n(v)} mm`);
  fill('probabilityThreshold',unique(state.summaries.map(s=>s.probability_threshold)).sort((a,b)=>a-b),v=>`${Math.round(v*100)}%`);
  ['forecastSource','forecastHorizon','rainThreshold','probabilityThreshold'].forEach(id=>$(id).addEventListener('change',checkMatch));
  $('applyButton').onclick=()=>{const s=findScenario();if(s)selectScenario(s)};
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
  $('matchHint').textContent=s?'Completed APSIM scenario available.':'This combination is not available.';
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
  $('datasetBadge').textContent='20 completed APSIM + GEFS scenarios';
  $('dataNotice').style.display='block';
  $('dataNotice').textContent='Walkamin 2008–09 proof-of-concept: 3-day GEFS rainfall forecast, 10/20/30/40 mm rainfall thresholds and 20/40/60/80/100% probability thresholds. These are test rules, not final agronomic recommendations.';
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
    $('resultSentence').innerHTML=`This rule held/delayed irrigation on <b>${n(s.mean_hold_days,0)} decision days</b>. Total seasonal irrigation stayed at <b>${n(s.mean_forecast_irrigation_mm)} mm</b> and simulated cane changed by <b>${pct(s.mean_yield_change_pct)}</b>.`;
  }else{
    $('resultSentence').innerHTML=`This forecast rule did <b>not change the irrigation schedule</b> in this season. Total irrigation remained ${n(s.mean_forecast_irrigation_mm)} mm.`;
  }
}

function dailyPoints(values,start){
  const t0=Date.parse(`${start}T00:00:00Z`);
  return values.map((v,i)=>[t0+i*86400000,v==null?null:+v]);
}

function renderCharts(){
  const s=state.current,r=rows(),fs=state.fullSeason;
  if(!window.Highcharts)return;
  const seasonScenario=fs?.scenarios?.[s.scenario_id];

  if(fs&&seasonScenario){
    Highcharts.chart('irrigationChart',{
      chart:{type:'column',zoomType:'x'},title:{text:null},credits:{enabled:false},legend:{align:'center',verticalAlign:'bottom'},
      xAxis:{type:'datetime',title:{text:null}},
      yAxis:{min:0,title:{text:'Irrigation (mm)'}},
      tooltip:{shared:true,xDateFormat:'%e %b %Y',valueSuffix:' mm'},
      plotOptions:{column:{pointRange:86400000,groupPadding:0.05,pointPadding:0.05}},
      series:[
        {name:'Without forecast',data:dailyPoints(fs.baseline_irrigation,fs.start)},
        {name:'With forecast',data:dailyPoints(seasonScenario.forecast_irrigation,fs.start)}
      ]
    });

    const gefs=Object.entries(seasonScenario.gefs||{}).map(([date,v])=>({
      x:Date.parse(`${date}T00:00:00Z`),y:+v[0],custom:{prob:+v[1]}
    }));
    Highcharts.chart('forecastObservationChart',{
      chart:{zoomType:'x'},title:{text:null},credits:{enabled:false},legend:{align:'center',verticalAlign:'bottom'},
      xAxis:{type:'datetime',title:{text:null}},
      yAxis:{min:0,title:{text:'Next-3-day rainfall (mm)'}},
      tooltip:{shared:false,xDateFormat:'%e %b %Y'},
      series:[
        {type:'line',name:'Observed next 3-day rainfall',data:dailyPoints(fs.observed72,fs.start),lineWidth:1,marker:{enabled:false},tooltip:{valueSuffix:' mm'}},
        {type:'scatter',name:'GEFS forecast mean on decision dates',data:gefs,marker:{radius:4},tooltip:{pointFormatter:function(){return `<span style="color:${this.color}">●</span> GEFS mean: <b>${Highcharts.numberFormat(this.y,1)} mm</b><br/>P(≥${n(s.rain_threshold_mm)} mm): <b>${Math.round(this.custom.prob*100)}%</b>`;}}}
      ]
    });
    $('forecastChartNote').textContent=`Full 2008–09 observed next-3-day rainfall is shown. GEFS points are the actual forecast dates reached by the selected ${n(s.rain_threshold_mm)} mm / ${Math.round(s.probability_threshold*100)}% irrigation rule.`;
  }
}

function renderTable(){
  $('decisionTable').innerHTML=rows().map(r=>{
    let action='No irrigation';
    if(+r.forecast_irrigation_mm>0) action='Irrigate';
    else if(r.decision_changed) action='Hold / delay';
    return `<tr><td>${niceDate(r.date)}</td><td>${r.forecast_mean_mm==null?'—':n(r.forecast_mean_mm,1)+' mm'}</td><td>${r.forecast_probability==null?'—':Math.round(r.forecast_probability*100)+'%'}</td><td>${r.actual_next3d_rain_mm==null?'—':n(r.actual_next3d_rain_mm,1)+' mm'}</td><td>${action}</td></tr>`;
  }).join('');
}

load();