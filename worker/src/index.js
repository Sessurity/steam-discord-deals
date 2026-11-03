// Direct Discord slash commands for ZHAXO, hosted in Cloudflare Workers.
// Discord HTTP signature (Ed25519) is verified for every incoming request.
const STEAM_QUERY="https://api.steampowered.com/IStoreQueryService/Query/v1/";
const APP_DETAILS="https://store.steampowered.com/api/appdetails";
const DISCORD="https://discord.com/api/v10";
const COVER="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/";
export const COMMANDS=[
 {type:1,name:"deals",description:"До 30 популярних знижок Steam",options:[
  {type:4,name:"discount",description:"Мінімальна знижка 70–100%",required:false,min_value:70,max_value:100}]},
 {type:1,name:"spore",description:"Ціна SPORE у Steam UA та RU"},
 {type:1,name:"find",description:"Пошук гри зі знижкою від 70%",options:[
  {type:3,name:"game",description:"Назва гри",required:true,min_length:2,max_length:80}]},
 {type:1,name:"help",description:"Команди ZHAXO"}
];
function bytes(hex){
 if(typeof hex!=="string"||hex.length%2||!/^[0-9a-f]+$/i.test(hex))return null;
 return Uint8Array.from(hex.match(/.{2}/g),x=>parseInt(x,16));
}
export async function verifySignature(raw,sig,stamp,pub){
 const s=bytes(sig),p=bytes(pub);
 if(!s||s.length!==64||!p||p.length!==32||!/^\d+$/.test(stamp||""))return false;
 try{
  const k=await crypto.subtle.importKey("raw",p,{name:"Ed25519"},false,["verify"]);
  return crypto.subtle.verify("Ed25519",k,s,new TextEncoder().encode(stamp+raw));
 }catch{return false;}
}
const asJson=(value,status=200)=>new Response(JSON.stringify(value),{status,headers:{"content-type":"application/json"}});
const safe=x=>String(x||"").replace(/[@*_\[\]~|]/g,"\\$&");
const money=(c,sym)=>Number.isInteger(c)&&c>=0?(c/100).toFixed(2).replace(".",",")+" "+sym:null;
export function parseItem(item){
 const o=item?.best_purchase_option||{},p=Number(o.discount_pct),id=Number(item?.appid||item?.id);
 const f=Number(o.final_price_in_cents),old=Number(o.original_price_in_cents);
 if(item?.type!==0||item.visible===false||!item.name||!Number.isInteger(id)||id<=0||
 !Number.isInteger(f)||f<0||!Number.isInteger(old)||old<=f||p<70||p>100)return null;
 const r=item.reviews?.summary_filtered||{},end=(o.active_discounts||[])
 .map(x=>Number(x.discount_end_date)).filter(x=>Number.isFinite(x)&&x>Date.now()/1000);
 return {appid:id,name:String(item.name),discount:p,final:f,original:old,
  reviews:Number(r.review_count)||0,positive:Number(r.percent_positive)||0,
  ends:end.length?Math.min(...end):null,price:money(f,"₴"),old_price:money(old,"₴")};
}
const score=g=>Math.log1p(g.reviews||0)*20+(g.positive||0)*0.2+g.discount*0.5;
export function choose(deals,limit=30,threshold=70){
 const all=[...deals].filter(g=>g&&g.discount>=threshold);
 const rank=(a,b)=>score(b)-score(a)||b.reviews-a.reviews||b.discount-a.discount||a.appid-b.appid;
 const popular=all.filter(g=>g.reviews>=5000).sort(rank);
 const others=all.filter(g=>g.reviews<5000).sort(rank);
 return popular.concat(others).slice(0,limit).sort((a,b)=>b.discount-a.discount||rank(a,b));
}
async function getJson(url){
 const r=await fetch(url,{headers:{accept:"application/json"},signal:AbortSignal.timeout(8000)});
 if(!r.ok)throw Error("Steam HTTP "+r.status);
 return r.json();
}
async function salesPage(start,threshold){
 const input={query:{start,count:500,sort:1,filters:{
  type_filters:{include_games:true,include_dlc:false},
  price_filters:{min_discount_percent:threshold}}},
  context:{language:"english",country_code:"UA",steam_realm:1},
  data_request:{include_basic_info:true,include_reviews:true,include_all_purchase_options:true}};
 const u=new URL(STEAM_QUERY);u.searchParams.set("input_json",JSON.stringify(input));
 const res=(await getJson(u.toString())).response||{};
 if(!Number.isInteger(res.metadata?.total_matching_records))throw Error("Steam response changed");
 return {total:res.metadata.total_matching_records,items:res.store_items||[]};
}
async function sales(threshold){
 const first=await salesPage(0,threshold);
 if(first.total>0&&!first.items.length)throw Error("Steam returned no games");
 const items=first.items.slice(),starts=[];
 if(first.total>20000)throw Error("Steam list too large");
 for(let pos=500;pos<first.total;pos+=500)starts.push(pos);
 // Workers Free supports up to six simultaneous outgoing connections.
 for(let i=0;i<starts.length;i+=4){
  const pages=await Promise.all(starts.slice(i,i+4).map(x=>salesPage(x,threshold)));
  for(const page of pages)items.push(...page.items);
 }
 const games=new Map();
 for(const item of items){const g=parseItem(item);if(g)games.set(g.appid,g);}
 return [...games.values()];
}
async function ruPrices(ids){
 const results=new Map(),unique=[...new Set(ids)];
 for(let i=0;i<unique.length;i+=15){
  const group=unique.slice(i,i+15),batches=[];
  for(let j=0;j<group.length;j+=5)batches.push(group.slice(j,j+5));
  const pages=await Promise.all(batches.map(async batch=>{
   const url=new URL(APP_DETAILS);url.searchParams.set("appids",batch.join(","));
   url.searchParams.set("cc","ru");url.searchParams.set("filters","price_overview");
   try{return await getJson(url.toString());}catch{return {};}
  }));
  for(const page of pages)for(const [id,item] of Object.entries(page)){
   const p=item?.data?.price_overview;
   if(item?.success&&p?.currency==="RUB"&&Number.isInteger(p.final)&&p.final>=0)
    results.set(Number(id),money(p.final,"₽"));
  }
 }
 return results;
}
export function card(g,number,ru){
 const url="https://store.steampowered.com/app/"+g.appid+"/?cc=ua";
 let desc="🇺🇦 **−"+g.discount+"%** · ~~"+g.old_price+"~~ → **"+g.price+"**";
 desc+="\n🇷🇺 Steam RU: **"+(ru||"ціна недоступна")+"**";
 if(g.ends)desc+="\n⏳ До "+new Date(g.ends*1000).toLocaleString("uk-UA",{timeZone:"Europe/Kyiv"});
 desc+="\n[Відкрити в Steam]("+url+")";
 return {title:String(number).padStart(2,"0")+" / "+safe(g.name).slice(0,160),
  url,description:desc,color:0x39CFC6,image:{url:COVER+g.appid+"/header.jpg"}};
}
export const PAGE_SIZE=5;

