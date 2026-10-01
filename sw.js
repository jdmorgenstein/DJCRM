const C='abp-v1';
const FILES=['./','index.html','style.css','app.js','data.json','manifest.webmanifest','icon-192.png','icon-512.png'];
self.addEventListener('install',e=>{self.skipWaiting();e.waitUntil(caches.open(C).then(c=>c.addAll(FILES)).catch(()=>{}))});
self.addEventListener('activate',e=>{e.waitUntil(caches.keys().then(k=>Promise.all(k.filter(x=>x!==C).map(x=>caches.delete(x)))).then(()=>self.clients.claim()))});
self.addEventListener('fetch',e=>{
 if(e.request.method!=='GET')return;
 e.respondWith(caches.match(e.request,{ignoreSearch:true}).then(r=>r||fetch(e.request).then(res=>{
   const cp=res.clone();caches.open(C).then(c=>c.put(e.request,cp)).catch(()=>{});return res;
 }).catch(()=>caches.match('index.html'))));
});
