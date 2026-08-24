'use strict';
function startApp(){

const TAIPEI_LAT=25.0330, TAIPEI_LON=121.5654;
const CATS={
  US_EO:  {label:'美國商用光學衛星',sublabel:'Vantor/Maxar · Planet SkySat',color:'#4488FF'},
  CN_COMM:{label:'中國商用光學衛星',sublabel:'SuperView · 高分 · 吉林',  color:'#FF9800'},
  CN_MIL: {label:'中國軍用偵察衛星',sublabel:'遙感 Yaogan',                        color:'#F44336'},
  TW_TASA:{label:'台灣 TASA 衛星',  sublabel:'Formosat-5 / -7 / -8',              color:'#00E5FF'},
  STARLINK:{label:'Starlink 星鏈',  sublabel:'SpaceX · Gen1/Gen2/V2 Mini',         color:'#A855F7'},
};

let viewer=null, satDs=null, circleDs=null, bordersDs=null;
let coverageData=null, passesData=null;
let activePanelTab='overview';
let activeCatFilter=null;
let _plotted={};   // 各類別圖上實繪顆數（與分類概況面板同步）

// ── Timeline state ─────────────────────────────────────────────────────────
let _tlMin=0;            // minutes offset from now (0=now, negative=past)
let _tlDebounce=null;
let _autoTimer=null;
let _coverageCtrl=null;   // AbortController for in-flight coverage fetch
let _passesCtrl=null;     // AbortController for in-flight passes fetch
let _loading=false;       // prevents concurrent _loadForTs calls
let _pendingReload=false; // 載入期間的時間軸操作 → 排隊於完成後重載（避免默默丟棄）
let _lastAction=0;        // rate-limit timestamp for buttons
const _TL_MAX=43200;     // 30 days in minutes

// ── Clock ──────────────────────────────────────────────────────────────────
function _cst(d){
  return new Date(d.getTime()+8*3600*1000).toISOString().replace('T',' ').slice(0,19)+' CST';
}
document.getElementById('clock').textContent=_cst(new Date());
setInterval(function(){document.getElementById('clock').textContent=_cst(new Date());},1000);

// ── Cesium init ────────────────────────────────────────────────────────────
async function initCesium(){
  Cesium.Ion.defaultAccessToken=(window.CESIUM_ION_TOKEN||'');

  viewer=new Cesium.Viewer('cesiumContainer',{
    // Initialise directly in 2D — no morph transition, no drift
    sceneMode:           Cesium.SceneMode.SCENE2D,
    animation:           false,
    timeline:            false,
    baseLayerPicker:     false,
    imageryProvider: new Cesium.TileMapServiceImageryProvider({
      url:'/cesium/Assets/Textures/NaturalEarthII/',
      fileExtension:'jpg',
      credit:'Natural Earth II',
    }),
    terrainProvider:     new Cesium.EllipsoidTerrainProvider(),
    sceneModePicker:     false,
    infoBox:             false,
    geocoder:            false,
    homeButton:          false,
    navigationHelpButton:false,
    selectionIndicator:  false,
    fullscreenButton:    false,
  });

  viewer.cesiumWidget.creditContainer.style.display='none';
  // Freeze clock — prevents time-driven camera updates that cause 2D drift
  viewer.clock.shouldAnimate=false;

  // setView() is synchronous — unlike flyTo() it does NOT start an animation
  // that can drift the camera position after load.
  viewer.camera.setView({
    destination:Cesium.Rectangle.fromDegrees(100.0,9.0,144.0,41.0),
  });

  // Disable rotation and tilt — meaningless in 2D and can cause drift
  viewer.scene.screenSpaceCameraController.enableRotate=false;
  viewer.scene.screenSpaceCameraController.enableTilt=false;

  satDs=new Cesium.CustomDataSource('satellites');
  circleDs=new Cesium.CustomDataSource('circle');
  await viewer.dataSources.add(satDs);
  await viewer.dataSources.add(circleDs);
  _drawCircle();

  // 渲染防護：Cesium 遇幾何錯誤（如 RangeError）會停止渲染，
  // 這裡移除肇事的國界圖層並重啟 render loop，避免整頁死掉。
  viewer.scene.renderError.addEventListener(function(_scene,error){
    console.error('Cesium 渲染錯誤，嘗試自動恢復:',error);
    try{
      if(bordersDs){viewer.dataSources.remove(bordersDs,true);bordersDs=null;}
    }catch(_e){/* noop */}
    setTimeout(function(){
      try{viewer.useDefaultRenderLoop=true;}catch(_e){/* noop */}
    },200);
  });

  document.getElementById('map-loading').style.display='none';
}

function _drawCircle(){
  const R=6371.0, cover=2000.0, ar=cover/R, n=128;
  const lat0=Cesium.Math.toRadians(TAIPEI_LAT);
  const lon0=Cesium.Math.toRadians(TAIPEI_LON);
  const pts=[];
  for(let i=0;i<=n;i++){
    const b=(i/n)*2*Math.PI;
    const la=Math.asin(Math.sin(lat0)*Math.cos(ar)+Math.cos(lat0)*Math.sin(ar)*Math.cos(b));
    const lo=lon0+Math.atan2(Math.sin(b)*Math.sin(ar)*Math.cos(lat0),
                             Math.cos(ar)-Math.sin(lat0)*Math.sin(la));
    pts.push(Cesium.Cartesian3.fromRadians(lo,la));
  }
  circleDs.entities.add({polyline:{positions:pts,width:2,
    material:new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#FFD600').withAlpha(0.75)),
    arcType:Cesium.ArcType.NONE}});
  circleDs.entities.add({position:Cesium.Cartesian3.fromDegrees(TAIPEI_LON,TAIPEI_LAT),
    point:{pixelSize:9,color:Cesium.Color.fromCssColorString('#FFD600'),
           outlineColor:Cesium.Color.BLACK,outlineWidth:1.5}});
}

// ── Data loading ───────────────────────────────────────────────────────────
async function loadCoverage(ts=null){
  if(_coverageCtrl) _coverageCtrl.abort();
  _coverageCtrl=new AbortController();
  const url=ts?'/api/taipei_coverage_at?ts='+encodeURIComponent(ts):'/api/taipei_coverage';
  try{
    const r=await fetch(url,{signal:_coverageCtrl.signal});
    if(!r.ok) throw new Error('HTTP '+r.status);
    coverageData=await r.json();
    _updateMarkers();
    _updateStatus();
    if(activePanelTab==='overview') renderOverview();
  }catch(e){
    if(e.name==='AbortError') return;
    console.warn('Coverage error',e);
  }
}

let _passesStepSec=900;  // 預設 15 分鐘步長

function updateStepLabel(v){
  document.getElementById('step-label').textContent=v>=60?'60 分':v+' 分';
}

function applyStepChange(){
  const stepMin=parseInt(document.getElementById('step-slider').value);
  _passesStepSec=stepMin*60;
  loadPasses();
}

async function loadPasses(ts=null){
  if(_passesCtrl) _passesCtrl.abort();
  _passesCtrl=new AbortController();
  let url=ts
    ?'/api/taipei_passes_at?ts='+encodeURIComponent(ts)+'&step_sec='+_passesStepSec
    :'/api/taipei_passes?step_sec='+_passesStepSec;
  try{
    const r=await fetch(url,{signal:_passesCtrl.signal});
    if(!r.ok) throw new Error('HTTP '+r.status);
    const d=await r.json();
    if(d.status==='computing'){
      // 後端正在計算，2秒後重試
      setTimeout(()=>loadPasses(ts),2000);
      return;
    }
    passesData=d;
    if(activePanelTab==='passes') renderPasses();
  }catch(e){
    if(e.name==='AbortError') return;
    console.warn('Passes error',e);
  }
}

function refreshAll(){
  const now=Date.now();
  if(now-_lastAction<1000) return;
  _lastAction=now;
  clearTimeout(_tlDebounce);
  clearTimeout(_autoTimer);
  _loadForTs();
}

// ── Markers ────────────────────────────────────────────────────────────────
function _updateMarkers(){
  if(!coverageData||!satDs) return;
  satDs.entities.suspendEvents();
  satDs.entities.removeAll();
  _plotted={};
  Object.entries(coverageData.categories).forEach(([catId,cd])=>{
    if(activeCatFilter&&activeCatFilter!==catId){_plotted[catId]=0;return;}
    const col=Cesium.Color.fromCssColorString(cd.color);
    // 超過 300 顆時只畫可見衛星，避免大星座（如 Starlink）拖慢 2D 地圖
    const allSats=cd.satellites||[];
    const satsToPlot=allSats.length>300?allSats.filter(s=>s.visible):allSats;
    _plotted[catId]=satsToPlot.length;
    satsToPlot.forEach(s=>{
      satDs.entities.add({
        id:'sat_'+s.norad_id,
        position:Cesium.Cartesian3.fromDegrees(s.lon,s.lat,0),
        point:{
          pixelSize:s.visible?8:5,
          color:s.visible?col:col.withAlpha(0.4),
          outlineColor:s.visible?Cesium.Color.WHITE.withAlpha(0.85):Cesium.Color.WHITE.withAlpha(0.2),
          outlineWidth:s.visible?1.5:0.5,
        },
        label:{
          text:s.name,
          font:'11px Tahoma,sans-serif',
          fillColor:Cesium.Color.WHITE,
          outlineColor:Cesium.Color.BLACK,
          outlineWidth:2,
          style:Cesium.LabelStyle.FILL_AND_OUTLINE,
          pixelOffset:new Cesium.Cartesian2(0,-14),
          horizontalOrigin:Cesium.HorizontalOrigin.CENTER,
          verticalOrigin:Cesium.VerticalOrigin.BOTTOM,
          distanceDisplayCondition:new Cesium.DistanceDisplayCondition(0,4.6e6),
          disableDepthTestDistance:Number.POSITIVE_INFINITY,
        },
      });
    });
  });
  satDs.entities.resumeEvents();
  _updateStatus();
  if(activePanelTab==='overview') renderOverview();
}

// ── Category filter ────────────────────────────────────────────────────────
function toggleCatFilter(catId){
  activeCatFilter=(activeCatFilter===catId)?null:catId;
  document.querySelectorAll('.leg-row[data-cat]').forEach(el=>{
    el.classList.toggle('muted',activeCatFilter!==null&&el.dataset.cat!==activeCatFilter);
  });
  _updateMarkers();   // 內部會同步重繪分類概況（含 active 樣式與圖上顆數）
}

// ── Panel tabs ─────────────────────────────────────────────────────────────
function switchPanelTab(tab){
  activePanelTab=tab;
  document.querySelectorAll('.ptab').forEach(el=>{
    el.classList.toggle('active',el.dataset.tab===tab);
  });
  const ctrl=document.getElementById('step-control');
  if(ctrl) ctrl.style.display=tab==='passes'?'flex':'none';
  if(tab==='overview') renderOverview();
  else renderPasses();
}

// ── Overview panel ─────────────────────────────────────────────────────────
function renderOverview(){
  const body=document.getElementById('panel-body');
  body.innerHTML='';
  if(!coverageData){body.innerHTML="<div class='pass-empty'>資料載入中...</div>";return;}
  Object.entries(CATS).forEach(([catId,cfg])=>{
    const cd=coverageData.categories[catId];
    if(!cd) return;
    // 圖上實繪數：與 _updateMarkers 相同邏輯推算，避免面板與地圖不同步
    const nAll=cd.count||0;
    const plotted=(catId in _plotted)?_plotted[catId]
                  :(nAll>300?cd.visible_count:nAll);
    const truncated=nAll>300;
    const filtered=activeCatFilter!==null&&activeCatFilter!==catId;
    const card=document.createElement('div');
    card.className='cat-card'+(activeCatFilter===catId?' active':'');
    card.dataset.cat=catId;
    card.innerHTML=
      "<div class='cat-header'><div class='cat-dot' style='background:"+cfg.color+"'></div>"
      +"<span class='cat-label'>"+cfg.label+"</span>"
      +"<span class='cat-cnt'>"+nAll+" 顆</span></div>"
      +"<div class='cat-sublabel'>"+cfg.sublabel+"</div>"
      +"<div class='cat-stats'>"
      +"<div class='cstat'><div class='sv' style='color:"+cfg.color+"'>"+nAll+"</div><div class='sl'>資料庫</div></div>"
      +"<div class='cstat'><div class='sv' style='color:#4CAF50'>"+cd.visible_count+"</div><div class='sl'>可見</div></div>"
      +"<div class='cstat'><div class='sv' style='color:#FFD600'>"+plotted+"</div><div class='sl'>圖上</div></div>"
      +"</div>"
      +(filtered?"<div class='cat-note'>已被篩選隱藏</div>"
        :(truncated?"<div class='cat-note'>大星座僅繪台北可見衛星</div>":""));
    card.addEventListener('click',()=>toggleCatFilter(catId));
    body.appendChild(card);
  });
}

// ── Pass panel ─────────────────────────────────────────────────────────────
function renderPasses(){
  const body=document.getElementById('panel-body');
  body.innerHTML='';
  if(!passesData){
    body.innerHTML="<div class='pass-empty'>過頂預報載入中（10–30 秒）...</div>";
    return;
  }
  const catIds=activeCatFilter?[activeCatFilter]:Object.keys(CATS);
  catIds.forEach(catId=>{
    const cfg=CATS[catId];
    const cd=passesData.categories[catId];
    if(!cd) return;
    const hdr=document.createElement('div');
    hdr.className='pass-cat-hdr';
    hdr.innerHTML="<span style='width:8px;height:8px;border-radius:50%;background:"+cfg.color+";display:inline-block'></span>"+cfg.label;
    body.appendChild(hdr);
    const passes=cd.passes||[];
    if(!passes.length){
      const e=document.createElement('div');
      e.className='pass-empty';
      e.textContent='24 小時內無過頂記錄';
      body.appendChild(e); return;
    }
    passes.slice(0,8).forEach(p=>{
      const item=document.createElement('div');
      item.className='pass-item';
      const rT=new Date(p.t_rise_utc);
      const elCol=p.max_el_deg>=45?'#4CAF50':p.max_el_deg>=20?'#FF9800':'#8b949e';
      const dm=Math.floor(p.duration_s/60), ds_=p.duration_s%60;
      const ts=new Date(rT.getTime()+8*3600*1000).toISOString().replace('T',' ').slice(5,16);
      item.innerHTML=
        "<div class='pass-name'>"+p.name+"</div>"
        +"<div class='pass-row'>"
        +"<span class='pass-time'>&#8599; "+ts+" CST</span>"
        +"<span class='pass-el' style='color:"+elCol+"'>Max "+p.max_el_deg+"&deg;</span>"
        +"</div>"
        +"<div class='pass-dur'>持續 "+dm+"m "+ds_+"s</div>";
      body.appendChild(item);
    });
  });
}

// ── Status bar ─────────────────────────────────────────────────────────────
function _updateStatus(){
  if(!coverageData) return;
  let tot=0,vis=0,plot=0;
  Object.entries(coverageData.categories).forEach(([catId,c])=>{
    tot+=c.count||0;vis+=c.visible_count||0;
    plot+=(catId in _plotted)?_plotted[catId]
          :((c.count||0)>300?c.visible_count||0:c.count||0);
  });
  document.getElementById('st-total').textContent=tot;
  document.getElementById('st-visible').textContent=vis;
  const sp=document.getElementById('st-plotted');
  if(sp) sp.textContent=plot;
  const t=new Date(coverageData.timestamp);
  document.getElementById('st-time').textContent=
    new Date(t.getTime()+8*3600*1000).toISOString().replace('T',' ').slice(11,19)+' CST';
}

// ── Timeline ───────────────────────────────────────────────────────────────
function _tlTs(){
  // Returns ISO string for current slider offset, or null if "now"
  if(Math.abs(_tlMin)<5) return null;
  return new Date(Date.now()+_tlMin*60*1000).toISOString();
}

function _tlUpdateDisplay(){
  const ts=new Date(Date.now()+_tlMin*60*1000);
  document.getElementById('tl-time').textContent=_cst(ts);
  const badge=document.getElementById('tl-badge');
  if(Math.abs(_tlMin)<5){
    badge.textContent='現在'; badge.className='tl-badge now';
  } else if(_tlMin<0){
    badge.textContent='歷史'; badge.className='tl-badge hist';
  } else {
    badge.textContent='預測'; badge.className='tl-badge future';
  }
}

function tlSliderInput(v){
  // Live display update while dragging — no API call yet
  _tlMin=parseInt(v);
  _tlUpdateDisplay();
  clearTimeout(_tlDebounce);
}

function tlSliderChange(v){
  // Mouseup / touch end: trigger data load with 500ms debounce
  _tlMin=parseInt(v);
  _tlUpdateDisplay();
  clearTimeout(_tlDebounce);
  clearTimeout(_autoTimer);
  _tlDebounce=setTimeout(()=>_loadForTs(),500);
}

function jumpToNow(){
  const now=Date.now();
  if(now-_lastAction<1000) return;
  _lastAction=now;
  _tlMin=0;
  document.getElementById('tl-slider').value=0;
  _tlUpdateDisplay();
  clearTimeout(_tlDebounce);
  clearTimeout(_autoTimer);
  _loadForTs();
}

async function _loadForTs(){
  if(_loading){_pendingReload=true;return;}
  _loading=true;
  const ts=_tlTs();
  const ldEl=document.getElementById('tl-loading');
  if(ldEl) ldEl.textContent='載入中...';
  try{
    await Promise.all([loadCoverage(ts), loadPasses(ts)]);
  }finally{
    _loading=false;
  }
  if(ldEl) ldEl.textContent='';
  if(_pendingReload){
    // 載入期間使用者又拉動了時間軸 → 立即以最新滑桿位置重載
    _pendingReload=false;
    _loadForTs();
    return;
  }
  // Schedule next auto-refresh only when at "now"
  if(!ts){
    clearTimeout(_autoTimer);
    _autoTimer=setTimeout(()=>_loadForTs(),60000);
  }
}

// ── Borders layer (2D) ─────────────────────────────────────────────────────
async function loadBorders(){
  try{
    const ds=await Cesium.GeoJsonDataSource.load('/api/layers/borders');
    // GeoJSON 多邊形在 Cesium 預設走 RHUMB 弧型細分，超大國界多邊形會觸發
    // 「RangeError: Too many properties to enumerate」並中止渲染。
    // 防護：多邊形一律轉為 GEODESIC 折線外框，並隱藏原多邊形（與 globe.js 同法）。
    const lineColor=Cesium.Color.fromCssColorString('#FFD600').withAlpha(0.7);
    [...ds.entities.values].forEach(ent=>{
      if(ent.polygon){
        const hier=ent.polygon.hierarchy&&ent.polygon.hierarchy.getValue(Cesium.JulianDate.now());
        if(hier&&hier.positions&&hier.positions.length){
          ds.entities.add({polyline:{
            positions:[...hier.positions,hier.positions[0]],
            width:1.5,
            material:new Cesium.ColorMaterialProperty(lineColor),
            arcType:Cesium.ArcType.GEODESIC,
          }});
        }
        ent.polygon.show=new Cesium.ConstantProperty(false);
      }
      if(ent.polyline&&ent.polyline.arcType===undefined){
        ent.polyline.arcType=Cesium.ArcType.GEODESIC;
      }
      if(ent.label) ent.label.show=new Cesium.ConstantProperty(false);
      if(ent.billboard) ent.billboard.show=new Cesium.ConstantProperty(false);
    });
    bordersDs=ds;
    await viewer.dataSources.add(ds);
  }catch(e){
    console.warn('Borders load failed',e);
  }
}

// ── Init ───────────────────────────────────────────────────────────────────
async function init(){
  await initCesium();
  loadBorders();         // fire-and-forget
  _tlUpdateDisplay();    // init timeline display
  await loadCoverage();
  loadPasses();
  // Start 60-second auto-refresh for "now" mode
  _autoTimer=setTimeout(()=>_loadForTs(),60000);
}

window.tlSliderInput=tlSliderInput;
window.tlSliderChange=tlSliderChange;
window.jumpToNow=jumpToNow;

init().catch(e=>{
  document.getElementById('map-loading').textContent='初始化失敗: '+e.message;
  console.error(e);
});

window.switchPanelTab=switchPanelTab;
window.toggleCatFilter=toggleCatFilter;
window.refreshAll=refreshAll;

} // end startApp