// Один Discord-відгук на одну slash-команду; кнопки змінюють саме його.
// Усі command responses — ephemeral: вони не засмічують публічний #deals.
export function pageView(games,ru=new Map(),title="ZHAXO / STEAM DEALS",page=0,threshold=70,owner="0"){
 const total=games.length;
 const maxPage=Math.max(1,Math.ceil(total/PAGE_SIZE));
 const current=Math.min(Math.max(0,page),maxPage-1);
 const begin=current*PAGE_SIZE;
 const visible=games.slice(begin,begin+PAGE_SIZE);
 const first=begin+1,last=begin+visible.length;
 const header={
  title:"⬡ ZHAXO  //  STEAM DEALS",
  color:0x39CFC6,
  description: total
   ? "**Добірка популярних ігор**\n🇺🇦 Steam UA · 🇷🇺 Steam RU\n"
     +"🔎 Від **−"+threshold+"%** · 🎮 **"+total+" ігор**\n"
     +"🔽 Від найбільшої знижки · сторінка **"+(current+1)+"/"+maxPage+"**"
   : "Наразі таких знижок не знайдено.",
  footer:{text:"ZHAXO • Steam Market Scanner · ціни підтверджує Steam"}
 };
 const embeds=[header,...visible.map((g,i)=>card(g,begin+i+1,ru.get(g.appid)))];
 if(embeds.reduce((sum,e)=>sum+e.title.length+e.description.length,0)>6000)
  throw Error("Too many Discord chars");
 const response={
  content:total?"⬡ **"+title+"** · **"+first+"–"+last+" із "+total+"**":null,
  embeds,allowed_mentions:{parse:[]},components:[]
 };
 if(maxPage>1){
  const id=p=>"zh:d:"+threshold+":"+p+":"+owner;
  response.components=[{type:1,components:[
   {type:2,style:2,label:"◀ Назад",custom_id:id(current-1),disabled:current===0},
   {type:2,style:2,label:(current+1)+" / "+maxPage,custom_id:"zh:page-indicator",disabled:true},
   {type:2,style:1,label:"Далі ▶",custom_id:id(current+1),disabled:current===maxPage-1}
  ]}];
 }
 return response;
}

