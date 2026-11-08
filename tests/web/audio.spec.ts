// 验证真实 MP3/WAV 的导入前后试听、播放清理和成片声音互斥。
import { test, expect, type Page } from "@playwright/test";

async function ready(page: Page) {
  const project = await (await page.request.post("/api/v1/projects", { data: { name: "音频试听验收" } })).json();
  await page.addInitScript((pid) => localStorage.setItem("shiguang-project", pid), project.project_id);
  await page.goto("/");
  await expect(page.getByRole("button", { name: "新会话", exact: true })).toBeEnabled();
  return `/api/v1/projects/${project.project_id}`;
}

async function saved(page: Page, revision: number) {
  await expect(page.locator(".statusbar")).toContainText(`版本 ${revision}`);
  await expect(page.locator(".save-status")).toContainText("所有更改已保存");
  await expect(page.getByRole("alert")).toHaveCount(0);
}

test("重叠配乐分行显示，从播放头添加并能直接选中删除及撤销", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const root = await ready(page);
  const assets = [];
  for (const name of ["frame-red.mp4", "frame-blue-24fps.mp4", "music.wav", "music.mp3"]) {
    const response = await page.request.post(root + "/assets/import", { data: { path: "test-inputs/" + name } });
    expect(response.ok()).toBe(true);
    assets.push(await response.json());
  }
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  const setup = [
    { tool: "assemble_sequence", arguments: { clips: assets.slice(0, 2).map((asset, index) => ({
      clip_id: `audio-scene-${index}`, asset_id: asset.asset_id, kind: "video", source_out_frame: 60,
    })) } },
    { tool: "add_audio", arguments: { asset_id: assets[2].asset_id, anchor: { end_frame: 120 }, track: 1 } },
    { tool: "add_audio", arguments: { asset_id: assets[3].asset_id, anchor: { end_frame: 120 }, track: 1 } },
    { tool: "add_audio", arguments: { asset_id: assets[2].asset_id, anchor: { kind: "clip", clip_id: "audio-scene-1", end_frame: 60 }, track: 2 } },
  ];
  for (const [seq, command] of setup.entries()) {
    const response = await page.request.post(`${root}/drafts/${draft.id}/tools`, {
      data: { ...command, op_id: `setup-${seq}`, expected_seq: seq },
    });
    expect(response.ok()).toBe(true);
  }
  expect((await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} })).ok()).toBe(true);
  await page.reload();
  await saved(page, 1);
  await expect(page.locator(".music-block")).toHaveCount(3);
  const lanePositions = await page.locator(".music-block").evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().top));
  expect(new Set(lanePositions).size).toBe(3);
  await page.locator(".music-block").last().scrollIntoViewIfNeeded();
  await expect(page.locator(".music-block").last()).toBeInViewport();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await page.screenshot({ path: "artifacts/frontend-acceptance/timeline-audio-lanes.png", fullPage: true });

  // 保留原有的叠加配乐内容，仅在用户删除时调整超出新成片的区间。
  const original = await (await page.request.get(root)).json();
  await page.getByRole("button", { name: "片段 2 frame-blue-24fps.mp4", exact: true }).click();
  await page.getByRole("button", { name: "删除选中片段", exact: true }).click();
  await saved(page, 2);
  await expect(page.locator(".clip-block")).toHaveCount(1);
  await expect(page.locator(".music-block")).toHaveCount(2);
  const shortened = await (await page.request.get(root)).json();
  expect(shortened.snapshot.audio_items.map((item: any) => item.anchor.end_frame)).toEqual([60, 60]);
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await saved(page, 3);
  expect((await (await page.request.get(root)).json()).snapshot).toEqual(original.snapshot);

  const mp3 = page.getByRole("button", { name: "音频 music.mp3，00:00.00 至 00:04.00", exact: true });
  await mp3.click();
  await expect(mp3).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("tab", { name: "片段属性", exact: true }).click();
  await expect(page.locator(".music-editor")).toHaveCount(1);
  await expect(page.locator(".music-editor")).toContainText("music.mp3");
  await page.getByRole("button", { name: "删除选中音频", exact: true }).click();
  await saved(page, 4);
  await expect(page.locator(".music-block")).toHaveCount(2);
  await expect(page.locator(".clip-block")).toHaveCount(2);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  await saved(page, 5);
  await mp3.click();
  await page.keyboard.press("Delete");
  await saved(page, 6);
  await expect(mp3).toHaveCount(0);
  expect((await (await page.request.get(root + "/assets")).json()).length).toBe(4);

  // 点击第二段画面把播放头放在 2 秒，新增配乐应从此处开始。
  await page.getByRole("button", { name: "片段 2 frame-blue-24fps.mp4", exact: true }).click();
  await page.getByRole("button", { name: "添加素材 music.mp3", exact: true }).click();
  await saved(page, 7);
  const added = (await (await page.request.get(root)).json()).snapshot.audio_items.at(-1);
  expect(added.anchor).toMatchObject({ kind: "timeline", start_frame: 60, end_frame: 120 });
  expect(added.source_in_frame).toBe(0);
  await expect(page.getByRole("button", { name: "音频 music.mp3，00:02.00 至 00:04.00", exact: true })).toHaveCount(1);
  await expect(page.locator(".music-block")).toHaveCount(3);
  await page.reload();
  await saved(page, 7);
  await expect(page.locator(".music-block")).toHaveCount(3);
  const firstClip = await page.locator(".clip-block").first().boundingBox();
  const transfer = await page.evaluateHandle(assetId => {
    const data = new DataTransfer();
    data.setData("application/shiguang-asset", assetId);
    return data;
  }, assets[2].asset_id);
  await page.locator(".music-track").first().dispatchEvent("drop", {
    dataTransfer: transfer, clientX: firstClip!.x + firstClip!.width / 2,
  });
  await saved(page, 8);
  const dropped = (await (await page.request.get(root)).json()).snapshot.audio_items.at(-1);
  expect(dropped.anchor).toMatchObject({ kind: "timeline", start_frame: 30, end_frame: 120 });
  await transfer.dispose();
  expect(errors).toEqual([]);
});

