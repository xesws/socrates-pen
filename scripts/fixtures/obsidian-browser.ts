/** Small host double for browser-testing the real PenView, not a second UI. */
function create(this: HTMLElement, tag: string, opts: any = {}) {
  if (typeof opts === "string") opts = {cls: opts};
  const el = document.createElement(tag);
  if (opts.cls) el.className = opts.cls;
  if (opts.text) el.textContent = opts.text;
  for (const [key,value] of Object.entries(opts.attr || {})) el.setAttribute(key,String(value));
  this.appendChild(el);
  return el;
}
Object.assign(HTMLElement.prototype, {
  createEl: create,
  createDiv(this: HTMLElement, opts: any) { return create.call(this,"div",opts); },
  createSpan(this: HTMLElement, opts: any) { return create.call(this,"span",opts); },
  empty(this: HTMLElement) { this.replaceChildren(); },
  setText(this: HTMLElement, value: string) { this.textContent=value; },
  setAttr(this: HTMLElement, key: string, value: string) { this.setAttribute(key,value); },
  addClass(this: HTMLElement, ...cls: string[]) { this.classList.add(...cls); },
  removeClass(this: HTMLElement, ...cls: string[]) { this.classList.remove(...cls); },
  hasClass(this: HTMLElement, cls: string) { return this.classList.contains(cls); },
  toggleClass(this: HTMLElement, cls: string, value: boolean) { this.classList.toggle(cls,value); },
});
export class Component {
  children: Component[]=[]; cleanup: (()=>void)[]=[];
  addChild<T extends Component>(child:T):T { this.children.push(child); child.onload?.(); return child; }
  removeChild(child:Component) { child.unload(); this.children=this.children.filter(c=>c!==child); }
  register(fn:()=>void) { this.cleanup.push(fn); }
  registerEvent(ref:any) { if (typeof ref==='function') this.register(ref); }
  registerDomEvent(el:any, event:string, fn:any) { el.addEventListener(event,fn); this.register(()=>el.removeEventListener(event,fn)); }
  onload() {} onunload() {}
  unload() { this.children.forEach(c=>c.unload()); this.cleanup.forEach(f=>f()); this.onunload(); }
}
export class ItemView extends Component {
  contentEl: HTMLElement; app:any;
  constructor(readonly leaf:any) {super();this.app=leaf.app;this.contentEl=leaf.contentEl;}
}
export class Plugin extends Component {
  app:any; manifest:any={version:'0.28.0'};
  constructor(app:any){super();this.app=app;}
  async saveData(data:any){(window as any).savedData=structuredClone(data);}
  async loadData(){return (window as any).savedData;}
}
export class App {}
export class WorkspaceLeaf {}
export class MarkdownView {}
export class PluginSettingTab {}
export class Setting {}
export class FileSystemAdapter {getBasePath(){return '/vault';}}
export class TFile {extension='md';name='note.md';path='note.md';}
export class Notice {constructor(text:string){((window as any).notices??=[]).push(text);}}
export const getLanguage=()=>"en";
export const getIcon=()=>document.createElement('span');
export function setTooltip(el:HTMLElement,text:string){el.title=text;}
export function setIcon(el:HTMLElement,name:string){el.dataset.icon=name;el.textContent=({plus:'＋',x:'×',square:'■','undo-2':'↶','redo-2':'↷','square-pen':'✎','fold-vertical':'↕',zap:'ϟ',radar:'◎'} as any)[name]||'·';}
export const MarkdownRenderer={async render(_app:any,text:string,el:HTMLElement){el.textContent=text;el.style.whiteSpace='pre-wrap';}};