async function spore(){
 const url=new URL(APP_DETAILS);url.searchParams.set("appids","17390");
 url.searchParams.set("cc","ua");url.searchParams.set("filters","price_overview");
 const [ua,ru]=await Promise.all([getJson(url.toString()),ruPrices([17390])]);
 const item=ua?.["17390"],info=item?.data?.price_overview;
 const valid=item?.success&&info?.currency==="UAH";
 const price=valid?money(info.final,"₴"):"ціна недоступна";
 return [{embeds:[{title:"★ SPORE · головний пріоритет",color:0xD6AF61,
  url:"https://store.steampowered.com/app/17390/Spore/",
  image:{url:COVER+"17390/header.jpg"},
  description:"🇺🇦 Steam UA: **"+price+"**"+(valid&&info.discount_percent?" · −"+info.discount_percent+"%":"")+
   "\n🇷🇺 Steam RU: **"+(ru.get(17390)||"ціна недоступна")+"**"}]}];
}
async function dealPage(threshold,page,owner){
 const games=choose(await sales(threshold),30,threshold);
 const index=Math.min(Math.max(0,page),Math.max(0,Math.ceil(games.length/PAGE_SIZE)-1));
 const shown=games.slice(index*PAGE_SIZE,(index+1)*PAGE_SIZE);
 const ru=await ruPrices(shown.map(g=>g.appid));
 return pageView(games,ru,"ZHAXO / ЗНИЖКИ ВІД −"+threshold+"%",index,threshold,owner);
}

async function commandPage(p){
 const name=p.data?.name,options=p.data?.options||[];
 const get=(id,fallback)=>options.find(o=>o.name===id)?.value??fallback;
 if(name==="spore")return (await spore())[0];
 if(name==="deals"){
  const pct=Number(get("discount",70));
  if(!Number.isInteger(pct)||pct<70||pct>100)throw Error("Invalid discount");
  const owner=p.member?.user?.id||p.user?.id||"0";
  return dealPage(pct,0,owner);
 }
 if(name==="find"){
  const term=String(get("game","")).trim().toLocaleLowerCase();
  if(term.length<2||term.length>80)throw Error("Invalid game");
  const games=choose((await sales(70)).filter(g=>g.name.toLocaleLowerCase().includes(term)),5);
  const ru=await ruPrices(games.map(g=>g.appid));
  return pageView(games,ru,"ZHAXO / ПОШУК",0,70,"0");
 }
 throw Error("Unknown command");
}

