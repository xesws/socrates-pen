import { App, FileSystemAdapter, MarkdownView, TFile } from "obsidian";
import { collapseWs, linesFromQuote } from "./locate";
import { t } from "./i18n";

export type EditorPick = {
  text: string;
  startLine: number;
  endLine: number;
  file: TFile;
  absPath: string;
};

const MIN_CHARS = 4;

export function vaultRoot(app: App): string {
  const ad = app.vault.adapter;
  if (ad instanceof FileSystemAdapter) return ad.getBasePath();
  throw new Error(t().errNeedDesktopVault);
}

export function relFromAbs(app: App, absPath: string): string | null {
  const root = vaultRoot(app).replace(/\\/g, "/");
  const abs = absPath.replace(/\\/g, "/");
  const prefix = root.endsWith("/") ? root : `${root}/`;
  if (abs === root || abs === prefix.slice(0, -1)) return "";
  if (abs.startsWith(prefix)) return abs.slice(prefix.length);
  return null;
}

export function handbookIdFromPath(absPath: string): string {
  let h = 0;
  for (let i = 0; i < absPath.length; i++) {
    h = (Math.imul(h, 31) + absPath.charCodeAt(i)) >>> 0;
  }
  const stem = absPath.split("/").pop()?.replace(/\.md$/i, "") || "note";
  const slug =
    stem
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^-+|-+$/g, "") || "note";
  const hex = h.toString(16);
  const short = slug.slice(0, 48).replace(/-+$/g, "") || "note";
  return `${short}-${hex}`;
}

function absFor(app: App, file: TFile): string {
  return `${vaultRoot(app)}/${file.path}`;
}

function markdownViews(app: App): MarkdownView[] {
  const out: MarkdownView[] = [];
  for (const leaf of app.workspace.getLeavesOfType("markdown")) {
    const v = leaf.view;
    if (v instanceof MarkdownView && v.file) out.push(v);
  }
  return out;
}

function pickFromEditor(app: App, view: MarkdownView): EditorPick | null {
  if (!view.file) return null;
  const editor = view.editor;
  const text = collapseWs(editor.getSelection() || "");
  if (text.length < MIN_CHARS) return null;
  const from = editor.getCursor("from");
  const to = editor.getCursor("to");
  return {
    text,
    startLine: from.line + 1,
    endLine: to.line + 1,
    file: view.file,
    absPath: absFor(app, view.file),
  };
}

function nodeIn(el: HTMLElement | null | undefined, node: Node | null): boolean {
  if (!el || !node) return false;
  return el === node || el.contains(node);
}

function pickFromPreview(app: App): EditorPick | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount < 1) return null;
  const text = collapseWs(sel.toString() || sel.getRangeAt(0).toString());
  if (text.length < MIN_CHARS) return null;
  const node = sel.getRangeAt(0).commonAncestorContainer;
  if (node instanceof Element && node.closest(".socrates-pen")) return null;
  const parent = node instanceof Element ? node : node.parentElement;
  if (parent?.closest(".socrates-pen")) return null;

  for (const view of markdownViews(app)) {
    const preview = view.previewMode?.containerEl;
    if (
      !nodeIn(view.contentEl, node) &&
      !nodeIn(view.containerEl, node) &&
      !nodeIn(preview, node)
    ) {
      continue;
    }
    const src = view.getViewData?.() ?? view.editor.getValue();
    const lines = linesFromQuote(src, text) ?? { startLine: 1, endLine: 1 };
    return {
      text,
      startLine: lines.startLine,
      endLine: lines.endLine,
      file: view.file as TFile,
      absPath: absFor(app, view.file as TFile),
    };
  }
  return null;
}

/** 现在这一刻能从编辑器 / 阅读模式读到的选区。活动叶子不必是 Markdown。 */
export function readLivePick(app: App): EditorPick | null {
  const views = markdownViews(app);
  const active = app.workspace.getActiveViewOfType(MarkdownView);
  const ordered = active ? [active, ...views.filter((v) => v !== active)] : views;
  for (const v of ordered) {
    const p = pickFromEditor(app, v);
    if (p) return p;
  }
  return pickFromPreview(app);
}

/** @deprecated 用 readLivePick；保留名字避免旧调用漏改 */
export function readEditorPick(app: App): EditorPick | null {
  return readLivePick(app);
}
