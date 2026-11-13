// 画面批注只收集定位信息，提交仍走当前会话的统一剪辑指令入口。
import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent } from "react";
import { createPortal } from "react-dom";
import type { AnnotationRegion, FrameAnnotation } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { timecode } from "../timeline";
import { Button, Icon } from "./Ui";
import { VoiceInput } from "./VoiceInput";
import "../annotations.css";

type Point = { x: number; y: number };
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const rounded = (value: number) => Math.round(value * 1e6) / 1e6;

export function FrameAnnotator({ editor, frame, onClose, onPause }: {
  editor: Editor; frame: number; onClose: () => void; onPause: () => void;
}) {
  const surface = useRef<HTMLDivElement>(null);
  const form = useRef<HTMLFormElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const drag = useRef<{ pointerId: number; start: Point } | null>(null);
  const [region, setRegion] = useState<AnnotationRegion | null>(null);
  const [selection, setSelection] = useState<FrameAnnotation | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [recording, setRecording] = useState(false);
  const [hint, setHint] = useState("拖动框选想修改的位置");
  const [position, setPosition] = useState({ left: 12, top: 12, width: 352 });

  // 外部时间线仍可定位；换帧后保留文字，但必须重新选择区域。
  useEffect(() => {
    drag.current = null;
    setRegion(null);
    setSelection(null);
    setHint("拖动框选想修改的位置");
  }, [frame]);
  useEffect(() => { surface.current?.focus({ preventScroll: true }); }, []);
  useEffect(() => {
    if (selection) input.current?.focus({ preventScroll: true });
  }, [selection]);
  useLayoutEffect(() => {
    if (!input.current) return;
    input.current.style.height = "auto";
    input.current.style.height = `${Math.min(120, Math.max(42, input.current.scrollHeight))}px`;
  }, [text, selection]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || event.isComposing) return;
      // 弹窗有自己的 Esc 行为；批注不抢占模型设置等弹窗。
      if (document.querySelector("dialog[open]")) return;
      event.preventDefault();
      onClose();
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);

  // 输入框挂到页面顶层，避免窄预览和竖屏素材将其裁掉；跟随滚动和面板缩放。
  useLayoutEffect(() => {
    if (!selection || !surface.current || !form.current) return;
    const update = () => {
      if (!surface.current || !form.current) return;
      const box = surface.current.getBoundingClientRect();
      const width = Math.min(352, window.innerWidth - 24);
      const height = form.current.offsetHeight;
      const edge = box.left + (selection.region.x + selection.region.width) * box.width;
      const bottom = box.top + (selection.region.y + selection.region.height) * box.height;
      const rightFits = edge + width + 24 <= window.innerWidth;
      setPosition({
        width,
        left: clamp(rightFits ? edge + 12 : edge - width, 12, window.innerWidth - width - 12),
        top: clamp(rightFits ? bottom - 24 : bottom + 12, 12, window.innerHeight - height - 12),
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(surface.current);
    observer.observe(form.current);
    window.addEventListener("resize", update);
    document.addEventListener("scroll", update, true);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      document.removeEventListener("scroll", update, true);
    };
  }, [selection]);

  function point(event: PointerEvent<HTMLDivElement>): Point {
    const rect = event.currentTarget.getBoundingClientRect();
    return { x: clamp((event.clientX - rect.left) / rect.width, 0, 1),
      y: clamp((event.clientY - rect.top) / rect.height, 0, 1) };
  }
  function rectangle(start: Point, end: Point): AnnotationRegion {
    return { x: rounded(Math.min(start.x, end.x)), y: rounded(Math.min(start.y, end.y)),
      width: rounded(Math.abs(end.x - start.x)), height: rounded(Math.abs(end.y - start.y)) };
  }
  function reselect() {
    setRegion(null);
    setSelection(null);
    surface.current?.focus({ preventScroll: true });
  }
  function selectFullFrame() {
    const full = { x: 0, y: 0, width: 1, height: 1 };
    setRegion(full);
    setSelection({ frame, region: full });
  }
  async function submit() {
    if (!selection || selection.frame !== frame || !text.trim() || sending || recording || editor.busy ||
        !editor.model?.configured || !editor.conversation.ready) return;
    setSending(true);
    try { await editor.command(text.trim(), onClose, undefined, selection); }
    finally { setSending(false); }
  }
  return <>
    <div className="annotation-surface" data-annotation-ui ref={surface} role="group" tabIndex={-1}
      aria-label="画面批注区域" aria-describedby="annotation-help"
      onPointerDown={event => {
        if (event.button !== 0 || !event.isPrimary || editor.busy || (event.target as Element).closest("button")) return;
        event.preventDefault();
        onPause();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointerId: event.pointerId, start: point(event) };
        setSelection(null);
        setRegion(null);
      }}
      onPointerMove={event => {
        if (drag.current?.pointerId === event.pointerId) setRegion(rectangle(drag.current.start, point(event)));
      }}
      onPointerUp={event => {
        if (drag.current?.pointerId !== event.pointerId) return;
        const next = rectangle(drag.current.start, point(event));
        drag.current = null;
        event.currentTarget.releasePointerCapture(event.pointerId);
        const bounds = event.currentTarget.getBoundingClientRect();
        if (next.width * bounds.width < 8 || next.height * bounds.height < 8) {
          setRegion(null);
          setHint("拖动选出一块区域，或选择整个画面");
          return;
        }
        setRegion(next);
        setSelection({ frame, region: next });
      }}
      onPointerCancel={() => { drag.current = null; reselect(); }}>
      <span id="annotation-help" className="sr-only">拖动框选；也可使用“批注整个画面”按钮。Escape 退出，切换时间点后重新框选。</span>
      {!region && <div className="annotation-hint" role="status">
        <span><Icon name="annotation" />{hint}</span>
        <Button tone="ghost" onClick={selectFullFrame} disabled={!!editor.busy}>批注整个画面</Button>
      </div>}
      {region && <div className="annotation-selection" style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`,
        width: `${region.width * 100}%`, height: `${region.height * 100}%` }}>
        {selection && <span className="annotation-pin"><Icon name="annotation" /></span>}
      </div>}
    </div>
    {selection && createPortal(<form className="annotation-composer" data-annotation-ui ref={form}
      aria-label="添加画面批注" style={position} onSubmit={event => { event.preventDefault(); void submit(); }}>
      <header>
        <span><Icon name="annotation" />画面批注 <time>{timecode(selection.frame)}</time></span>
        <Button tone="ghost" icon="close" aria-label="取消批注" onClick={onClose} />
      </header>
      <div className="annotation-entry">
      <textarea ref={input} aria-label="批注内容" placeholder="添加批注，比如给这里加字幕…"
        value={text} rows={1} maxLength={8000} disabled={sending || !!editor.busy}
        onChange={event => setText(event.target.value)} onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); }
        }} />
        <div className="annotation-input-actions">
          <VoiceInput editor={editor} compact onStart={onPause} onBusyChange={setRecording}
            onText={value => setText(old => old ? `${old}\n${value}` : value)} />
          <Button type="submit" tone="primary" icon="arrow" aria-label="发送批注"
            disabled={!text.trim() || sending || recording || !!editor.busy || !editor.model?.configured || !editor.conversation.ready} />
        </div>
      </div>
      <footer>
        <Button tone="ghost" onClick={reselect} disabled={sending || !!editor.busy}>重新框选</Button>
        <span>{sending ? "正在发送到当前会话…" : !editor.model?.configured ? "连接剪辑模型后即可发送" : "与 AI 助手共用当前会话"}</span>
      </footer>
    </form>, document.body)}
  </>;
}
