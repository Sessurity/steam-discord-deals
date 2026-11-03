import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import worker from "./src/index.js";

async function fixtures() {
 const pair=await webcrypto.subtle.generateKey("Ed25519",true,["sign","verify"]);
 const publicKey=Buffer.from(await webcrypto.subtle.exportKey("raw",pair.publicKey)).toString("hex");
 const timestamp="1793700000";
 const interact=async (data,env={},onTask=()=>{})=>{
  const body=JSON.stringify(data);
  const signature=Buffer.from(await webcrypto.subtle.sign("Ed25519",pair.privateKey,
   new TextEncoder().encode(timestamp+body))).toString("hex");
  const request=new Request("https://worker.example/discord",{
   method:"POST",body,headers:{"content-type":"application/json",
    "x-signature-ed25519":signature,"x-signature-timestamp":timestamp}});
  const result=await worker.fetch(request,{DISCORD_PUBLIC_KEY:publicKey,...env},{
   waitUntil:onTask
  });
  return [result.status,await result.json()];
 };
 return interact;
}

test("/help is ephemeral and returns immediately",async()=>{
 const interact=await fixtures();
 const [status,data]=await interact({type:2,data:{name:"help"}});
 assert.equal(status,200);
 assert.equal(data.type,4);
 assert.equal(data.data.flags,64);
 assert(data.data.embeds[0].description.includes("/deals"));
});

test("/deals uses one deferred ephemeral reply; pagination edits, no new followups",async()=>{
 const interact=await fixtures();
 const original=globalThis.fetch,requests=[],promises=[];
 globalThis.fetch=async(url,options={})=>{
  requests.push({url:String(url),method:options.method||"GET"});
  if(String(url).includes("IStoreQueryService"))return new Response(JSON.stringify({
   response:{metadata:{total_matching_records:0},store_items:[]}}),{status:200});
  if(String(url).includes("/webhooks/"))return new Response("{}",{status:200});
  throw Error("Unexpected request "+url);
 };
 try{
  const app="1566753146590724136",user="123456789012345678";
  const base={application_id:app,token:"TEST_INTERACTION",
   channel_id:"987654321098765432",member:{user:{id:user}}};
  const [status,data]=await interact({type:2,data:{name:"deals"},...base},{},p=>promises.push(p));
  assert.equal(status,200);
  assert.equal(data.type,5);
  assert.equal(data.data.flags,64);
  await Promise.all(promises.splice(0));
  assert.equal(requests.filter(x=>x.method==="PATCH").length,1);
  assert.equal(requests.filter(x=>x.method==="POST").length,0);

  const [status2,data2]=await interact({type:3,data:{custom_id:"zh:d:70:1:"+user},...base},
   {},p=>promises.push(p));
  assert.equal(status2,200);
  assert.equal(data2.type,6);
  await Promise.all(promises.splice(0));
  assert.equal(requests.filter(x=>x.method==="PATCH").length,2);
  assert.equal(requests.filter(x=>x.method==="POST").length,0);

  const [status3,data3]=await interact({type:3,data:{custom_id:"zh:d:70:1:"+user},
    ...base,member:{user:{id:"999999999999999999"}}});
  assert.equal(status3,200);
  assert.equal(data3.type,4);
  assert.equal(data3.data.flags,64);
 }finally{globalThis.fetch=original;}
});
