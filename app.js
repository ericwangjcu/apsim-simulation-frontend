const state = { manifest:null, summaries:[], years:[], timeseries:[], current:null, pinnedA:null, pinnedB:null };
const $ = id => document.getElementById(id);
const fmt = (v,d=1) => Number.isFinite(+v) ? Number(v).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d}) : '—';
const pct = v => `${v >= 0 ? '+' : ''}${fmt(v,1)}%`;
const unique = arr => [...new Set(arr.filter(v => v !== undefined && v !== null))];
const scenarioYears = id => state.years.filter(r => r.scenario_id === id).sort((a,b)=>a.year-b.year);
const scenarioTimeline = (id,year) => state.timeseries.filter(r=>r.scenario_id===id && +r.year===+year).sort((a,b)=>new Date(a.date)-new Date(b.date));

function buildDemoData(){
  const configs=[['walkamin_3d_20_50',3,20,.5,64,-.1,8,0],['walkamin_3d_30_50',3,30,.5,82,-.4,9,1],['walkamin_5d_20_50',5,20,.5,91,-1.3,9,3],['walkamin_3d_20_70',3,20,.7,46,0,6,0]];
  const summaries=[],years=[],timeseries=[];
  configs.forEach((cfg,si)=>{
    const [sid,h,thr,p,meanSave,yieldBase,yearsSave,yearsPenalty]=cfg, yr=[];
    for(let y=2000;y<=2009;y++){
      const i=y-2000, base=340+((y*17+si*11)%7)*20;
      const factors=[.45,.8,1.15,.65,1.35,.9,1.05,.55,1.2,.75];
      let save=Math.max(0,Math.round(meanSave*factors[i]/5)*5); if(i>=yearsSave) save=0;
      const forecast=base-save, baseYield=103+((y+si)%5)*2.1;
      let yc=yieldBase+[.3,-.1,.2,-.4,.1,0,.2,-.2,.1,-.1][i]; if(yearsPenalty&&i>=10-yearsPenalty) yc-=1.1;
      const fy=baseYield*(1+yc/100);
      const r={scenario_id:sid,year:y,rainfall_mm:900+((y*31)%8)*95,baseline_irrigation_mm:base,forecast_irrigation_mm:forecast,water_saved_mm:save,water_saved_pct:100*save/base,baseline_events:base/60,forecast_events:forecast/60,irrigations_avoided:save/60,baseline_cane_yield_t_ha:baseYield,forecast_cane_yield_t_ha:fy,yield_change_pct:yc,baseline_runoff_mm:120+((y+si)%4)*18,forecast_runoff_mm:115+((y+si)%4)*17,baseline_drainage_mm:170+((y+2*si)%5)*15,forecast_drainage_mm:160+((y+2*si)%5)*14,baseline_water_stress:.88+((y+si)%3)*.03,forecast_water_stress:.89+((y+si)%3)*.03};
      years.push(r); yr.push(r);
      for(let d=0;d<365;d+=30){
        const dt=new Date(Date.UTC(y,0,1+d)), rain=Math.max(0,Math.round(12*Math.sin((d+y)%37)+8));
        const prob=Math.max(.05,Math.min(.95,.45+.35*Math.sin((d+si*9)/31))), stress=Math.max(.55,Math.min(1.35,.92+.22*Math.sin((d+20)/45)));
        const event=d%60===0, changed=event&&prob>=p&&((d/60+si+y)%3!==0), bi=event?60:0, fi=changed?0:bi;
        timeseries.push({scenario_id:sid,year:y,date:dt.toISOString().slice(0,10),rain_mm:rain,forecast_probability:+prob.toFixed(2),water_stress:+stress.toFixed(2),baseline_irrigation_mm:bi,forecast_irrigation_mm:fi,decision_changed:changed,actual_next3d_rain_mm:changed?Math.round(10+35*prob):null,decision_note:changed?'Illustrative decision event for interface testing.':''});
      }
    }
    const avg=k=>yr.reduce((a,r)=>a+r[k],0)/yr.length;
    summaries.push({scenario_id:sid,site:'Walkamin',soil:'Walkamin reference soil',crop:'Sugarcane – plant crop',period:'2000–2009',irrigation_amount_mm:60,irrigation_trigger:'Prototype APSIM stress trigger',forecast_horizon_days:h,rain_threshold_mm:thr,probability_threshold:p,forecast_source:'GEFS historical forecast',n_years:10,mean_baseline_irrigation_mm:avg('baseline_irrigation_mm'),mean_forecast_irrigation_mm:avg('forecast_irrigation_mm'),mean_water_saved_mm:avg('water_saved_mm'),mean_water_saved_pct:100*avg('water_saved_mm')/avg('baseline_irrigation_mm'),mean_baseline_events:avg('baseline_events'),mean_forecast_events:avg('forecast_events'),mean_irrigations_avoided:avg('irrigations_avoided'),mean_hold_days:0,mean_baseline_runoff_mm:avg('baseline_runoff_mm'),mean_forecast_runoff_mm:avg('forecast_runoff_mm'),mean_baseline_drainage_mm:avg('baseline_drainage_mm'),mean_forecast_drainage_mm:avg('forecast_drainage_mm'),mean_baseline_water_stress:avg('baseline_water_stress'),mean_forecast_water_stress:avg('forecast_water_stress'),mean_baseline_cane_yield_t_ha:avg('baseline_cane_yield_t_ha'),mean_forecast_cane_yield_t_ha:avg('forecast_cane_yield_t_ha'),mean_yield_change_pct:avg('yield_change_pct'),years_with_saving:yr.filter(r=>r.water_saved_mm>0).length,years_with_yield_penalty:yr.filter(r=>r.yield_change_pct<-1).length});
  });
  return {manifest:{schema_version:'0.1.0',dataset_status:'illustrative_prototype',title:'CLOVER farmer interface prototype',warning:'Illustrative interface data only.'},summaries,years,timeseries};
}

