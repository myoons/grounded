import { app, BrowserWindow } from "electron";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../../docs/figures/", import.meta.url));
const output = path.join(os.homedir(), "Library/Application Support/Roomshift/artifacts/figures-qa");
let window;
const deadline = setTimeout(() => app.exit(1), 60000);
async function main() {
try {
  await app.whenReady();
  await mkdir(output, { recursive: true });
  const figures = JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8"));
  window = new BrowserWindow({ show: false, width: 1080, height: 800, webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
  const reports = [];
  for (const figure of figures) {
    await window.loadFile(path.join(root, figure.name + ".svg"));
    const report = await window.webContents.executeJavaScript(`(async () => {
      await document.fonts.ready;
      const svg = document.querySelector('svg');
      const bounds = svg.viewBox.baseVal;
      const texts = [...document.querySelectorAll('text')].map(node => {
        const box = node.getBBox();
        return {text:node.textContent,x:box.x,y:box.y,width:box.width,height:box.height};
      });
      const outside = texts.filter(box => box.x < 0 || box.y < 0 || box.x+box.width > bounds.width || box.y+box.height > bounds.height);
      const overlaps = [];
      for(let left=0;left<texts.length;left++)for(let right=left+1;right<texts.length;right++){
        const first=texts[left], second=texts[right];
        if(Math.min(first.x+first.width,second.x+second.width)-Math.max(first.x,second.x)>1 && Math.min(first.y+first.height,second.y+second.height)-Math.max(first.y,second.y)>1) overlaps.push([first.text,second.text]);
      }
      return {outside,overlaps,width:bounds.width,height:bounds.height,textCount:texts.length};
    })()`);
    window.setContentSize(report.width, report.height);
    await writeFile(path.join(output, figure.name + ".png"), (await window.webContents.capturePage()).toPNG());
    reports.push({ name: figure.name, ...report });
    console.log(JSON.stringify({ name: figure.name, outside: report.outside, overlaps: report.overlaps }));
  }
  await writeFile(path.join(output, "report.json"), JSON.stringify(reports, null, 2));
  assert.ok(reports.every(report => !report.outside.length && !report.overlaps.length), "Figure label layout requires repair.");
} catch (error) {
  console.error(error);
  process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  window?.destroy();
  app.exit(process.exitCode || 0);
}
}
void main();
