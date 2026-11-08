// 直接验证共享 GPU 调色器的数值、分色选择性、曲线及所有预设。
import { test, expect } from "@playwright/test";

test("浮点计算保留高光余量，八色 HSL 与七种曲线及 18 款预设输出有效像素", async ({
  page,
}) => {
  await page.goto("/");
  const compositionUrl = `/@fs${new URL("../../packages/composition/", import.meta.url).pathname}`;
  const results = await page.evaluate(async (path) => {
    const { createColorRenderer } = await import(
      /* @vite-ignore */ path + "colorShader.ts"
    );
    const { defaultGrade, COLOR_PRESETS } = await import(
      /* @vite-ignore */ path + "color.ts"
    );
    const source = new OffscreenCanvas(64, 32),
      ctx = source.getContext("2d")!;
    for (const [i, color] of [
      "rgb(128,128,128)",
      "rgb(150,55,40)",
      "rgb(40,140,60)",
      "rgb(35,70,150)",
    ].entries()) {
      ctx.fillStyle = color;
      ctx.fillRect(i * 16, 0, 16, 32);
    }
    ctx.fillStyle = "white";
    ctx.fillRect(0, 0, 4, 4);
    const renderer = createColorRenderer(64, 32),
      target = new OffscreenCanvas(64, 32),
      read = target.getContext("2d")!;
    const sample = (
      grade: ReturnType<typeof defaultGrade>,
      preset: string | null = null,
      intensity = 1,
    ) => {
      read.drawImage(
        renderer.draw(source, {
          color_grade: grade,
          color_preset: preset,
          color_preset_intensity: intensity,
        }),
        0,
        0,
      );
      return [8, 24, 40, 56].map(
        (x) =>
          Array.from(read.getImageData(x, 16, 1, 1).data).slice(
            0,
            3,
          ) as number[],
      );
    };
    const neutral = sample(defaultGrade());
    const floating = sample({ ...defaultGrade(), exposure: 3, contrast: 0.1 });
    const hsl = defaultGrade();
    hsl.hsl.red.saturation = -1;
    const selective = sample(hsl);
    const rgb = defaultGrade();
    rgb.curves.red = [
      { x: 0, y: 0 },
      { x: 0.5, y: 0.8 },
      { x: 1, y: 1 },
    ];
    const redCurve = sample(rgb);
    const hueResults = [];
    for (const name of ["hue_hue", "hue_sat", "hue_lum"]) {
      const g = defaultGrade();
      g.curves[name] = [
        { x: 0, y: 0.65 },
        { x: 0.2, y: 0.65 },
        { x: 1, y: 0.65 },
      ];
      hueResults.push({ name, pixels: sample(g) });
    }
    const presets = COLOR_PRESETS.map((p: { id: string }) => ({
      id: p.id,
      pixels: sample(defaultGrade(), p.id),
    }));
    const zero = sample(defaultGrade(), "neon", 0);
    sample(defaultGrade());
    const marker = Array.from(read.getImageData(1, 1, 1, 1).data);
    renderer.cleanup();
    return {
      neutral,
      floating,
      selective,
      redCurve,
      hueResults,
      presets,
      zero,
      marker,
    };
  }, compositionUrl);
  expect(results.neutral[0]).toEqual([128, 128, 128]);
  expect(results.floating[0][0]).toBeGreaterThan(145); // 如果中途裁到 1，就只剩约 140。
  expect(results.floating[0][0]).toBeLessThan(155);
  // 红橙交界会平滑混合；红色通道仍应消除至少约四分之三的原有色差。
  expect(results.selective[1][0] - results.selective[1][1]).toBeLessThan(25);
  expect(
    Math.abs(results.selective[2][1] - results.neutral[2][1]),
  ).toBeLessThanOrEqual(2);
  expect(results.redCurve[0][0]).toBeGreaterThan(195);
  expect(results.redCurve[0][1]).toBe(128);
  for (const result of results.hueResults)
    expect(result.pixels).not.toEqual(results.neutral);
  expect(results.presets).toHaveLength(18);
  for (const preset of results.presets) {
    expect(preset.pixels, preset.id).not.toEqual(results.neutral);
    expect(
      preset.pixels.flat().some((v: number) => v > 20),
      preset.id,
    ).toBe(true);
  }
  expect(results.zero).toEqual(results.neutral);
  expect(results.marker).toEqual([255, 255, 255, 255]);
});