async function loadData(){
  try {
    const [manifest,summaries,years,timeseries] = await Promise.all([
      fetch('data/cube_manifest.json').then(r=>{if(!r.ok) throw new Error('cube unavailable'); return r.json();}),
      fetch('data/scenario_summary.json').then(r=>{if(!r.ok) throw new Error('cube unavailable'); return r.json();}),
      fetch('data/scenario_year.json').then(r=>{if(!r.ok) throw new Error('cube unavailable'); return r.json();}),
      fetch('data/scenario_timeseries.json').then(r=>{if(!r.ok) throw new Error('cube unavailable'); return r.json();})
    ]);
    Object.assign(state,{manifest,summaries,years,timeseries});
  } catch(err) {
    const demo=buildDemoData(); Object.assign(state,demo);
  }
  renderDatasetStatus(); buildControls(); selectScenario(state.summaries[0]);
}
function renderDatasetStatus(){
  const demo = state.manifest.dataset_status !== 'historical_simulation';
  $('datasetBadge').textContent = demo ? 'Illustrative prototype data' : 'Historical simulation cube';
  $('datasetBadge').className = `badge ${demo ? 'warning' : ''}`;
  $('datasetNotice').style.display = demo ? 'block' : 'none';
  $('datasetNotice').textContent = demo ? 'Interface prototype only: the values shown are illustrative and must not be used as CLOVER research findings.' : '';
}
function fillSelect(id, values, labelFn=v=>v){ const el=$(id); el.innerHTML=''; values.forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=labelFn(v); el.appendChild(o); }); }
function buildControls(){
  fillSelect('site', unique(state.summaries.map(x=>x.site)));
  fillSelect('soil', unique(state.summaries.map(x=>x.soil)));
  fillSelect('crop', unique(state.summaries.map(x=>x.crop)));
  fillSelect('period', unique(state.summaries.map(x=>x.period)));
  fillSelect('irrigationAmount', unique(state.summaries.map(x=>x.irrigation_amount_mm)), v=>`${v} mm`);
  fillSelect('forecastHorizon', unique(state.summaries.map(x=>x.forecast_horizon_days)), v=>`${v} days`);
  fillSelect('rainThreshold', unique(state.summaries.map(x=>x.rain_threshold_mm)), v=>`${v} mm`);
  fillSelect('probabilityThreshold', unique(state.summaries.map(x=>x.probability_threshold)), v=>`${Math.round(v*100)}%`);
  ['site','soil','crop','period','irrigationAmount','forecastHorizon','rainThreshold','probabilityThreshold'].forEach(id=>$(id).addEventListener('change', updateMatchHint));
  ['farmArea','pumpFlow','pumpPower','energyPrice','waterPrice'].forEach(id=>$(id).addEventListener('input',()=>state.current&&renderAll()));
  $('runButton').onclick=()=>{ const s=findScenario(); if(s) selectScenario(s); };
  $('resetButton').onclick=()=>{ buildControls(); selectScenario(state.summaries[0]); };
  document.querySelectorAll('.tab').forEach(b=>b.onclick=()=>switchTab(b.dataset.tab));
  $('timelineYear').onchange=renderTimeline;
  $('pinA').onclick=()=>{state.pinnedA=state.current; renderCompare();};
  $('pinB').onclick=()=>{state.pinnedB=state.current; renderCompare();};
  updateMatchHint();
}
function findScenario(){
  const target={site:$('site').value,soil:$('soil').value,crop:$('crop').value,period:$('period').value,irrigation_amount_mm:+$('irrigationAmount').value,forecast_horizon_days:+$('forecastHorizon').value,rain_threshold_mm:+$('rainThreshold').value,probability_threshold:+$('probabilityThreshold').value};
  return state.summaries.find(s=>Object.entries(target).every(([k,v])=>typeof v==='number'?+s[k]===v:s[k]===v));
}
function updateMatchHint(){ const s=findScenario(); $('matchHint').textContent=s?'Scenario is available in the cube.':'This exact combination is not in the cube yet.'; $('runButton').disabled=!s; $('runButton').style.opacity=s?'1':'.5'; }
function setControls(s){ $('site').value=s.site; $('soil').value=s.soil; $('crop').value=s.crop; $('period').value=s.period; $('irrigationAmount').value=s.irrigation_amount_mm; $('forecastHorizon').value=s.forecast_horizon_days; $('rainThreshold').value=s.rain_threshold_mm; $('probabilityThreshold').value=s.probability_threshold; updateMatchHint(); }
function selectScenario(s){ state.current=s; setControls(s); renderAll(); }
function renderAll(){ renderHero(); renderKpis(); renderOverview(); renderAnnual(); populateTimelineYears(); renderTimeline(); renderCost(); renderCompare(); }
function costs(s){ const area=+$('farmArea').value||0, flow=+$('pumpFlow').value||0, power=+$('pumpPower').value||0, ep=+$('energyPrice').value||0, wp=+$('waterPrice').value||0; const ml=s.mean_water_saved_mm*area*0.01; const hours=flow>0 ? ml*1e6/(flow*3600) : 0; const kwh=hours*power; return {area,flow,power,ep,wp,ml,hours,kwh,energy:kwh*ep,water:ml*wp,total:kwh*ep+ml*wp}; }
function renderHero(){ const s=state.current; $('heroTitle').textContent=`${s.site}: ${s.forecast_horizon_days}-day forecast strategy`; $('heroText').textContent=`Delay irrigation when the probability of at least ${s.rain_threshold_mm} mm rain in the next ${s.forecast_horizon_days} days reaches ${Math.round(s.probability_threshold*100)}%. Compared with the same baseline irrigation rule without forecast information.`; $('heroPill').textContent=s.mean_water_saved_mm>0?`${fmt(s.mean_water_saved_pct,1)}% less irrigation`:`${fmt(s.mean_hold_days||0,0)} forecast hold days`; }
function renderKpis(){ const s=state.current,c=costs(s); $('kpiWater').textContent=`${fmt(s.mean_water_saved_mm,0)} mm/season`; $('kpiWaterPct').textContent=`${fmt(s.mean_water_saved_pct,1)}% less irrigation`; $('kpiML').textContent=`${fmt(c.ml,1)} ML/season`; $('kpiEvents').textContent=fmt(s.mean_hold_days ?? s.mean_irrigations_avoided,0); $('kpiHours').textContent=`${fmt(c.hours,0)} h/season`; $('kpiCost').textContent=`$${fmt(c.total,0)}`; $('kpiYield').textContent=pct(s.mean_yield_change_pct); $('kpiYield').className=Math.abs(s.mean_yield_change_pct)<=1?'good':s.mean_yield_change_pct<0?'risk':'good'; $('kpiYieldNote').textContent=Math.abs(s.mean_yield_change_pct)<=1?'little simulated crop change':'check crop trade-off'; }
function renderOverview(){
  const s=state.current;
  $('overviewNarrative').innerHTML = s.n_years===1 ? `<div class="callout"><strong>${fmt(s.mean_hold_days||0,0)} forecast hold days</strong> in the ${s.period} test season.</div><p>Baseline and forecast-informed strategies both applied <strong>${fmt(s.mean_baseline_irrigation_mm,0)} mm</strong> in total. The forecast changed the timing of irrigation but did not reduce total irrigation in this season.</p><p>Simulated cane outcome changed by <strong>${pct(s.mean_yield_change_pct)}</strong>; sucrose changed by <strong>${pct(s.sucrose_change_pct||0)}</strong>.</p>` : `<div class="callout"><strong>${s.years_with_saving} of ${s.n_years} seasons</strong> used less irrigation water under the forecast strategy.</div><p>Across ${s.period}, average irrigation fell from <strong>${fmt(s.mean_baseline_irrigation_mm,0)} mm</strong> to <strong>${fmt(s.mean_forecast_irrigation_mm,0)} mm</strong> per season, saving <strong>${fmt(s.mean_water_saved_mm,0)} mm</strong>.</p><p>Simulated cane yield changed by <strong>${pct(s.mean_yield_change_pct)}</strong>.</p>`;
  if(window.Highcharts) Highcharts.chart('overviewChart',{chart:{type:'column'},title:{text:null},credits:{enabled:false},xAxis:{categories:['Irrigation','Cane outcome']},yAxis:[{title:{text:'Irrigation (mm)'}},{title:{text:'Cane outcome (t/ha)'},opposite:true}],series:[{name:'Baseline irrigation',data:[s.mean_baseline_irrigation_mm,null],yAxis:0},{name:'Forecast irrigation',data:[s.mean_forecast_irrigation_mm,null],yAxis:0},{name:'Baseline cane',data:[null,s.mean_baseline_cane_yield_t_ha],yAxis:1},{name:'Forecast cane',data:[null,s.mean_forecast_cane_yield_t_ha],yAxis:1}]});
  const rows=[['Irrigation',`${fmt(s.mean_baseline_irrigation_mm,0)} mm`,`${fmt(s.mean_forecast_irrigation_mm,0)} mm`,`${fmt(s.mean_water_saved_mm,0)} mm saved`],['Irrigation events',fmt(s.mean_baseline_events,1),fmt(s.mean_forecast_events,1),`${fmt(s.mean_irrigations_avoided,1)} avoided`],['Forecast hold days','—',fmt(s.mean_hold_days||0,0),'timing changed'],['Runoff',`${fmt(s.mean_baseline_runoff_mm,0)} mm`,`${fmt(s.mean_forecast_runoff_mm,0)} mm`,`${fmt(s.mean_forecast_runoff_mm-s.mean_baseline_runoff_mm,1)} mm`],['Drainage',`${fmt(s.mean_baseline_drainage_mm,0)} mm`,`${fmt(s.mean_forecast_drainage_mm,0)} mm`,`${fmt(s.mean_forecast_drainage_mm-s.mean_baseline_drainage_mm,1)} mm`],['Mean crop water stress',fmt(s.mean_baseline_water_stress,3),fmt(s.mean_forecast_water_stress,3),fmt(s.mean_forecast_water_stress-s.mean_baseline_water_stress,3)],['Cane outcome',`${fmt(s.mean_baseline_cane_yield_t_ha,1)} t/ha`,`${fmt(s.mean_forecast_cane_yield_t_ha,1)} t/ha`,pct(s.mean_yield_change_pct)],['Sucrose change','—','—',pct(s.sucrose_change_pct||0)]];
  $('overviewMetrics').innerHTML=`<tr><th>Measure</th><th>Baseline</th><th>Forecast</th><th>Difference</th></tr>`+rows.map(r=>`<tr>${r.map(x=>`<td>${x}</td>`).join('')}</tr>`).join('');
}
function renderAnnual(){ const rows=scenarioYears(state.current.scenario_id); if(window.Highcharts) Highcharts.chart('annualChart',{chart:{type:'column'},title:{text:null},credits:{enabled:false},xAxis:{categories:rows.map(r=>r.season||String(r.year))},yAxis:{title:{text:'Irrigation (mm)'}},tooltip:{shared:true},series:[{name:'Baseline',data:rows.map(r=>r.baseline_irrigation_mm)},{name:'Forecast-informed',data:rows.map(r=>r.forecast_irrigation_mm)}]}); $('annualTable').innerHTML=rows.map(r=>{const cls=r.water_saved_mm>0&&r.yield_change_pct>=-1?'good':r.yield_change_pct<-1?'risk':'neutral'; const outcome=r.water_saved_mm>0&&r.yield_change_pct>=-1?'Benefit':r.yield_change_pct<-1?'Trade-off':'Timing changed'; return `<tr><td>${r.season||r.year}</td><td>${fmt(r.baseline_irrigation_mm,0)} mm</td><td>${fmt(r.forecast_irrigation_mm,0)} mm</td><td>${fmt(r.water_saved_mm,0)} mm</td><td>${fmt(r.water_saved_pct,1)}%</td><td>${pct(r.yield_change_pct)}</td><td class="${cls}">${outcome}</td></tr>`}).join(''); }
function populateTimelineYears(){ const ys=unique(scenarioTimelineYears()).sort(); const current=$('timelineYear').value; fillSelect('timelineYear',ys); if(ys.includes(+current)) $('timelineYear').value=current; }
function scenarioTimelineYears(){ return state.timeseries.filter(r=>r.scenario_id===state.current.scenario_id).map(r=>+r.year); }
function renderTimeline(){
  const year=+$('timelineYear').value, rows=scenarioTimeline(state.current.scenario_id,year);
  if(!rows.length){ $('timelineChart').innerHTML='<div class="empty-state">No time-series data stored for this season.</div>'; $('decisionList').innerHTML=''; return; }
  if(window.Highcharts) Highcharts.chart('timelineChart',{chart:{zoomType:'x'},title:{text:null},credits:{enabled:false},xAxis:{type:'datetime'},yAxis:[{title:{text:'Water (mm)'}},{title:{text:'Stress / probability'},opposite:true,min:0,max:1.5}],tooltip:{shared:true},series:[{type:'column',name:'Rain',data:rows.map(r=>[Date.parse(r.date),r.rain_mm]),yAxis:0},{type:'line',name:'Baseline irrigation',data:rows.map(r=>[Date.parse(r.date),r.baseline_irrigation_mm]),yAxis:0,step:'left'},{type:'line',name:'Forecast irrigation',data:rows.map(r=>[Date.parse(r.date),r.forecast_irrigation_mm]),yAxis:0,step:'left'},{type:'line',name:'3-day rain probability',data:rows.map(r=>[Date.parse(r.date),r.forecast_probability]),yAxis:1,dashStyle:'ShortDash'},{type:'line',name:'Crop water stress',data:rows.map(r=>[Date.parse(r.date),r.water_stress]),yAxis:1}]});
  const decisions=rows.filter(r=>r.decision_changed);
  $('decisionList').innerHTML=decisions.length?decisions.map(r=>`<div class="decision-card"><div><strong>${new Date(r.date).toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})}</strong><span class="good">Irrigation changed</span></div><div><strong>Baseline: ${r.baseline_irrigation_mm>0?`irrigate ${fmt(r.baseline_irrigation_mm,0)} mm`:'no irrigation'} · Forecast strategy: ${r.forecast_irrigation_mm>0?`irrigate ${fmt(r.forecast_irrigation_mm,0)} mm`:'wait'}</strong><p>${Math.round(r.forecast_probability*100)}% forecast probability of ≥${state.current.rain_threshold_mm} mm rain. ${r.actual_next3d_rain_mm!==null?`Observed next 3 days: ${fmt(r.actual_next3d_rain_mm,1)} mm.`:''} ${r.decision_note||''}</p></div></div>`).join(''):'<div class="empty-state">No irrigation decision changes in this season.</div>';
}
function renderCost(){ const c=costs(state.current); $('costSummary').innerHTML=[['Water saved',`${fmt(c.ml,1)} ML/season`],['Pump time avoided',`${fmt(c.hours,0)} hours/season`],['Energy avoided',`${fmt(c.kwh,0)} kWh/season`],['Energy saving',`$${fmt(c.energy,0)}/season`],['Water-cost saving',`$${fmt(c.water,0)}/season`],['Combined operating saving',`$${fmt(c.total,0)}/season`]].map(([a,b])=>`<div class="cost-item"><span>${a}</span><strong>${b}</strong></div>`).join(''); $('costFormula').innerHTML=`${fmt(state.current.mean_water_saved_mm,1)} mm × ${fmt(c.area,1)} ha × 0.01 = <strong>${fmt(c.ml,2)} ML saved</strong><br>${fmt(c.ml,2)} ML ÷ ${fmt(c.flow,0)} L/s = <strong>${fmt(c.hours,1)} pump hours</strong><br>${fmt(c.hours,1)} h × ${fmt(c.power,1)} kW = <strong>${fmt(c.kwh,0)} kWh</strong>`; }
function renderCompare(){ const a=state.pinnedA,b=state.pinnedB; $('compareEmpty').classList.toggle('hidden',!!(a&&b)); $('compareContent').classList.toggle('hidden',!(a&&b)); if(!(a&&b)) return; const card=(s,label)=>`<div class="compare-card"><h4>${label} · ${s.forecast_horizon_days}d / ${s.rain_threshold_mm}mm / ${Math.round(s.probability_threshold*100)}%</h4>${[['Water saved',`${fmt(s.mean_water_saved_mm,0)} mm (${fmt(s.mean_water_saved_pct,1)}%)`],['Forecast hold days',fmt(s.mean_hold_days||0,0)],['Irrigations avoided',fmt(s.mean_irrigations_avoided,1)],['Cane change',pct(s.mean_yield_change_pct)]].map(r=>`<div class="compare-row"><span>${r[0]}</span><strong>${r[1]}</strong></div>`).join('')}</div>`; $('compareContent').innerHTML=card(a,'Scenario A')+card(b,'Scenario B'); }
function switchTab(id){ document.querySelectorAll('.tab').forEach(x=>x.classList.toggle('active',x.dataset.tab===id)); document.querySelectorAll('.tab-panel').forEach(x=>x.classList.toggle('active',x.id===id)); setTimeout(()=>{ if(id==='overview') renderOverview(); if(id==='annual') renderAnnual(); if(id==='timeline') renderTimeline(); },20); }
loadData().catch(err=>{ console.error(err); $('datasetNotice').style.display='block'; $('datasetNotice').textContent='Could not load the scenario cube. Serve this folder through a web server rather than opening index.html directly.'; });
