/* 지도는 선택만 전달한다. 상세 정보는 지도 아래의 Streamlit 영역에 표시한다. */
(()=>{
 const send=(type,extra={})=>window.parent.postMessage({isStreamlitMessage:true,type,...extra},'*');
 const status=document.getElementById('status');
 let map,cluster,initialized=false,serial=0,activeCode='',activeRequest='',lastResponse='';
 const points=new Map(),markers=new Map();
 function highlight(){
  markers.forEach((marker,code)=>{const element=marker.getElement();if(element)element.classList.toggle('selected',code===activeCode)});
 }
 function select(code){
  if(!points.has(code))return;
  activeCode=code;activeRequest=Date.now().toString(36)+'-'+(++serial);lastResponse='';highlight();
  status.textContent=points.get(code).name+' 물때를 불러오고 있어요. 상세 정보는 지도 아래에 표시돼요.';
  send('streamlit:setComponentValue',{value:{action:'select',station_id:code,request_id:activeRequest},dataType:'json'});
 }
 function receive(response){
  if(!response){
   if(lastResponse&&lastResponse===activeRequest){activeCode='';activeRequest='';lastResponse='';highlight();status.textContent='다른 깃발을 눌러 물때를 확인해 보세요.'}
   return;
  }
  // 지도 클릭 응답은 현재 요청만 반영한다. 목록·다시 불러오기 요청은 부모가 생성한다.
  const external=String(response.request_id).startsWith('list-');
  if(!external&&(response.request_id!==activeRequest||response.station_id!==activeCode))return;
  if(lastResponse===response.request_id)return;
  if(!points.has(response.station_id))return;
  activeCode=response.station_id;activeRequest=response.request_id;lastResponse=response.request_id;highlight();
  status.textContent=points.get(activeCode).name+' 선택 · '+(response.error?'조회에 실패했어요. 지도 아래에서 다시 시도해 주세요.':'지도 아래에서 물때를 확인해 주세요. 다른 깃발도 바로 선택할 수 있어요.');
 }
 function initialize(stations){
  if(typeof L==='undefined'||typeof L.markerClusterGroup!=='function'){
   status.textContent='지도를 불러오지 못했어요. 아래 지점 목록에서 물때를 확인해 주세요.';return;
  }
  // 휠 한 번에 과도하게 확대되지 않도록 기본 감도보다 완만하게 조절한다.
  map=L.map('map',{scrollWheelZoom:true,wheelPxPerZoomLevel:120,wheelDebounceTime:100,zoomAnimation:false,fadeAnimation:false}).setView([35.9,127.8],6);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:18,
   attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>'
  }).on('tileerror',()=>{status.textContent='배경 지도를 불러오지 못했어요. 공식 좌표의 깃발과 아래 목록은 사용할 수 있어요.'}).addTo(map);
  cluster=L.markerClusterGroup({showCoverageOnHover:false,maxClusterRadius:40,spiderfyOnMaxZoom:true,removeOutsideVisibleBounds:false});
  const icon=L.divIcon({className:'tide-flag',html:'<span class="flag-shape" aria-hidden="true"></span>',iconSize:[44,44],iconAnchor:[6,32]});
  stations.forEach(s=>{
   if(!Number.isFinite(s.latitude)||!Number.isFinite(s.longitude))return;
   points.set(s.id,s);
   const marker=L.marker([s.latitude,s.longitude],{icon,title:s.name,alt:s.name+' 물때 보기',keyboard:true});
   marker.bindTooltip(s.name,{direction:'top'});marker.on('click',()=>select(s.id));markers.set(s.id,marker);cluster.addLayer(marker);
  });
  map.addLayer(cluster);map.on('zoomend',highlight);
  if(markers.size)map.fitBounds(cluster.getBounds(),{padding:[20,20]});
  status.textContent='공식 예보지점 '+markers.size+'곳 · 휠로 확대·축소 · 숫자는 확대, 깃발은 물때 선택';
  // The Streamlit column can resize after the iframe has loaded.
  // Recalculate tile positions whenever the map element actually changes size.
  let resizeFrame=0;
  const refreshSize=()=>{
   cancelAnimationFrame(resizeFrame);
   resizeFrame=requestAnimationFrame(()=>map.invalidateSize({pan:false,animate:false}));
  };
  new ResizeObserver(refreshSize).observe(document.getElementById('map'));
  window.addEventListener('resize',refreshSize);
  refreshSize();
 }
 window.addEventListener('message',event=>{
  if(event.source!==window.parent||!event.data||event.data.type!=='streamlit:render')return;
  const args=event.data.args||{};
  if(!initialized){
   initialized=true;initialize(args.stations||[]);
   if(args.response){activeCode=args.response.station_id;activeRequest=args.response.request_id}
  }
  if(map)receive(args.response);
 });
 send('streamlit:componentReady',{apiVersion:1});send('streamlit:setFrameHeight',{height:425});
})();
