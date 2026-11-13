const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'../dist/annotate.js'),'utf8');
const translations=fs.readFileSync(path.join(__dirname,'../dist/i18n-annotate.js'),'utf8');
const settle=async()=>{for(let i=0;i<16;i++)await Promise.resolve();};

function harness({result=true}={}){
  const clock={now:1_000_000};
  const sent=[],listeners={},intervals=[];let ops=[];
  const context={globalAlpha:1,strokeStyle:'',fillStyle:'',lineWidth:1,lineCap:'',lineJoin:'',
    setTransform(){},clearRect(){ops=[];},beginPath(){},moveTo(){},lineTo(){},closePath(){},arc(){},
    stroke(){ops.push({op:'stroke',alpha:this.globalAlpha,color:this.strokeStyle,width:this.lineWidth});},
    fill(){ops.push({op:'fill',alpha:this.globalAlpha,color:this.fillStyle});}};
  function node(tag){
    const attributes={};
    return {tagName:tag.toUpperCase(),id:'',className:'',textContent:'',title:'',type:'',hidden:false,dataset:{},children:[],listeners:{},width:300,height:150,scrollLeft:0,scrollTop:0,
      style:{setProperty(name,value){this[name]=value;}},
      setAttribute(name,value){attributes[name]=String(value);},getAttribute(name){return attributes[name]??null;},
      addEventListener(name,callback){this.listeners[name]=callback;},appendChild(child){this.children.push(child);},append(...children){this.children.push(...children);for(const child of children)child.parentElement=this;},
      after(...nodes){this.parentElement.children.push(...nodes);},getContext(){return context;},
      setPointerCapture(){},hasPointerCapture(){return true;},releasePointerCapture(){},
      getBoundingClientRect(){return {left:0,top:0,width:640,height:400};}};
  }
  const surface=node('div'),actions=node('span'),remote=node('canvas'),pointer=node('button');
  remote.width=640;remote.height=400;remote.parentElement=surface;actions.append(pointer);
  const byId={'remote-canvas':remote,'remote-pointer':pointer};
  const head=node('head');
  const invoke=(command,args)=>{
    if(command!=='remote_draw')throw Error(`Unexpected native command ${command}`);
    sent.push(JSON.parse(JSON.stringify(args)));
    return result instanceof Error?Promise.reject(result):Promise.resolve(result);
  };
  vm.runInNewContext(script,{Date:{now:()=>clock.now},
    window:{__TAURI__:{core:{invoke}},devicePixelRatio:2,setInterval(callback){intervals.push(callback);}},
    document:{head,getElementById:id=>byId[id]??null,createElement:node,addEventListener(name,callback){(listeners[name]??=[]).push(callback);}}});
  const find=id=>[...actions.children,...surface.children].find(item=>item.id===id)??actions.children.flatMap(item=>item.children).find(item=>item.id===id);
  const layer=find('remote-draw-layer');
  const state=(extra={})=>{for(const callback of listeners['mixel-remote-state']??[])callback({detail:{phase:'active',session:'room-1',sharing:false,control:false,peer_features:['draw'],...extra}});};
  const at=(x,y,pointerId=1)=>({clientX:x,clientY:y,pointerId,button:0,preventDefault(){}});
  return {clock,sent,head,find,layer,state,surface,
    get ops(){return ops;},
    click:id=>find(id).listeners.click(),
    down:(x,y)=>layer.listeners.pointerdown(at(x,y)),move:(x,y)=>layer.listeners.pointermove(at(x,y)),up:(x,y)=>layer.listeners.pointerup(at(x,y)),
    async tick(ms=50){clock.now+=ms;intervals[0]();await settle();},
    strokes:()=>sent.map(item=>item.message)};
}

test('the pen appears only for an active viewing session whose host can show drawings, also without control',async()=>{
  const h=harness();
  const toggle=h.find('remote-draw'),bar=h.find('remote-draw-tools');
  assert.equal(toggle.hidden,true);
  h.state({peer_features:['clip2','sound']});assert.equal(toggle.hidden,true,'host without draw');
  h.state({sharing:true,peer_features:['draw']});assert.equal(toggle.hidden,true,'this computer is the one shared');
  h.state({phase:'negotiating'});assert.equal(toggle.hidden,true);
  h.state();assert.equal(toggle.hidden,false,'view-only session');
  assert.equal(bar.hidden,true);assert.equal(h.layer.dataset.drawing,'false');
  h.click('remote-draw');
  assert.equal(toggle.textContent,'Draw: on');assert.equal(toggle.getAttribute('aria-pressed'),'true');
  assert.equal(bar.hidden,false);assert.equal(h.layer.dataset.drawing,'true');assert.equal(h.layer.hidden,false);
  assert.equal(h.find('remote-draw-pen').getAttribute('aria-pressed'),'true');
  assert.equal(h.find('remote-draw-color-0').getAttribute('aria-pressed'),'true');
  assert.match(h.head.children[0].textContent,/\.draw-layer/);
  h.click('remote-draw');assert.equal(bar.hidden,true);assert.equal(h.layer.dataset.drawing,'false');
  // Without pen mode the layer lets clicks through to the remote picture: nothing is drawn.
  h.down(100,100);await h.tick();assert.deepEqual(h.sent,[]);
});

