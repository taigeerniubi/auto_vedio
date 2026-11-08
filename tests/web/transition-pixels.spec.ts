// 逐个核对真实浏览器截图，防止动画样式变化却未绘制素材的黑屏问题。
import {expect, test, type Locator, type Page} from '@playwright/test';
import {TRANSITION_FAMILIES, TRANSITION_PRESETS, type TransitionPresetId} from '../../packages/composition/transitionPresets';

async function visiblePixels(page: Page, demo: Locator, path: string) {
  const png = await demo.screenshot({path});
  return page.evaluate(async base64 => {
    const image = await createImageBitmap(await (await fetch(`data:image/png;base64,${base64}`)).blob());
    const canvas = new OffscreenCanvas(64, 36), ctx = canvas.getContext('2d')!;
    // 去掉边框，断言屏幕实际呈现的内容，而非 DOM 样式或播放帧号。
    ctx.drawImage(image, 3, 3, image.width - 6, image.height - 6, 0, 0, 64, 36);
    image.close();
    const pixels = ctx.getImageData(0, 0, 64, 36).data;
    let colorful = 0;
    for (let i = 0; i < pixels.length; i += 4) {
      if (Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 25) colorful++;
    }
    return {pixels: Array.from(pixels), colorful: colorful / (64 * 36)};
  }, png.toString('base64'));
}

for (const id of Object.keys(TRANSITION_PRESETS) as TransitionPresetId[]) {
  test(`${id} 的悬停和再次移入均显示变化的实际画面`, async ({page}) => {
    const errors: string[] = [], writes: string[] = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('request', r => { if (r.method() === 'POST' && r.url().endsWith('/tools')) writes.push(r.url()); });
    await page.goto('/');
    await page.getByRole('button', {name: '转场', exact: true}).click();
    const family = TRANSITION_FAMILIES.find(f => f.id === TRANSITION_PRESETS[id].family)!;
    const card = page.getByRole('button', {name: family.label, exact: true});
    if (family.directional) {
      await card.click();
      await page.getByLabel('转场方向').selectOption(id.split('_').at(-1)!);
    }
    const demo = card.locator('.transition-demo');
    for (let round = 0; round < 2; round++) {
      await page.mouse.move(600, 90);
      await card.hover();
      await expect(demo.locator('.transition-demo-player img')).toHaveCount(2);
      await expect.poll(() => demo.locator('.transition-demo-player img').evaluateAll(images => images.every(image =>
        (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0 && parseFloat(getComputedStyle(image).width) > 100,
      ))).toBe(true);
      const samples: Awaited<ReturnType<typeof visiblePixels>>[] = [];
      for (let i = 0; i < 4; i++) {
        samples.push(await visiblePixels(page, demo, `artifacts/transition-audit/${id}-${round}-${i}.png`));
        if (i < 3) await page.waitForTimeout(110);
      }
      // 淡黑/闪白/翻转允许特定瞬间黑白，但整个采样区间不能都是纯色。
      expect(Math.max(...samples.map(s => s.colorful)), `${id} round ${round}: visible scene`).toBeGreaterThan(.25);
      const changes = samples.slice(1).map(sample => sample.pixels.reduce((sum, value, i) =>
        sum + Math.abs(value - samples[0].pixels[i]), 0) / sample.pixels.length);
      expect(Math.max(...changes), `${id} round ${round}: moving scene`).toBeGreaterThan(3);
      await expect(card).not.toContainText('预览暂不可用');
    }
    expect(errors).toEqual([]);
    expect(writes).toEqual([]);
  });
}