// Кнопки не створюють нових відповідей — змінюють поточну ephemeral-картку.
export function parsePageButton(customId){
 const m=/^zh:d:(\d{2,3}):(\d{1,2}):(\d{15,25})$/.exec(customId||"");
 if(!m)return null;
 const discount=Number(m[1]),page=Number(m[2]);
 return discount>=70&&discount<=100&&page>=0&&page<=5
  ?{discount,page,owner:m[3]}:null;
}

async function send(app,token,path,method,payload){
 const r=await fetch(DISCORD+"/webhooks/"+app+"/"+token+path,{
  method,headers:{"content-type":"application/json"},body:JSON.stringify(payload),
  signal:AbortSignal.timeout(10000)});
 if(!r.ok)throw Error("Discord HTTP "+r.status);
}
async function complete(p,button=null){
 try{
  const result=button
   ?await dealPage(button.discount,button.page,button.owner)
   :await commandPage(p);
  // Deferred interaction / button: редагуємо @original, без follow-up повідомлень.
  await send(p.application_id,p.token,"/messages/@original","PATCH",result);
 }catch(err){
  console.error("ZHAXO error",String(err));
  try{await send(p.application_id,p.token,"/messages/@original","PATCH",
   {content:null,components:[],embeds:[{
    title:"⚠️ Не вдалося перевірити Steam",
    description:"Спробуй пізніше — Steam або його API зараз недоступні.",
    color:0xD6AF61}]});}
  catch(e){console.error("Discord reply error",String(e));}
 }
}

export default {
 async fetch(req,env,ctx){
  if(new URL(req.url).pathname!=="/discord")return new Response("Not found",{status:404});
  if(req.method!=="POST")return new Response("Method not allowed",{status:405});
  if(!env.DISCORD_PUBLIC_KEY)return new Response("Public key missing",{status:503});
  const raw=await req.text();
  if(!await verifySignature(raw,req.headers.get("x-signature-ed25519"),
     req.headers.get("x-signature-timestamp"),env.DISCORD_PUBLIC_KEY))
   return new Response("Invalid Discord signature",{status:401});
  let p;try{p=JSON.parse(raw);}catch{return new Response("Bad JSON",{status:400});}
  if(p.type===1)return asJson({type:1});
  if(p.type!==2&&p.type!==3)
   return asJson({type:4,data:{content:"Непідтримувана команда.",flags:64}});
  if(env.DISCORD_CHANNEL_ID&&p.channel_id!==env.DISCORD_CHANNEL_ID)
   return asJson({type:4,data:{content:"Скористайся каналом #deals.",flags:64}});
  if(env.DISCORD_GUILD_ID&&p.guild_id!==env.DISCORD_GUILD_ID)
   return asJson({type:4,data:{content:"Сервер не налаштований.",flags:64}});
  const uid=p.member?.user?.id||p.user?.id;
  if(env.DISCORD_ALLOWED_USER_IDS){
   if(!env.DISCORD_ALLOWED_USER_IDS.split(",").map(x=>x.trim()).includes(uid))
    return asJson({type:4,data:{content:"Команди недоступні цьому користувачу.",flags:64}});
  }
  if(p.type===3){
   const button=parsePageButton(p.data?.custom_id);
   if(!button||button.owner!==uid)
    return asJson({type:4,data:{content:"Ця сторінка недоступна.",flags:64}});
   ctx.waitUntil(complete(p,button));
   return asJson({type:6}); // deferred UPDATE_MESSAGE, не створює нове повідомлення
  }
  if(p.data?.name==="help")return asJson({type:4,data:{flags:64,embeds:[{
   title:"⬡ ZHAXO · Команди",color:0x39CFC6,
   description:"**/deals** — 30 знижок від 70%\n**/deals discount:90** — від 90%\n**/spore** — ціна SPORE\n**/find game:назва** — пошук гри"}]}});
  ctx.waitUntil(complete(p));
  return asJson({type:5,data:{flags:64}}); // ephemeral: видно тільки автору
 }
};