test('strokes are batched into remote_draw messages of at most 64 points and end with done',async()=>{
  const h=harness();h.state();h.click('remote-draw');
  h.down(64,40);await h.tick(0);
  assert.deepEqual(h.sent,[{session:'room-1',message:{kind:'stroke',id:1,tool:'pen',color:0,width:2,points:[[6554,6554]],done:false}}]);
  h.move(64,40);h.move(64.5,40);await h.tick();assert.equal(h.sent.length,1,'no movement, nothing sent');
  for(let x=70;x<70+100;x++)h.move(x,50);
  assert.equal(h.sent.length,1,'points wait for the next batch');
  await h.tick();
  const batch=h.strokes().slice(1);
  assert.deepEqual(batch.map(message=>message.points.length),[64,36]);
  assert.ok(batch.every(message=>message.kind==='stroke'&&message.id===1&&!message.done));
  assert.deepEqual(batch[0].points[0],[Math.round(70/640*65535),Math.round(50/400*65535)]);
  h.move(300,60);h.up(300,60);await h.tick(0);
  const last=h.strokes().at(-1);
  assert.deepEqual(last,{kind:'stroke',id:1,tool:'pen',color:0,width:2,points:[[Math.round(300/640*65535),Math.round(60/400*65535)]],done:true});
  // The next stroke has its own id; a tap is one message.
  h.down(10,10);h.up(10,10);await h.tick(0);
  assert.deepEqual(h.strokes().slice(-1),[{kind:'stroke',id:2,tool:'pen',color:0,width:2,points:[[1024,1638]],done:false}].map(message=>({...message,done:true})));
  assert.ok(h.sent.every(item=>item.session==='room-1'));
});

test('tool, colour and size apply to the next stroke; an arrow sends only its moving end',async()=>{
  const h=harness();h.state();h.click('remote-draw');
  h.click('remote-draw-arrow');h.click('remote-draw-color-3');h.click('remote-draw-width');
  assert.equal(h.find('remote-draw-arrow').getAttribute('aria-pressed'),'true');
  assert.equal(h.find('remote-draw-pen').getAttribute('aria-pressed'),'false');
  assert.equal(h.find('remote-draw-color-3').getAttribute('aria-pressed'),'true');
  assert.equal(h.find('remote-draw-width').textContent,'Size: 3');
  h.down(0,0);for(let x=10;x<=200;x+=10)h.move(x,100);await h.tick();
  h.move(320,200);h.up(320,200);await h.tick(0);
  assert.deepEqual(h.strokes(),[
    {kind:'stroke',id:1,tool:'arrow',color:3,width:3,points:[[0,0],[20480,16384]],done:false},
    {kind:'stroke',id:1,tool:'arrow',color:3,width:3,points:[[32768,32768]],done:true},
  ]);
  h.click('remote-draw-width');h.click('remote-draw-width');assert.equal(h.find('remote-draw-width').textContent,'Size: 1');
  h.click('remote-draw-highlighter');h.down(5,5);h.up(5,5);await h.tick(0);
  assert.deepEqual(h.strokes().at(-1),{kind:'stroke',id:2,tool:'highlighter',color:3,width:1,points:[[512,819]],done:true});
});

test('strokes are drawn here too, the highlighter see-through, and the drawing fades like on the host',async()=>{
  const h=harness();h.state();h.click('remote-draw');
  h.down(10,10);h.move(200,10);h.up(200,10);
  h.click('remote-draw-highlighter');h.click('remote-draw-color-1');
  h.down(10,100);h.move(200,100);h.up(200,100);
  h.click('remote-draw-arrow');h.down(10,200);h.move(300,200);h.up(300,200);
  await h.tick();
  assert.deepEqual(h.ops.map(op=>[op.op,op.alpha,op.color]),[['stroke',1,'#E5322D'],['stroke',0.35,'#FFD400'],['stroke',1,'#FFD400'],['fill',1,'#FFD400']]);
  assert.equal(h.ops[0].width,5*400/1080,'size 2 on a picture shown 400 high');
  assert.equal(h.layer.width,1280,'device pixels');assert.equal(h.layer.style.width,'640px');
  h.click('remote-draw');
  await h.tick(5000);
  assert.ok(h.ops.length===4&&h.ops[0].alpha<1&&h.ops[0].alpha>0.9,'starts to fade 5 s after the last stroke');
  await h.tick(250);
  const fading=h.ops[0].alpha;assert.ok(fading>0.4&&fading<0.65,String(fading));
  await h.tick(500);
  assert.deepEqual(h.ops,[]);assert.equal(h.layer.hidden,true,'faded and cleared');
  // Undo after the fade still tells the host (it keeps its own timing).
  h.click('remote-draw');h.click('remote-draw-undo');await h.tick(0);
  assert.deepEqual(h.strokes().at(-1),{kind:'undo'});
});

