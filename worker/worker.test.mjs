import {test} from "node:test";
import assert from "node:assert/strict";
import {webcrypto} from "node:crypto";
import {COMMANDS,verifySignature,parseItem,choose,pages} from "./src/index.js";
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
test("30 games fit 3 Discord messages, regional RUB never guessed",()=>{
 const games=Array.from({length:30},(_,i)=>({appid:i+1,name:"Test",discount:90,price:"50 ₴",old_price:"500 ₴"}));
 const out=pages(games,new Map([[1,"400,00 ₽"]]));
 assert.deepEqual(out.map(x=>x.embeds.length),[10,10,10]);
 assert(out[0].embeds[0].description.includes("400,00 ₽"));
 assert(out[0].embeds[1].description.includes("ціна недоступна"));
});
test("all desired slash commands",()=>assert.deepEqual(COMMANDS.map(x=>x.name),["deals","spore","find","help"]));
