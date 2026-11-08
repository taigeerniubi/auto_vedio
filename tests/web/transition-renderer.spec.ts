// 通过真实 WebGL 像素检查高级效果的纹理方向、端点和可观察的画面变换。
import {test, expect} from '@playwright/test';

test('高级转场保持上下方向与端点，并实际改变图像像素', async ({page}) => {
  await page.goto('/');
  const modulePath = '/@fs/' + process.cwd().replaceAll('\\', '/') + '/packages/composition/shaderTransitions.tsx';
  const results = await page.evaluate(async (path) => {
    const {createTransitionRenderer} = await import(path);
    const make = (color: string) => {
      const c = new OffscreenCanvas(320, 180), ctx = c.getContext('2d')!;
      ctx.fillStyle = color; ctx.fillRect(0, 0, 320, 180);
      ctx.fillStyle = '#ffeec0'; ctx.fillRect(0, 0, 320, 25);
      ctx.fillStyle = '#123645'; ctx.fillRect(0, 155, 320, 25);
      for (let x = 30; x < 320; x += 40) { ctx.fillStyle = '#eeeeee'; ctx.fillRect(x, 35, 6, 110); }
      return c;
    };
    const a = make('#c73040'), b = make('#305cc7');
    const output = document.createElement('canvas'); output.width = 320; output.height = 180;
    const ctx = output.getContext('2d')!;
    const read = () => ctx.getImageData(0, 0, 320, 180).data;
    const delta = (x: Uint8ClampedArray, y: Uint8ClampedArray) => x.reduce((sum, v, i) => sum + Math.abs(v - y[i]), 0) / x.length;
    ctx.drawImage(a, 0, 0); const first = read();
    ctx.drawImage(b, 0, 0); const last = read();
    const blend = new Uint8ClampedArray(first.map((v, i) => (v + last[i]) / 2));
    return ['cross_zoom', 'blur_slide_left', 'blur_slide_right', 'blur_slide_up', 'blur_slide_down', 'film_burn', 'glitch'].map(preset => {
      const r = createTransitionRenderer(preset, 320, 180);
      r.draw(ctx, a, b, 0); const start = delta(read(), first);
      r.draw(ctx, a, b, 1); const end = delta(read(), last);
      r.draw(ctx, a, b, .999); const nearEnd = delta(read(), last);
      r.draw(ctx, a, b, .5); const effect = delta(read(), blend);
      const top = Array.from(ctx.getImageData(160, 10, 1, 1).data);
      r.cleanup();
      return {preset, start, end, nearEnd, effect, top};
    });
  }, modulePath);
  for (const r of results) {
    expect(r.start, r.preset).toBe(0);
    expect(r.end, r.preset).toBe(0);
    expect(r.nearEnd, r.preset).toBeLessThan(2);
    expect(r.effect, r.preset).toBeGreaterThan(.6);
  }
});
