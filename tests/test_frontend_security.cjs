const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function client() {
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      innerHTML: '', textContent: '', value: '', style: {}, hidden: false, open: false,
      classList: {toggle(){}, remove(){}, add(){}}, addEventListener(){},
      setAttribute(){}, querySelectorAll(){return []}, append(){}, replaceChildren(){}, focus(){},
      showModal(){this.open=true}, close(){this.open=false},
    });
    return elements.get(id);
  };
  const sent = [];
  const context = vm.createContext({
    document: {getElementById: element, querySelectorAll:()=>[], addEventListener(){},
      body:{classList:{remove(){},toggle(){}}}, createElement:()=>element('created')},
    window: {addEventListener(){}}, location:{search:'',pathname:'/'},
    URLSearchParams, Headers, AbortSignal, setTimeout, clearTimeout, innerWidth:1440,
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},
    fetch:async (path,options)=>{sent.push({path, options}); return {ok:true,json:async()=>({auth_required:true,tasks:[],model:'Test'})}},
  });
  vm.runInContext(fs.readFileSync('static/workspace.js','utf8'),context);
  return {context,element,sent};
}

test('Chinese model step title cannot create an HTML node',()=>{
  const {context,element}=client();
  vm.runInContext(`state.steps=[{title:'中文<img src=x onerror="alert(1)">',status:'pending',substeps:[]}];renderTask();`,context);
  assert.ok(!element('timeline').innerHTML.includes('<img'));
  assert.ok(element('timeline').innerHTML.includes('&lt;img'));
});

test('API helper sends workspace key without putting it in URL',async()=>{
  const {context,sent}=client();
  await vm.runInContext(`accessKey='unit-test-key'; api('/tasks');`,context);
  const request=sent.find(item=>item.path==='/tasks' && new Headers(item.options.headers).has('X-API-Key'));
  assert.equal(new Headers(request?.options.headers).get('X-API-Key'),'unit-test-key');
  assert.ok(!request.path.includes('unit-test-key'));
});
