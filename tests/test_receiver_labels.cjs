const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const path=require('node:path');
const root=path.resolve(__dirname,'..');
const viewer=fs.readFileSync(path.join(root,'raspberry-viewer/viewer.js'),'utf8');
const html=fs.readFileSync(path.join(root,'src/aor_dv10/web/static/index.html'),'utf8');
const node=()=>({children:[],hidden:false,append(...values){this.children.push(...values)},appendChild(value){this.children.push(value)},replaceChildren(){this.children=[]}});
const c=vm.createContext({document:{createElement:node}});
vm.runInContext(viewer.slice(viewer.indexOf('function renderStationLabel('),viewer.indexOf('const frequency =')),c);
for(const [bank,name] of [['Bank 1','DEMO ONE'],['Bank 4 / local','DEMO TWO']]){
 const el=node();c.renderStationLabel(el,bank+' → '+name,{bank_name:bank,memory_name:name});
 assert.equal(el.children.length,1);
 const rows=el.children[0].children;
 assert.equal(rows.length,2);
 assert.equal(rows[0].children[1].textContent,bank);
 assert.equal(rows[1].children[1].textContent,name);
}
assert(!html.includes("replaceAll(' / ', '\\n\\n')"));
for(const match of html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)) if(match[1].trim()) new vm.Script(match[1]);
new vm.Script(viewer);
console.log('PASS one bank/name pair, literal slash retained, control and listening scripts parse');
