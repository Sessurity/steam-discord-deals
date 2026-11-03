import {test} from "node:test";
import assert from "node:assert/strict";
import {webcrypto} from "node:crypto";
import {COMMANDS,verifySignature,parseItem,choose,pageView} from "./src/index.js";
test("valid Discord Ed25519 signature",async()=>{
 const pair=await webcrypto.subtle.generateKey("Ed25519",true,["sign","verify"]);
 const pub=Buffer.from(await webcrypto.subtle.exportKey("raw",pair.publicKey)).toString("hex");
 const data='{"type":1}',stamp="1793700000";
 const sig=Buffer.from(await webcrypto.subtle.sign("Ed25519",pair.privateKey,new TextEncoder().encode(stamp+data))).toString("hex");
 assert.equal(await verifySignature(data,sig,stamp,pub),true);
 assert.equal(await verifySignature("tampered",sig,stamp,pub),false);
});
test("Steam parse keeps only valid discounts",()=>{
 const i={type:0,id:123,name:"Game",best_purchase_option:{discount_pct:90,final_price_in_cents:"1000",original_price_in_cents:"10000"}};
 assert.equal(parseItem(i).discount,90);
 assert.equal(parseItem({...i,type:4}),null);
 assert.equal(parseItem({...i,best_purchase_option:{...i.best_purchase_option,discount_pct:30}}),null);
});
test("popular selection then descending discount",()=>{
 const famous=Array.from({length:30},(_,i)=>({appid:i+1,name:"Known",discount:70+i%5,reviews:10000+i*100,positive:95}));
 const indie=Array.from({length:60},(_,i)=>({appid:i+1000,name:"Unknown",discount:99,reviews:10,positive:100}));
 const selected=choose(indie.concat(famous));
 assert.equal(selected.length,30);
 assert(selected.every(g=>g.appid<1000));
 assert.deepEqual(selected.map(g=>g.discount),selected.map(g=>g.discount).sort((a,b)=>b-a));
});
test("single message with five cards, header, and buttons",()=>{
 const games=Array.from({length:30},(_,i)=>({appid:i+1,name:"Game",discount:90,price:"50 ₴",old_price:"500 ₴"}));
 const first=pageView(games,new Map([[1,"400,00 ₽"]]),"ZHAXO",0,90,"123456789012345678");
 assert.equal(first.embeds.length,6);
 assert.equal(first.components.length,1);
 assert.equal(first.components[0].components[0].disabled,true);
 assert(first.embeds[1].description.includes("400,00 ₽"));
 const last=pageView(games,new Map(),"ZHAXO",5,90,"123456789012345678");
 assert(last.embeds[1].title.startsWith("26"));
 assert.equal(last.components[0].components[2].disabled,true);
});
test("all desired slash commands",()=>assert.deepEqual(COMMANDS.map(x=>x.name),["deals","spore","find","help"]));
