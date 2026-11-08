// 验证转场选择、统一工具参数、预览清单、移除及完整撤销。
import { test, expect } from "@playwright/test";
import { TRANSITION_FAMILIES, TRANSITION_PRESETS, isShaderTransition, type TransitionPresetId } from "../../packages/composition/transitionPresets";

test("全部转场通过同一工具应用，预览与时间线同步且可以撤销", async ({ page }) => {
  test.setTimeout(180000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const project = await (await page.request.post("/api/v1/projects", { data: { name: "多转场验收", composition: { width: 640, height: 360 } } })).json();
  const root = `/api/v1/projects/${project.project_id}`;
  const assets = [];
  for (const name of ["frame-red.mp4", "frame-blue-24fps.mp4", "music.wav"]) {
    const response = await page.request.post(root + "/assets/import", { data: { path: "test-inputs/" + name } });
    expect(response.ok()).toBe(true);
    assets.push(await response.json());
  }
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  const commands = [
    { tool: "assemble_sequence", arguments: { clips: assets.slice(0, 2).map((a, i) => ({ clip_id: `t${i}`, asset_id: a.asset_id, kind: "video", source_out_frame: 60 })) } },
    { tool: "add_audio", arguments: { asset_id: assets[2].asset_id, anchor: { end_frame: 120 } } },
  ];
  for (const [seq, command] of commands.entries()) {
    const response = await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: { ...command, op_id: `setup-${seq}`, expected_seq: seq } });
    expect(response.ok()).toBe(true);
  }
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  const initial = await (await page.request.get(root)).json();
  await page.addInitScript(pid => localStorage.setItem("shiguang-project", pid), project.project_id);
  await page.goto("/");
  await page.getByRole("button", { name: "片段 1 frame-red.mp4", exact: true }).click();
  await page.getByRole("button", { name: "转场", exact: true }).click();
  const panel = page.locator(".transition-content");
  await expect(panel).toContainText("14 类 · 29 个预设");
  await expect(panel.locator('.transition-card')).toHaveCount(14);
  let revision = 1;
  let mix = "";
  const catalog = await (await page.request.get("/api/v1/presets")).json();
  expect(catalog.transition).toEqual(Object.keys(TRANSITION_PRESETS));
  for (const id of Object.keys(TRANSITION_PRESETS) as TransitionPresetId[]) {
    const preset = TRANSITION_PRESETS[id];
    const family = TRANSITION_FAMILIES.find(f => f.id === preset.family)!;
    await panel.getByRole("button", { name: family.label, exact: true }).click();
    if (family.directional) await panel.getByLabel("转场方向").selectOption(id.split("_").at(-1)!);
    expect((await (await page.request.get(root)).json()).revision).toBe(revision);
    const nextPreview = page.waitForResponse(response => response.url().endsWith(root + "/preview") && response.request().postDataJSON().expected_revision === revision + 1);
    const write = page.waitForRequest(request => request.url().endsWith("/tools") && request.method() === "POST");
    await panel.getByRole("button", { name: "应用到下一片段", exact: true }).click();
    expect((await write).postDataJSON()).toMatchObject({ tool: "set_transition", arguments: { preset_id: id, duration_frames: 12 } });
    const prepared = await (await nextPreview).json();
    revision++;
    expect(prepared.plan.transitions[0].preset_id).toBe(id);
    if (mix) expect(prepared.plan.mix_file).toBe(mix);
    mix = prepared.plan.mix_file;
    await expect(page.locator(".statusbar")).toContainText(`版本 ${revision}`);
    await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
    await expect(page.locator(".transition-block")).toHaveAttribute("aria-label", `${preset.label}转场，12 帧`);
    await expect(panel).toContainText(`已应用：${preset.label}`);
    await expect(page.getByRole("alert")).toHaveCount(0);
    if (isShaderTransition(id)) {
      const canvas = page.locator(`.preview-stage canvas[data-transition="${id}"]`);
      await expect(canvas).toHaveAttribute('data-frame', '54');
      expect(await canvas.evaluate(node => {
        const pixels = (node as HTMLCanvasElement).getContext('2d')!.getImageData(0, 0, 640, 360).data;
        return pixels.some((value, i) => i % 4 !== 3 && value > 40);
      })).toBe(true);
    }
  }
  const last = await (await page.request.get(root)).json();
  expect(last.snapshot.audio_items[0].anchor.end_frame).toBe(108);
  await expect.poll(() => page.locator(".preview-stage video").evaluateAll(nodes =>
    nodes.length === 2 && nodes.every(node => (node as HTMLVideoElement).readyState >= 2 && !(node as HTMLVideoElement).seeking),
  )).toBe(true);
  const ruler = page.getByLabel('时间标尺');
  const pixelsPerFrame = ((await page.locator('.clip-block').first().boundingBox())!.width + 2) / 60;
  const previewCanvas = page.locator('.preview-stage canvas[data-transition="glitch"]');
  for (const target of [49, 57, 51]) {
    await ruler.click({position: {x: target * pixelsPerFrame, y: 12}});
    await expect(previewCanvas).toHaveAttribute('data-frame', String(target));
  }
  const updates = previewCanvas.evaluate(node => new Promise<number[]>(resolve => {
    const frames = new Set<number>();
    const done = () => { observer.disconnect(); clearTimeout(timer); resolve([...frames]); };
    const observer = new MutationObserver(() => {
      frames.add(Number((node as HTMLElement).dataset.frame));
      if (frames.size >= 3) done();
    });
    const timer = setTimeout(done, 3000);
    observer.observe(node, {attributes: true, attributeFilter: ['data-frame']});
  }));
  await page.getByRole('button', {name: '播放', exact: true}).click();
  expect((await updates).filter(f => f > 51).length).toBeGreaterThanOrEqual(2);
  const pause = page.getByRole('button', {name: '暂停', exact: true});
  if (await pause.isVisible()) await pause.click();
  await ruler.click({position: {x: 54 * pixelsPerFrame, y: 12}});
  await expect(previewCanvas).toHaveAttribute('data-frame', '54');
  await page.screenshot({ path: "artifacts/transition-acceptance/editor-transitions.png", fullPage: true });
  await panel.getByRole("button", { name: "移除转场", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText(`版本 ${++revision}`);
  await expect(page.locator(".transition-block")).toHaveCount(0);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await expect(page.locator(".statusbar")).toContainText(`版本 ${++revision}`);
  expect((await (await page.request.get(root)).json()).snapshot).toEqual(last.snapshot);
  await page.reload();
  await page.getByRole("button", { name: "片段 1 frame-red.mp4", exact: true }).click();
  await page.getByRole("button", { name: "转场", exact: true }).click();
  await expect(panel.getByRole("button", { name: "故障闪切", exact: true })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "片段 2 frame-blue-24fps.mp4", exact: true }).click();
  await expect(panel.getByRole("button", { name: "应用到下一片段", exact: true })).toBeDisabled();
  // 没有下一镜头时仍可试看，卡片本身不写入工程。
  const card = panel.getByRole('button', {name: '缩放模糊', exact: true});
  await card.click();
  await expect(card).toHaveAttribute('aria-pressed', 'true');
  await expect(card.locator('canvas[data-transition="cross_zoom"]')).toHaveAttribute('data-frame', /\d+/);
  expect((await (await page.request.get(root)).json()).revision).toBe(revision);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await card.focus();
  await page.getByRole('button', {name: '故障闪切', exact: true}).click();
  await expect(panel.locator('canvas[data-transition="glitch"]')).toHaveAttribute('data-frame', '33');
  await page.setViewportSize({width: 390, height: 844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(initial.snapshot.transitions).toEqual([]);
  expect(errors).toEqual([]);
});
