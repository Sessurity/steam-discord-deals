import { COMMANDS } from "./src/index.js";
const token=process.env.DISCORD_BOT_TOKEN,app=process.env.DISCORD_APP_ID,guild=process.env.DISCORD_GUILD_ID;
const missing=[];
if(!token)missing.push("DISCORD_BOT_TOKEN: GitHub Actions secret");
if(!/^\d{15,25}$/.test(app||""))missing.push("DISCORD_APP_ID: Application ID (not Public Key)");
if(!/^\d{15,25}$/.test(guild||""))missing.push("DISCORD_GUILD_ID: Discord Server ID (not Channel ID)");
if(missing.length){
 console.error("Registration configuration incomplete:");
 for(const field of missing)console.error("- "+field);
 console.error("Start a NEW Run workflow from the GitHub website and fill both input fields; RE-RUN does not prompt for new inputs.");
 process.exit(1);
}
const url="https://discord.com/api/v10/applications/"+app+"/guilds/"+guild+"/commands";
const res=await fetch(url,{method:"PUT",headers:{authorization:"Bot "+token,"content-type":"application/json"},body:JSON.stringify(COMMANDS)});
if(!res.ok){console.error("Discord HTTP "+res.status);process.exit(1);}
const items=await res.json();
console.log("Registered slash commands: "+items.map(x=>"/"+x.name).join(", "));
