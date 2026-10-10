export const SIZE=1200;
export function hash(x,z){const n=Math.sin(x*127.1+z*311.7)*43758.5453;return n-Math.floor(n);}
const smooth=t=>t*t*(3-2*t);
export function noise(x,z){const a=Math.floor(x),b=Math.floor(z),u=smooth(x-a),v=smooth(z-b);return (hash(a,b)*(1-u)+hash(a+1,b)*u)*(1-v)+(hash(a,b+1)*(1-u)+hash(a+1,b+1)*u)*v;}
export function height(x,z){
 let f=0,amp=1,total=0;for(let i=0;i<5;i++){f+=noise(x*.004*2**i+17,z*.004*2**i-5)*amp;total+=amp;amp*=.48;}
 const river=60*Math.sin(z*.006)+20*Math.sin(z*.018);
 const valley=1-Math.exp(-(((x-river)/85)**2));
 const edge=Math.max(0,Math.min(1,(600-Math.max(Math.abs(x),Math.abs(z)))/65));
 return -5+valley*(30+220*(f/total)**1.65)*smooth(edge);
}
export function position(i,time){const phase=i*2.399963+time*(.028+(i%5)*.002),r=105+(i%9)*34;const x=Math.sin(phase)*r,z=Math.cos(phase*.87+i)*r;return {x,y:Math.max(80,height(x,z)+30)+Math.sin(time*.4+i)*6,z};}
export function seeded(seed=12345){return()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};}
