import { COMMANDS } from "./src/index.js";
const token=process.env.DISCORD_BOT_TOKEN,app=process.env.DISCORD_APP_ID,guild=process.env.DISCORD_GUILD_ID;
if(!token||!app||!guild||!/^\d+$/.test(app)||!/^\d+$/.test(guild)){
 console.error("Need DISCORD_BOT_TOKEN, DISCORD_APP_ID and DISCORD_GUILD_ID");
 process.exit(1);
}
const url="https://discord.com/api/v10/applications/"+app+"/guilds/"+guild+"/commands";
const res=await fetch(url,{method:"PUT",headers:{authorization:"Bot "+token,"content-type":"application/json"},body:JSON.stringify(COMMANDS)});
if(!res.ok){console.error("Discord HTTP "+res.status);process.exit(1);}
const items=await res.json();
console.log("Registered slash commands: "+items.map(x=>"/"+x.name).join(", "));
