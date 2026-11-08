// 在单次 highp 浮点着色中完成调色，最后才量化为 SDR 显示像素。
import {
  COLOR_PRESETS,
  CURVE_NAMES,
  HSL_NAMES,
  normalizeGrade,
  wheelRgb,
  WHEEL_NAMES,
  curveValue,
  type ColorSettings,
  type ColorGrade,
} from "./color";

const vertex = `#version 300 es
in vec2 aPosition; out vec2 uv;
void main(){uv=aPosition*.5+.5;gl_Position=vec4(aPosition,0.,1.);}`;
const fragment = `#version 300 es
precision highp float;
uniform sampler2D sourceImage;
uniform highp sampler2D curveMap;
uniform vec4 basic[6];
uniform vec3 wheels[8];
uniform vec3 channels[16];
uniform int legacy;
uniform float intensity;
in vec2 uv; out vec4 outColor;
const vec3 L=vec3(.2126,.7152,.0722);
float luminance(vec3 c){return dot(c,L);}
vec3 decode(vec3 c){return mix(c/12.92,pow(max((c+.055)/1.055,vec3(0)),vec3(2.4)),step(vec3(.04045),c));}
vec3 encode(vec3 c){return mix(c*12.92,1.055*pow(max(c,vec3(0)),vec3(1./2.4))-.055,step(vec3(.0031308),c));}
float curve(float value,int row){
  float x=clamp(value,0.,1.)*1023.; int i=min(1022,int(floor(x)));
  float a=texelFetch(curveMap,ivec2(i,row),0).r, b=texelFetch(curveMap,ivec2(i+1,row),0).r;
  return mix(a,b,x-float(i))+(value-clamp(value,0.,1.))*(b-a)*1023.;
}
vec3 rgb2hsl(vec3 c){
  float hi=max(c.r,max(c.g,c.b)),lo=min(c.r,min(c.g,c.b)),d=hi-lo,l=(hi+lo)*.5,h=0.,s=0.;
  if(d>.000001){s=d/max(.000001,1.-abs(2.*l-1.));
    h=hi==c.r ? mod((c.g-c.b)/d,6.) : hi==c.g ? (c.b-c.r)/d+2. : (c.r-c.g)/d+4.;h=fract(h/6.);}
  return vec3(h,s,l);
}
vec3 hsl2rgb(vec3 hsl){
  vec3 rgb=clamp(abs(mod(hsl.x*6.+vec3(0,4,2),6.)-3.)-1.,0.,1.);
  float c=(1.-abs(2.*hsl.z-1.))*hsl.y;return (rgb-.5)*c+hsl.z;
}
vec3 grade(vec3 c,int stage){
  vec4 a=basic[stage*3],b=basic[stage*3+1],d=basic[stage*3+2];
  if(d.z<.5)return c;
  c=decode(c)*exp2(a.x)*exp2(vec3(a.z*.45+a.w*.15,-a.w*.25,-a.z*.45+a.w*.15));
  c=encode(c);c=(c-.5)*a.y+.5;
  float lum=luminance(c), sh=1.-smoothstep(0.,.6,lum),hi=smoothstep(.4,1.,lum),mid=max(0.,1.-sh-hi);
  c+=vec3(b.w*sh*.35+b.z*hi*.35+d.x*pow(max(lum,0.),3.)*.25+d.y*pow(max(1.-lum,0.),3.)*.25);
  c+=wheels[stage*4]*sh+wheels[stage*4+1]*mid+wheels[stage*4+2]*hi+wheels[stage*4+3];
  vec3 bounded=clamp(c,0.,1.), hsl=rgb2hsl(bounded);float originalHue=hsl.x;
  float centers[8]=float[8](0.,1./12.,1./6.,1./3.,.5,2./3.,.75,5./6.);
  vec3 change=vec3(0);float weights=0.;
  for(int i=0;i<8;i++){float distance=abs(originalHue-centers[i]);distance=min(distance,1.-distance);
    float w=1.-smoothstep(0.,i==1||i==6?1./12.:1./6.,distance);change+=channels[stage*8+i]*w;weights+=w;}
  change/=max(weights,1.);float colorMask=smoothstep(0.,.15,hsl.y);
  hsl.x=fract(hsl.x+change.x*colorMask+(curve(originalHue,stage*7+4)-.5)*colorMask);
  hsl.y*=max(0.,1.+change.y)*(curve(originalHue,stage*7+5)*2.);
  hsl.z+=change.z*.35*colorMask+(curve(originalHue,stage*7+6)-.5)*.7*colorMask;
  hsl.y=clamp(hsl.y,0.,1.);hsl.z=clamp(hsl.z,0.,1.);
  c+=hsl2rgb(hsl)-bounded;
  lum=luminance(c);float sat=max(c.r,max(c.g,c.b))-min(c.r,min(c.g,c.b));
  c=mix(vec3(lum),c,b.x*(1.+b.y*(1.-clamp(sat,0.,1.))));
  c=vec3(curve(c.r,stage*7),curve(c.g,stage*7),curve(c.b,stage*7));
  return vec3(curve(c.r,stage*7+1),curve(c.g,stage*7+2),curve(c.b,stage*7+3));
}
vec3 saturateColor(vec3 c,float amount){return mix(vec3(dot(c,vec3(.213,.715,.072))),c,amount);}
vec3 rotateHue(vec3 c,float degrees){
  float co=cos(radians(degrees)),si=sin(radians(degrees));
  return vec3(dot(c,vec3(.213+.787*co-.213*si,.715-.715*co-.715*si,.072-.072*co+.928*si)),
    dot(c,vec3(.213-.213*co+.143*si,.715+.285*co+.140*si,.072-.072*co-.283*si)),
    dot(c,vec3(.213-.213*co-.787*si,.715-.715*co+.715*si,.072+.928*co+.072*si)));
}
void main(){vec4 pixel=texture(sourceImage,uv);vec3 corrected=grade(pixel.rgb,0),look=corrected;
  if(legacy==1){look=mix(look,vec3(dot(look,vec3(.393,.769,.189)),dot(look,vec3(.349,.686,.168)),dot(look,vec3(.272,.534,.131))),.24);look=rotateHue(saturateColor(look,1.15),-8.);}
  else if(legacy==2)look=rotateHue(saturateColor(look,.85),12.);
  else if(legacy==3)look=vec3(dot(look,vec3(.2126,.7152,.0722)));
  else look=grade(look,1);
  outColor=vec4(clamp(mix(corrected,look,intensity),0.,1.),pixel.a);
}`;

