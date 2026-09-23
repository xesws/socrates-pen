import { PracticeView } from "../../src/views/PracticeView";
import { DEFAULT_SETTINGS } from "../../src/settings";
import { setLang } from "../../src/i18n";
import { FileSystemAdapter, TFile } from "obsidian";

type LiveConfig = {
  sidecarUrl: string;
  vaultRoot: string;
  notePath: string;
};

const win = window as any;
const cfg = win.practiceLiveConfig as LiveConfig;
if (!cfg?.sidecarUrl || !cfg?.vaultRoot || !cfg?.notePath) {
  throw new Error("practiceLiveConfig with sidecarUrl, vaultRoot and notePath is required");
}

class LiveAdapter extends FileSystemAdapter {
  getBasePath() {
    return cfg.vaultRoot;
  }
}

const events = new Map<string, Set<Function>>();
let view: PracticeView;
const file = new TFile();
file.path = cfg.notePath;
file.name = cfg.notePath.split("/").pop() || "note.md";
file.extension = file.name.split(".").pop() || "md";
(file as any).basename = file.name.replace(/\.md$/i, "");

const app: any = {
  workspace: {
    on(name: string, fn: Function) {
      if (!events.has(name)) events.set(name, new Set());
      events.get(name)!.add(fn);
      return () => events.get(name)!.delete(fn);
    },
    getActiveFile: () => file,
    getLeavesOfType: (type: string) => (type === "socrates-pen-practice" && view ? [{ view }] : []),
  },
  vault: {
    adapter: new LiveAdapter(),
    getAbstractFileByPath: (path: string) => (path === file.path ? file : null),
  },
};

const plugin: any = {
  app,
  manifest: { version: "0.29.0" },
  settings: {
    ...DEFAULT_SETTINGS,
    lang: "en",
    sidecarUrl: cfg.sidecarUrl,
    practiceExperiment: true,
    model: "practice-live-no-llm",
  },
  async saveSettings() {},
  saveSettingsSoon() {},
  async setPracticeExperiment(on: boolean) {
    plugin.settings.practiceExperiment = on;
    if (view) view.onPracticeExperimentChanged();
  },
};

async function main() {
  setLang("en");
  view = new PracticeView({ app, contentEl: document.getElementById("workspace") } as any, plugin);
  win.qa = { app, plugin, view };
  await view.onOpen();
  win.ready = true;
}

void main().catch((error) => {
  win.readyError = String(error?.stack || error);
  throw error;
});
