import {PenView} from '../../src/views/PenView';
import SocratesPenPlugin from '../../src/main';
import {DEFAULT_SETTINGS} from '../../src/settings';
import {setLang} from '../../src/i18n';
import {TFile,FileSystemAdapter} from 'obsidian';

const win=window as any;
const sessions=new Map<string,any>();
const streams=new Map<string,{body:any; controller:ReadableStreamDefaultController; emit:(ev:any)=>void}>();
const events=new Map<string,Set<Function>>();
const file=new TFile();
const app:any={workspace:{
  on(name:string,fn:Function){if(!events.has(name))events.set(name,new Set());events.get(name)!.add(fn);return()=>events.get(name)!.delete(fn);},
  getActiveFile:()=>file,getLeavesOfType:()=>[],
},vault:{adapter:new FileSystemAdapter(),getAbstractFileByPath:()=>file}};
let nextSession=0;
const createSession=(hid:string)=>{
  const session={session_id:`session-${++nextSession}`,handbook_id:hid,chips:[],ui_messages:[],dyn_chips:[],has_substantive:false,spend:{}};
  sessions.set(session.session_id,session);return structuredClone(session);
};
const encoder=new TextEncoder();
const response=(body:any)=>new Response(JSON.stringify(body),{headers:{'Content-Type':'application/json'}});
win.fetch=async (url:string,init:any={})=>{
  const path=new URL(url,'http://localhost').pathname;
  const body=JSON.parse(init.body||'{}');
  if(path==='/v1/health')return response({status:'ok',version:'0.28.0',capabilities:{big_bang:true},llm:{ok:true,base_url:DEFAULT_SETTINGS.baseUrl,model:DEFAULT_SETTINGS.model,key_source:'sidecar',key_tail:'test'}});
  if(path==='/v1/llm/preflight')return response({base:{ok:true,code:'',message:''}});
  if(path==='/v1/handbooks/import')return response({handbook_id:body.handbook_id});
  if(path==='/v1/sessions')return response(createSession(body.handbook_id));
  if(path.endsWith('/snapshots'))return response({undo_n:0,redo_n:0,can_undo:false,can_redo:false,revision:'initial'});
  if(path.endsWith('/cancel')){
    const sid=path.split('/')[3]; const stream=streams.get(sid);
    if(stream&&stream.body.run_id===body.run_id){stream.emit({type:'cancelled'});stream.controller.close();streams.delete(sid);}
    const session=sessions.get(sid);if(session)session.pending=null;
    return response({ok:true,cancelling:!!stream});
  }
  if(path.startsWith('/v1/sessions/')&&init.method!=='POST')return response(sessions.get(path.split('/')[3]));
  if(path==='/v1/chat'||path==='/v1/chat/approve'){
    const stream=new ReadableStream({start(controller){
      const emit=(ev:any)=>controller.enqueue(encoder.encode(`data: ${JSON.stringify({session_id:body.session_id,run_id:body.run_id,...ev})}\n\n`));
      streams.set(body.session_id,{body,controller,emit});
      emit({type:'status',phase:'thinking'});
    }});
    return new Response(stream,{headers:{'Content-Type':'text/event-stream'}});
  }
  throw new Error(`Unexpected API ${path}`);
};
setLang('en');
const plugin=new SocratesPenPlugin(app as any,{version:'0.28.0'} as any);
plugin.settings={...DEFAULT_SETTINGS,deepQuestions:false};
(plugin as any).sidecarSnap=()=>({phase:'running'});
(plugin as any).sidecarWatch=()=>()=>{};
(plugin as any).takePick=()=>null;
const first=createSession('note');
plugin.notes={'note.md':{handbook_id:'note',session_id:first.session_id}};
const leaf:any={app,contentEl:document.getElementById('workspace')};
let view=new PenView(leaf,plugin);
win.qa={view,plugin,streams,sessions,
  async reopen(){await view.onClose();view.unload();view=new PenView(leaf,plugin);win.qa.view=view;await view.onOpen();},
  async capture(){await view.captureSelection({file,absPath:'/vault/note.md',text:'# Big Bang\nA shared note with independent questions.',startLine:1,endLine:2});},
  emit(index:number,event:any){const pane=(view as any).panes[index].pane;streams.get(pane.session)!.emit(event);},
  finish(index:number){const pane=(view as any).panes[index].pane;const stream=streams.get(pane.session)!;stream.emit({type:'done',has_substantive:true});stream.controller.close();streams.delete(pane.session);},
  async history(index:number){const pane=(view as any).panes[index].pane;pane.msgs=Array.from({length:35},(_,i)=>({role:i%2?'assistant':'user',text:`Message ${i+1}\nSeveral lines of text to exercise independent scrolling and preserve reading position while other agents work.\nA second paragraph with some context.`}));await pane.paintLog();},
  async language(lang:'en'|'zh'){setLang(lang);view.relocalize();},
};
view.onOpen().then(()=>{win.ready=true;});