export function createColorRenderer(width: number, height: number) {
  const canvas = new OffscreenCanvas(width, height);
  const gl = canvas.getContext("webgl2", {
    alpha: false,
    premultipliedAlpha: false,
    preserveDrawingBuffer: true,
  });
  if (
    !gl ||
    (gl.getShaderPrecisionFormat(gl.FRAGMENT_SHADER, gl.HIGH_FLOAT)
      ?.precision ?? 0) < 23
  )
    throw new Error("当前设备不支持浮点调色，请启用浏览器硬件加速。");
  function compile(type: number, code: string) {
    const shader = gl!.createShader(type)!;
    gl!.shaderSource(shader, code);
    gl!.compileShader(shader);
    if (!gl!.getShaderParameter(shader, gl!.COMPILE_STATUS))
      throw new Error(
        gl!.getShaderInfoLog(shader) || "Color shader compile failed",
      );
    return shader;
  }
  const vs = compile(gl.VERTEX_SHADER, vertex),
    fs = compile(gl.FRAGMENT_SHADER, fragment),
    program = gl.createProgram()!;
  gl.attachShader(program, vs);
  gl.attachShader(program, fs);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS))
    throw new Error(
      gl.getProgramInfoLog(program) || "Color shader link failed",
    );
  gl.useProgram(program);
  const buffer = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
    gl.STATIC_DRAW,
  );
  const position = gl.getAttribLocation(program, "aPosition");
  gl.enableVertexAttribArray(position);
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const texture = gl.createTexture(),
    curves = gl.createTexture();
  for (const [i, t] of [texture, curves].entries()) {
    gl.activeTexture(gl.TEXTURE0 + i);
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.texParameteri(
      gl.TEXTURE_2D,
      gl.TEXTURE_MIN_FILTER,
      i ? gl.NEAREST : gl.LINEAR,
    );
    gl.texParameteri(
      gl.TEXTURE_2D,
      gl.TEXTURE_MAG_FILTER,
      i ? gl.NEAREST : gl.LINEAR,
    );
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }
  const uniform = (name: string) => gl.getUniformLocation(program, name);
  gl.uniform1i(uniform("sourceImage"), 0);
  gl.uniform1i(uniform("curveMap"), 1);
  let lastSettings = "";
  return {
    canvas,
    draw(source: CanvasImageSource, settings: ColorSettings) {
      const key = JSON.stringify([
        settings.color_preset,
        settings.color_preset_intensity,
        settings.color_grade,
      ]);
      if (key !== lastSettings) {
        const preset = COLOR_PRESETS.find(
          (p) => p.id === settings.color_preset,
        );
        const grades = [
          normalizeGrade(settings.color_grade),
          normalizeGrade(preset?.grade as Partial<ColorGrade>),
        ];
        const basic = grades.flatMap((g) => [
          g.exposure,
          g.contrast,
          g.temperature,
          g.tint,
          g.saturation,
          g.vibrance,
          g.highlights,
          g.shadows,
          g.whites,
          g.blacks,
          g.enabled ? 1 : 0,
          0,
        ]);
        gl.uniform4fv(uniform("basic[0]"), basic);
        gl.uniform3fv(
          uniform("wheels[0]"),
          grades.flatMap((g) =>
            WHEEL_NAMES.flatMap((k) => wheelRgb(g.wheels[k])),
          ),
        );
        gl.uniform3fv(
          uniform("channels[0]"),
          grades.flatMap((g) =>
            HSL_NAMES.flatMap((k) => [
              g.hsl[k].hue / 360,
              g.hsl[k].saturation,
              g.hsl[k].luminance,
            ]),
          ),
        );
        gl.uniform1i(
          uniform("legacy"),
          preset?.legacy === "warm"
            ? 1
            : preset?.legacy === "cool"
              ? 2
              : preset?.legacy === "monochrome"
                ? 3
                : 0,
        );
        gl.uniform1f(
          uniform("intensity"),
          preset ? (settings.color_preset_intensity ?? 1) : 0,
        );
        const data = new Float32Array(1024 * 14);
        grades.forEach((g, stage) =>
          CURVE_NAMES.forEach((k, row) => {
            for (let x = 0; x < 1024; x++)
              data[(stage * 7 + row) * 1024 + x] = curveValue(
                g.curves[k],
                x / 1023,
              );
          }),
        );
        gl.activeTexture(gl.TEXTURE1);
        gl.bindTexture(gl.TEXTURE_2D, curves);
        gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
        gl.texImage2D(
          gl.TEXTURE_2D,
          0,
          gl.R32F,
          1024,
          14,
          0,
          gl.RED,
          gl.FLOAT,
          data,
        );
        lastSettings = key;
      }
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        source as TexImageSource,
      );
      gl.viewport(0, 0, width, height);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      return canvas;
    },
    cleanup() {
      gl.deleteTexture(texture);
      gl.deleteTexture(curves);
      gl.deleteBuffer(buffer);
      gl.deleteProgram(program);
      gl.deleteShader(vs);
      gl.deleteShader(fs);
      gl.getExtension("WEBGL_lose_context")?.loseContext();
    },
  };
}
