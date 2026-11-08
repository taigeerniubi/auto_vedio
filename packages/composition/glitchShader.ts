// 适配 GL Transitions 的 GlitchMemories，使用确定性帧进度驱动故障闪切。
// Author: Gunnar Roth, based on work from natewave. License: MIT.
// Source: https://github.com/gl-transitions/gl-transitions/blob/master/transitions/GlitchMemories.glsl
// 完整许可见 GL-TRANSITIONS-LICENSE.txt；仅将纹理接口适配为 WebGL 2。
const vertex = `#version 300 es
in vec2 position;
out vec2 uv;
void main() {
  uv = vec2(position.x * .5 + .5, .5 - position.y * .5);
  gl_Position = vec4(position, 0., 1.);
}`;
const fragment = `#version 300 es
precision highp float;
uniform sampler2D fromImage;
uniform sampler2D toImage;
uniform float progress;
in vec2 uv;
out vec4 color;
vec4 getFromColor(vec2 p) { return texture(fromImage, p); }
vec4 getToColor(vec2 p) { return texture(toImage, p); }
vec4 transition(vec2 p) {
  vec2 block = floor(p.xy / vec2(16));
  vec2 uv_noise = block / vec2(64);
  uv_noise += floor(vec2(progress) * vec2(1200.0, 3500.0)) / vec2(64);
  vec2 dist = progress > 0.0 ? (fract(uv_noise) - 0.5) * 0.3 * (1.0 - progress) : vec2(0.0);
  vec2 red = p + dist * 0.2;
  vec2 green = p + dist * .3;
  vec2 blue = p + dist * .5;
  vec4 center = mix(getFromColor(green), getToColor(green), progress);
  return vec4(mix(getFromColor(red), getToColor(red), progress).r, center.g,
    mix(getFromColor(blue), getToColor(blue), progress).b, center.a);
}
void main() { color = transition(uv); }`;

export function glitchShader(canvas: OffscreenCanvas) {
  const gl = canvas.getContext('webgl2', {premultipliedAlpha: true});
  if (!gl) throw new Error('WebGL 2 is required for this transition');
  function compile(type: number, source: string) {
    const shader = gl!.createShader(type)!;
    gl!.shaderSource(shader, source);
    gl!.compileShader(shader);
    if (!gl!.getShaderParameter(shader, gl!.COMPILE_STATUS)) {
      const message = gl!.getShaderInfoLog(shader);
      gl!.deleteShader(shader);
      throw new Error(`Transition shader compilation failed: ${message}`);
    }
    return shader;
  }
  const program = gl.createProgram()!;
  const shaders = [compile(gl.VERTEX_SHADER, vertex), compile(gl.FRAGMENT_SHADER, fragment)];
  for (const shader of shaders) gl.attachShader(program, shader);
  gl.linkProgram(program);
  for (const shader of shaders) gl.deleteShader(shader);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(`Transition shader linking failed: ${gl.getProgramInfoLog(program)}`);
  const vao = gl.createVertexArray();
  gl.bindVertexArray(vao);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'position');
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const textures = [0, 1].map(() => {
    const texture = gl.createTexture()!;
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    return texture;
  });
  const from = gl.getUniformLocation(program, 'fromImage');
  const to = gl.getUniformLocation(program, 'toImage');
  const progress = gl.getUniformLocation(program, 'progress');
  return {
    draw(prev: OffscreenCanvas, next: OffscreenCanvas, time: number) {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.useProgram(program);
      gl.bindVertexArray(vao);
      for (const [i, source] of [prev, next].entries()) {
        gl.activeTexture(gl.TEXTURE0 + i);
        gl.bindTexture(gl.TEXTURE_2D, textures[i]);
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
      }
      gl.uniform1i(from, 0);
      gl.uniform1i(to, 1);
      gl.uniform1f(progress, time);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    },
    cleanup() {
      for (const texture of textures) gl.deleteTexture(texture);
      gl.deleteBuffer(buffer);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(program);
    },
  };
}
