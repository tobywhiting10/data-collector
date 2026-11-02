DASH_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bussage lights - admin</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mut:#667085;--line:#e3e6ec;--acc:#c0392b;--ok:#1e8e3e;--warn:#b26a00}
@media(prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--fg:#e8ebf1;--mut:#9aa4b5;--line:#2a313d;--acc:#ff6b5b;--ok:#4cc26d;--warn:#f0b24a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,sans-serif}
main{max-width:920px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 12px}h2{font-size:16px;margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:16px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.k b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.k span{color:var(--mut);font-size:12px}
.k.main{border-color:var(--acc)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:14px}
form{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
input{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit;min-width:0}
input[type=number]{width:120px}input.note{flex:1;min-width:140px}
button{padding:9px 14px;border:0;border-radius:8px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}
button.x{background:none;color:var(--mut);padding:2px 8px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:6px 4px;border-top:1px solid var(--line)}th{color:var(--mut);font-weight:500;font-size:12px;border:0}
td.n{text-align:right;font-variant-numeric:tabular-nums}.msg{min-height:1.2em;font-size:13px}.ok{color:var(--ok)}.err{color:var(--acc)}.warn{color:var(--warn)}
.small{color:var(--mut);font-size:12px}
nav{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}.tab{background:var(--card);color:var(--fg);border:1px solid var(--line)}.tab.on{background:var(--acc);color:#fff;border-color:var(--acc)}
select{padding:9px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}[hidden]{display:none!important}
</style></head><body><main>
<h1>Bussage lights - admin</h1>
<nav><button class="tab on" id="tab-ov">Overview</button><button class="tab" id="tab-tx">Transaction log</button><button class="tab" id="tab-yaml">Song buttons YAML</button></nav>
<div id="pg-ov">
<div class="grid" id="kpis"></div>
<div id="warn" class="msg warn"></div>

<section><h2>Settings</h2>
<form id="f-set">
<label class="small">To CRUK %<br><input type="number" step="0.01" min="0" max="100" id="s-charity_percent" required></label>
<label class="small">To project %<br><input type="number" id="s-project" disabled></label>
<label class="small">Card fee %<br><input type="number" step="0.01" min="0" max="20" id="s-card_fee_percent" required></label>
<label class="small">Card fee fixed (p)<br><input type="number" step="1" min="0" max="100" id="s-card_fee_fixed_pence" required></label>
<label class="small">Coffee fee %<br><input type="number" step="0.01" min="0" max="30" id="s-bmc_fee_percent" required></label>
<label class="small">Coffee fee fixed (p)<br><input type="number" step="1" min="0" max="100" id="s-bmc_fee_fixed_pence" required></label>
<button style="align-self:end">Save and refresh</button></form>
<div class="msg" id="m-set"></div>
<div class="small">Card split applies to card money after fees. Saving re-reads the CRUK page and recalculates every total (past payments included).</div></section>

<section><h2>Money moved from the card reader to CRUK</h2>
<div class="small" style="margin-bottom:8px">Enter the amount of <b>each transfer you make</b> (e.g. this week's £60), <b>not</b> a running total. The page adds them up; delete a row to correct a mistake.</div>
<form id="f-tr"><input type="number" step="0.01" min="0.01" placeholder="£ amount" required id="tr-amt">
<input type="date" id="tr-date"><input class="note" placeholder="note (optional)" id="tr-note" maxlength="120"><button>Add transfer</button></form>
<div class="msg" id="m-tr"></div><table id="t-tr"></table></section>

<section><h2>Buy Me a Coffee</h2>
<div class="small" style="margin-bottom:8px">Coffees arrive automatically by webhook. Use this form only for ones the webhook cannot see (e.g. monthly/membership renewals, or anything from before the webhook was set up). Delete a row to correct a mistake.</div>
<form id="f-bmc"><input type="number" step="0.01" min="0.01" placeholder="£ amount" required id="bmc-amt">
<input type="date" id="bmc-date"><input class="note" placeholder="note (optional)" id="bmc-note" maxlength="120"><button>Add</button></form>
<div class="msg" id="m-bmc"></div><table id="t-bmc"></table></section>

<section><h2>CRUK page total (manual override)</h2>
<form id="f-cruk"><input type="number" step="0.01" min="0" placeholder="£ total incl. Gift Aid" required id="cruk-amt"><button>Set</button></form>
<div class="small" id="cruk-info"></div>
<div class="small">Normally read automatically every hour; a manual value is overwritten at the next successful read.</div></section>
<div class="small" id="foot"></div>
</div>
<div id="pg-tx" hidden><section><h2>Transaction log</h2>
<div class="small">Every card payment (reported one by one by Home Assistant) and every Buy Me a Coffee entry (webhook or manual). Fees are estimates from the current settings.</div>
<form><select id="tx-filter"><option value="">All</option><option value="card">Card only</option><option value="coffee">Coffee only</option></select><span class="small" id="tx-count"></span></form>
<div style="overflow-x:auto"><table id="t-tx"></table></div></section></div>
<div id="pg-yaml" hidden><section><h2>Song buttons for the Home Assistant dashboard</h2>
<div class="small">Generated from songs.yaml. Copy a category block into a column's <code>cards:</code> list.</div>
<form><button type="button" id="y-load">Refresh</button><button type="button" id="y-copy">Copy all</button><span class="small" id="y-msg"></span></form>
<textarea id="y-text" readonly style="width:100%;height:60vh;font:12px/1.3 monospace;background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px"></textarea></section></div>
</main><script>
const $=id=>document.getElementById(id), P=n=>"£"+Number(n).toFixed(2), today=new Date().toISOString().slice(0,10);
$("tr-date").value=$("bmc-date").value=today;
async function api(m,u,b){const r=await fetch(u,{method:m,headers:{"Content-Type":"application/json"},body:b?JSON.stringify(b):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(typeof j.detail==="string"?j.detail:"Rejected: check the values");return j}
function rows(t,items,del){t.innerHTML="<tr><th>Date</th><th class=n>Amount</th><th>Note</th><th></th></tr>";
 for(const i of items){const tr=t.insertRow();tr.insertCell().textContent=i.date;const a=tr.insertCell();a.className="n";a.textContent=P(i.amount);
  tr.insertCell().textContent=i.note||"";const b=document.createElement("button");b.className="x";b.textContent="delete";
  b.onclick=async()=>{if(confirm("Delete "+P(i.amount)+" on "+i.date+"?")){await api("DELETE",del+i.id);load()}};tr.insertCell().appendChild(b)}}
async function load(){const s=await api("GET","/dash/api/state"),m=s.summary;
 const K=[["Amount to be paid to CRUK",m.amount_to_pay_cruk,1],["Total (CRUK + card - moved)",m.total,1],["CRUK page only",m.cruk_only],["Card total (gross)",m.card_total],["Card to charity (90%)",m.card_charity],
  ["Card to project (10%)",m.card_project],["Moved to CRUK",m.moved_to_cruk],
  ["Buy Me a Coffee (last 2 months)",m.bmc_last_2_months],["Project total",m.project_total]];
 const c=m.settings;for(const k in c){const e=$("s-"+k);if(e&&document.activeElement!==e&&!e.dataset.dirty)e.value=c[k]}showProj();
 $("kpis").innerHTML=K.map(k=>`<div class="k ${k[2]?"main":""}"><span>${k[0]}</span><b>${P(k[1])}</b></div>`).join("");
 $("warn").textContent=m.card_charity_pending<0?"Moved to CRUK is more than the charity share of card money taken - check the transfers.":"";
 rows($("t-tr"),s.transfers,"/dash/api/transfers/");rows($("t-bmc"),s.bmc,"/dash/api/bmc/");
 $("cruk-info").textContent="Current: "+P(s.cruk.total)+" (raised "+P(s.cruk.raised)+" + Gift Aid "+P(s.cruk.gift_aid)+"), source: "+(s.cruk.origin||"never read")+(s.cruk.updated_at?", updated "+s.cruk.updated_at:"");
 $("foot").textContent="Card payments: "+s.card_payments+" - plays counted: "+s.total_plays+" - data as of "+s.generated_at}
function showProj(){const v=parseFloat($("s-charity_percent").value);$("s-project").value=isNaN(v)?"":Math.round((100-v)*100)/100}
for(const e of document.querySelectorAll("#f-set input"))e.oninput=()=>{e.dataset.dirty=1;showProj()};
$("f-set").onsubmit=async e=>{e.preventDefault();const m=$("m-set");m.className="msg";m.textContent="Saving and reading CRUK...";
 const g=k=>Number($("s-"+k).value);
 try{const r=await api("POST","/dash/api/settings",{charity_percent:g("charity_percent"),card_fee_percent:g("card_fee_percent"),card_fee_fixed_pence:g("card_fee_fixed_pence"),bmc_fee_percent:g("bmc_fee_percent"),bmc_fee_fixed_pence:g("bmc_fee_fixed_pence")});
  for(const i of document.querySelectorAll("#f-set input"))delete i.dataset.dirty;
  m.className="msg "+(r.cruk_refreshed?"ok":"warn");m.textContent=r.cruk_refreshed?"Saved. CRUK figure refreshed and totals recalculated.":"Saved and totals recalculated, but: "+r.cruk_error;load()}
 catch(x){m.className="msg err";m.textContent=x.message}};
const TABS=["ov","tx","yaml"];
function show(t){for(const x of TABS){$("pg-"+x).hidden=x!==t;$("tab-"+x).classList.toggle("on",x===t)}
 try{location.hash=t}catch(e){} if(t==="tx")loadTx(); if(t==="yaml"&&!$("y-text").value)loadYaml()}
for(const x of TABS)$("tab-"+x).onclick=()=>show(x);
async function loadTx(){const f=$("tx-filter").value,d=await api("GET","/dash/api/transactions");const t=$("t-tx");
 t.innerHTML="<tr><th>When</th><th>Type</th><th>Source</th><th class=n>Amount</th><th class=n>Est. fee</th><th class=n>Est. net</th><th>Reference</th></tr>";
 let n=0;for(const x of d.transactions){if(f&&x.type!==f)continue;n++;const r=t.insertRow();
  const w=x.when.length>10?x.when.replace("T"," ").slice(0,16):x.when;
  [w,x.type==="card"?"Card":"Coffee",x.source].forEach(v=>r.insertCell().textContent=v);
  [x.amount,x.est_fee,x.est_net].forEach(v=>{const c=r.insertCell();c.className="n";c.textContent=P(v)});r.insertCell().textContent=x.ref}
 $("tx-count").textContent=n+" shown"+(d.count>d.shown?" (latest "+d.shown+" of "+d.count+")":"")}
$("tx-filter").onchange=loadTx;
async function loadYaml(){$("y-msg").textContent="Loading...";try{const r=await fetch("/api/v1/songs/buttons.yaml");$("y-text").value=await r.text();$("y-msg").textContent=""}catch(e){$("y-msg").textContent="Failed: "+e.message}}
$("y-load").onclick=loadYaml;
$("y-copy").onclick=async()=>{try{await navigator.clipboard.writeText($("y-text").value);$("y-msg").textContent="Copied"}catch(e){$("y-text").select();$("y-msg").textContent="Press Ctrl+C to copy"}};
function wire(f,msg,url,body){$(f).onsubmit=async e=>{e.preventDefault();const m=$(msg);m.className="msg";m.textContent="";
 try{await api("POST",url,body());m.className="msg ok";m.textContent="Saved";e.target.reset();$("tr-date").value=$("bmc-date").value=today;load()}catch(x){m.className="msg err";m.textContent=x.message}}}
wire("f-tr","m-tr","/dash/api/transfers",()=>({amount:$("tr-amt").value,date:$("tr-date").value,note:$("tr-note").value}));
wire("f-bmc","m-bmc","/dash/api/bmc",()=>({amount:$("bmc-amt").value,date:$("bmc-date").value,note:$("bmc-note").value}));
wire("f-cruk","m-bmc","/dash/api/cruk",()=>({total:$("cruk-amt").value}));
if(TABS.includes(location.hash.slice(1)))show(location.hash.slice(1));
load().catch(e=>{$("warn").textContent="Could not load: "+e.message});setInterval(()=>load().catch(()=>{}),60000);
</script></body></html>"""