test("导入前直接试听 MP3 和 WAV，切换或关闭后停止且不改变选中项", async ({ page }) => {
  const history = await (await page.request.post("/api/v1/projects", { data: { name: "音频导入历史" } })).json();
  for (const name of ["music.mp3", "music.wav"]) {
    const imported = await page.request.post(`/api/v1/projects/${history.project_id}/assets/import`, {
      data: { path: "test-inputs/" + name },
    });
    expect(imported.ok()).toBe(true);
  }
  const root = await ready(page);
  const before = await (await page.request.get(root)).json();
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "导入素材" });
  await dialog.getByRole("button", { name: "试听 music.mp3", exact: true }).click();
  const mp3 = page.locator('audio[data-audition][aria-label="试听播放器 music.mp3"]');
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(0.1);
  const previous = await mp3.elementHandle();
  await dialog.getByRole("button", { name: "暂停试听 music.mp3", exact: true }).click();
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  const pausedAt = await mp3.evaluate((node: HTMLAudioElement) => node.currentTime);
  await dialog.getByRole("button", { name: "试听 music.mp3", exact: true }).click();
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(pausedAt);
  await dialog.getByRole("button", { name: "试听 music.wav", exact: true }).click();
  const wav = page.locator('audio[data-audition][aria-label="试听播放器 music.wav"]');
  await expect.poll(() => wav.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(0.1);
  expect(await previous!.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  await expect(dialog.getByRole("button", { name: "试听 music.mp3", exact: true })).toHaveAttribute("aria-pressed", "false");
  await expect(dialog.getByRole("button", { name: "暂停试听 music.wav", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(dialog.locator("audio[controls]")).toHaveCount(0);
  expect(await dialog.getByRole("checkbox").evaluateAll((nodes) => nodes.some((node) => (node as HTMLInputElement).checked))).toBe(false);
  const current = await wav.elementHandle();
  await page.screenshot({ path: "artifacts/frontend-acceptance/library-audio-preview.png", fullPage: true });
  await dialog.getByRole("button", { name: "关闭弹窗", exact: true }).click();
  expect(await current!.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  expect(await (await page.request.get(root + "/assets")).json()).toEqual([]);
  expect(await (await page.request.get(root)).json()).toEqual(before);
});

test("素材封面直接播放暂停，结束后复位，且与成片播放互斥", async ({ page }) => {
  const root = await ready(page);
  const assets = [];
  for (const name of ["music.mp3", "music.wav", "frame-red.mp4"]) {
    const response = await page.request.post(root + "/assets/import", { data: { path: "test-inputs/" + name } });
    expect(response.ok()).toBe(true);
    assets.push(await response.json());
  }
  const draft = await (await page.request.post(root + "/drafts", { data: {} })).json();
  const inserted = await page.request.post(`${root}/drafts/${draft.id}/tools`, { data: {
    tool: "insert_clip", arguments: { clip: { clip_id: "listen-video", asset_id: assets[2].asset_id, kind: "video", source_out_frame: 60 } },
    op_id: "listen-video", expected_seq: 0,
  } });
  expect(inserted.ok()).toBe(true);
  await page.request.post(`${root}/drafts/${draft.id}/commit`, { data: {} });
  await page.reload();
  await page.getByRole("button", { name: "音频", exact: true }).click();
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "试听 music.mp3", exact: true }).click();
  const mp3 = page.locator('audio[data-audition][aria-label="试听播放器 music.mp3"]');
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(0.1);
  await expect(page.locator(".asset-panel audio[controls], .audio-preview")).toHaveCount(0);
  await page.getByRole("button", { name: "暂停试听 music.mp3", exact: true }).press("Space");
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  const pausedAt = await mp3.evaluate((node: HTMLAudioElement) => node.currentTime);
  await page.getByRole("button", { name: "试听 music.mp3", exact: true }).click();
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(pausedAt);
  await mp3.evaluate((node: HTMLAudioElement) => { node.currentTime = node.duration - 0.08; });
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.ended)).toBe(true);
  await page.getByRole("button", { name: "试听 music.mp3", exact: true }).click();
  await expect(page.getByRole("button", { name: "暂停试听 music.mp3", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(await mp3.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeLessThan(1);
  await page.getByRole("button", { name: "播放", exact: true }).click();
  await expect.poll(() => mp3.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  await expect(page.getByRole("button", { name: "试听 music.mp3", exact: true })).toHaveAttribute("aria-pressed", "false");
  await page.getByRole("button", { name: "试听 music.wav", exact: true }).click();
  const wav = page.locator('audio[data-audition][aria-label="试听播放器 music.wav"]');
  await expect.poll(() => wav.evaluate((node: HTMLAudioElement) => node.currentTime)).toBeGreaterThan(0.1);
  await expect(page.getByRole("button", { name: "播放", exact: true })).toBeVisible();
  const current = await wav.elementHandle();
  await page.screenshot({ path: "artifacts/frontend-acceptance/project-audio-preview.png", fullPage: true });
  await page.getByRole("button", { name: "文字", exact: true }).click();
  expect(await current!.evaluate((node: HTMLAudioElement) => node.paused)).toBe(true);
  const project = await (await page.request.get(root)).json();
  expect(project.revision).toBe(1);
  expect(project.snapshot.audio_items).toEqual([]);
});
