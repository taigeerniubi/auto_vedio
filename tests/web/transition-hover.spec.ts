// 回归悬停直接试看、连续循环、鼠标与焦点交错，以及慢加载时的可见反馈。
import {expect, test} from '@playwright/test';
import {TRANSITION_FAMILIES} from '../../packages/composition/transitionPresets';

test('快速切换卡片可直接试看，焦点变化不会取消鼠标悬停', async ({page}) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await page.getByRole('button', {name: '转场', exact: true}).click();
  const panel = page.locator('.transition-content');
  // 同一页面遍历整个目录，再回到黑屏回归项，全部只悬停。
  for (const label of [...TRANSITION_FAMILIES.map(family => family.label), '圆形展开', '立方体', '缩放模糊']) {
    const card = panel.getByRole('button', {name: label, exact: true});
    await card.hover();
    const player = card.locator('.transition-demo-player');
    await expect(player).toBeVisible();
    await expect(player.locator('img')).toHaveCount(2);
    const visual = () => player.evaluate(node => {
      const canvas = node.querySelector('canvas');
      return canvas ? canvas.dataset.frame : [...node.querySelectorAll('div')].map(n => n.style.opacity + n.style.transform + n.style.clipPath).join('|');
    });
    const first = await visual();
    await expect.poll(visual).not.toBe(first);
    await expect(panel.locator('.transition-demo-player')).toHaveCount(1);
  }
  const a = panel.getByRole('button', {name: '缩放模糊', exact: true});
  const b = panel.getByRole('button', {name: '模糊甩镜', exact: true});
  await a.focus();
  await b.hover();
  await a.evaluate(node => (node as HTMLElement).blur());
  await expect(b.locator('.transition-demo-player')).toBeVisible();
  await expect(a.locator('.transition-demo-player')).toHaveCount(0);
  // 移出再移入能够重播；这里不应出现吞掉 mouseenter 的旧焦点状态。
  await page.mouse.move(600, 90);
  await expect(panel.locator('.transition-demo-player')).toHaveCount(0);
  await b.hover();
  await expect(b.locator('canvas[data-transition="blur_slide_left"]')).toHaveAttribute('data-frame', /\d+/);
  expect(errors).toEqual([]);
});

test('长时间悬停持续展示效果并复用同一画布，减少动态效果时有明确标记', async ({page}) => {
  await page.goto('/');
  await page.getByRole('button', {name: '转场', exact: true}).click();
  const card = page.getByRole('button', {name: '缩放模糊', exact: true});
  await card.hover();
  const canvas = card.locator('canvas[data-transition="cross_zoom"]');
  await expect(canvas).toHaveAttribute('data-frame', /\d+/);
  const playback = await canvas.evaluate(node => new Promise<{connected: boolean; min: number; max: number; wraps: number}>(resolve => {
    const frames = [Number((node as HTMLElement).dataset.frame)];
    const observer = new MutationObserver(() => frames.push(Number((node as HTMLElement).dataset.frame)));
    observer.observe(node, {attributes: true, attributeFilter: ['data-frame']});
    setTimeout(() => {
      observer.disconnect();
      resolve({connected: node.isConnected, min: Math.min(...frames), max: Math.max(...frames),
        wraps: frames.filter((f, i) => i > 0 && f < frames[i - 1]).length});
    }, 1500);
  }));
  expect(playback.connected).toBe(true);
  expect(playback.min).toBeGreaterThanOrEqual(24);
  expect(playback.max).toBeLessThanOrEqual(41);
  expect(playback.wraps).toBeGreaterThanOrEqual(1);
  await page.emulateMedia({reducedMotion: 'reduce'});
  await expect(card).toContainText('静态预览');
  await expect(canvas).toHaveAttribute('data-frame', '33');
  await page.emulateMedia({reducedMotion: 'no-preference'});
  await expect(card).not.toContainText('静态预览');
  await expect.poll(() => canvas.getAttribute('data-frame')).not.toBe('33');
});

test('首次示例准备较慢时显示状态，准备好后自动开始试看', async ({page}) => {
  await page.addInitScript(() => {
    const decode = HTMLImageElement.prototype.decode;
    let released = false;
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    Object.assign(window, {releaseDemoImages: () => { released = true; release(); }});
    HTMLImageElement.prototype.decode = async function() {
      if (!released && this.src.startsWith('data:image/svg+xml')) await pending;
      return decode.call(this);
    };
  });
  await page.goto('/');
  await page.getByRole('button', {name: '转场', exact: true}).click();
  const card = page.getByRole('button', {name: '故障闪切', exact: true});
  await card.hover();
  await expect(card).toContainText('准备预览…');
  await page.evaluate(() => (window as unknown as {releaseDemoImages: () => void}).releaseDemoImages());
  await expect(card.locator('canvas[data-transition="glitch"]')).toHaveAttribute('data-frame', /\d+/);
  await expect(card).not.toContainText('准备预览…');
});