test('undo removes the last stroke and clear removes all, here and on the remote computer',async()=>{
  const h=harness();h.state();h.click('remote-draw');
  for(const y of [20,60,100]){h.down(10,y);h.move(100,y);h.up(100,y);}
  await h.tick();assert.equal(h.ops.length,3);
  h.click('remote-draw-undo');await h.tick();
  assert.equal(h.ops.length,2);assert.deepEqual(h.strokes().at(-1),{kind:'undo'});
  // Undo while drawing ends the stroke being drawn.
  h.down(10,150);h.move(100,150);h.click('remote-draw-undo');h.move(120,150);await h.tick();
  assert.equal(h.ops.length,2);
  assert.deepEqual(h.strokes().slice(-2).map(message=>[message.kind,message.id,message.done]),[['stroke',4,true],['undo',undefined,undefined]],'the remote computer undoes the same stroke');
  h.click('remote-draw-clear');await h.tick();
  assert.deepEqual(h.ops,[]);assert.deepEqual(h.strokes().at(-1),{kind:'clear'});
});

test('a host whose picture contains the strokes (drawpic) shows finished strokes from the picture',async()=>{
  const h=harness();h.state({peer_features:['draw','drawpic']});h.click('remote-draw');
  h.down(10,10);h.move(100,10);h.up(100,10);
  h.down(10,50);h.move(100,50);await h.tick();
  assert.equal(h.ops.length,2);
  await h.tick(1500);
  assert.equal(h.ops.length,1,'only the stroke still being drawn is drawn here');
  h.up(100,50);await h.tick(1600);assert.deepEqual(h.ops,[]);
  h.click('remote-draw-undo');await h.tick(0);assert.deepEqual(h.strokes().at(-1),{kind:'undo'});
});

test('a new session or the end of the session drops the drawing and the pen mode',async()=>{
  const h=harness({result:false});h.state();h.click('remote-draw');
  h.down(10,10);h.move(100,10);await h.tick();assert.equal(h.ops.length,1);
  h.state({session:'room-2'});await h.tick();
  assert.deepEqual(h.ops,[]);assert.equal(h.find('remote-draw').textContent,'Draw: off');
  h.move(200,10);h.up(200,10);await h.tick();
  assert.ok(h.sent.every(item=>item.session==='room-1'),'nothing of the old stroke goes to the new session');
  h.click('remote-draw');h.down(10,10);await h.tick(0);
  assert.equal(h.sent.at(-1).session,'room-2');
  h.state({phase:'closing'});assert.equal(h.find('remote-draw').hidden,true);assert.equal(h.find('remote-draw-tools').hidden,true);
  const before=h.sent.length;h.down(10,10);await h.tick();assert.equal(h.sent.length,before);
});

test('a failing native command does not break drawing',async()=>{
  const h=harness({result:Error('The drawing transport is busy')});h.state();h.click('remote-draw');
  h.down(10,10);h.up(10,10);h.down(20,20);h.up(20,20);await h.tick();
  assert.equal(h.sent.length,2);assert.equal(h.ops.length,2);
});

test('the drawing texts are added to the German, French and Italian tables without replacing existing ones',()=>{
  const tables={de:{texts:{Undo:'Schon übersetzt'},patterns:[]},fr:{texts:{},patterns:[]},it:{texts:{},patterns:[]}};
  vm.runInNewContext(translations,{window:{MIXEL_I18N:tables},Object});
  assert.equal(tables.de.texts['Draw: off'],'Zeichnen: aus');
  assert.equal(tables.de.texts.Undo,'Schon übersetzt');
  assert.equal(tables.fr.texts.Highlighter,'Surligneur');
  assert.equal(tables.it.texts['Size: 4'],'Dimensione: 4');
  const source=fs.readFileSync(path.join(__dirname,'../annotate.ts'),'utf8');
  for(const text of ['Draw: off','Drawing tools','Pen','Highlighter','Arrow','Undo','Clear','Line width','Red','Yellow','Green','Blue','White']){
    assert.ok(source.includes(`'${text}'`),text);
    for(const language of ['de','fr','it'])assert.ok(tables[language].texts[text],`${language}: ${text}`);
  }
  for(const text of ['Draw: on','Size: 1','Size: 2','Size: 3','Size: 4']){
    for(const language of ['de','fr','it'])assert.ok(tables[language].texts[text],`${language}: ${text}`);
  }
  vm.runInNewContext(translations,{window:{},Object});
});
