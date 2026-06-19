sleep=function(){return Promise.resolve();};addLog=function(m,c){};
renderBattle=function(){};renderActions=function(){};playBgm=function(){};
showSwitchPrompt=function(m){if(battleState)battleState.phase='switch';};
switchBattleTab=function(){};clearTurnTimer=function(){};startTurnTimer=function(){};checkServer=function(){};hideAllScreens=function(){};
const fs=require('fs');const W=JSON.parse(fs.readFileSync('/home/claude/ppo_weights.json'));
function mlp(obs){const[D,H1,H2,OUT]=W.arch;function L(v,Wf,bf,inN,outN,act){const o=new Array(outN);for(let j=0;j<outN;j++){let s=bf[j];for(let i=0;i<inN;i++)s+=v[i]*Wf[i*outN+j];o[j]=act?(s>0?s:0):s;}return o;}const a1=L(obs,W.W1,W.b1,D,H1,true),a2=L(a1,W.W2,W.b2,H1,H2,true);return L(a2,W.W3,W.b3,H2,OUT,false);}
function nnIdx(me,opp){const z=mlp(buildPpoObs(me,opp));let b=-1,bp=-1e9;me.moves.forEach((mv,i)=>{if(i<4&&mv.pp>0&&z[i]>bp){bp=z[i];b=i;}});return b>=0?b:0;}
function rngPick(a){return a[Math.floor(Math.random()*a.length)];}
function setup(){const t1=[],t2=[];while(t1.length<3){const p=rngPick(POKE_DB);if(p.allMoves&&p.allMoves.filter(m=>m.power>0).length>=2&&!t1.includes(p.id))t1.push(p.id);}while(t2.length<3){const p=rngPick(POKE_DB);if(p.allMoves&&p.allMoves.filter(m=>m.power>0).length>=2&&!t2.includes(p.id))t2.push(p.id);}
  const mk=(ids)=>ids.map(id=>buildBattlePoke(buildOptimizedAiPoke(POKE_DB.find(p=>p.id===id))));
  battleState={player:{party:mk(t1),active:0,revealed:[true,false,false]},ai:{party:mk(t2),active:0,revealed:[true,false,false]},turn:1,phase:'action',weather:null,weatherTurns:0,field:{stealthRockPlayer:false,stealthRockAi:false,spikesPlayer:0,spikesAi:0,toxicSpikesPlayer:0,toxicSpikesAi:0,stickyWebPlayer:false,stickyWebAi:false,reflectPlayer:0,reflectAi:0,lightScreenPlayer:0,lightScreenAi:0,auroraVeilPlayer:0,auroraVeilAi:0,trickRoom:0,tailwindPlayer:0,tailwindAi:0,terrain:null,terrainTurns:0},_pivotPending:false};}
// NN이 player. ai는 heuristic(hard)
async function nnAsPlayer(){aiDifficulty='hard';setup();let win=null;showResult=function(w){win=w?'player':'ai';battleState=null;};let g=0;
  while(battleState&&!battleState._ended&&g<250){g++;if(battleState.phase==='switch'){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;battleState.phase='action';continue;}
    let act=battleState.player.party[battleState.player.active];if(!act||act.currentHp<=0){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;act=battleState.player.party[a];}
    await playerMove(nnIdx(act,battleState.ai.party[battleState.ai.active]));}
  return win==='player';}
// NN이 ai. player는 heuristic(hard) → playerMove에 heuristic 선택 주입
async function nnAsAi(){aiDifficulty='hard';setup();let win=null;showResult=function(w){win=w?'player':'ai';battleState=null;};
  const origAi=aiPickMove;aiPickMove=function(me,opp){return me.moves[nnIdx(me,opp)]||me.moves[0];};let g=0;
  while(battleState&&!battleState._ended&&g<250){g++;if(battleState.phase==='switch'){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;battleState.phase='action';continue;}
    let act=battleState.player.party[battleState.player.active];if(!act||act.currentHp<=0){const a=battleState.player.party.findIndex(p=>p.currentHp>0);if(a===-1)break;battleState.player.active=a;act=battleState.player.party[a];}
    // player = heuristic
    aiDifficulty='hard';const hm=origAi(act,battleState.ai.party[battleState.ai.active]);let idx=act.moves.findIndex(m=>m.name===hm.name&&m.pp>0);if(idx<0)idx=0;
    await playerMove(idx);}
  aiPickMove=origAi;return win==='ai';}
(async()=>{const N=parseInt(process.argv[2]||'150');let w1=0,d1=0,w2=0,d2=0;
  for(let i=0;i<N;i++){try{if(await nnAsPlayer())w1++;d1++;}catch(e){}}
  for(let i=0;i<N;i++){try{if(await nnAsAi())w2++;d2++;}catch(e){}}
  const avg=Math.round((w1/d1+w2/d2)/2*100);
  console.error(`NN vs heuristic | player측 ${Math.round(w1/d1*100)}% + ai측 ${Math.round(w2/d2*100)}% → 평균 ${avg}%`);})();
