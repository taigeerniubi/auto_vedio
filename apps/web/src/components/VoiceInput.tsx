// 录音仅转写到输入框，由用户检查后发送。
import { useEffect, useRef, useState } from "react";
import { api, projectPath } from "../api";
import { Button } from "./Ui";
import type { Editor } from "../hooks/useEditor";

export function VoiceInput({
  editor: e,
  onText,
  onStart,
}: {
  editor: Editor;
  onText: (text: string) => void;
  onStart: () => void;
}) {
  const [state, setState] = useState<
    "idle" | "asking" | "recording" | "transcribing"
  >("idle");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const cancelled = useRef(false);
  const generation = useRef(0);
  const alive = useRef(true);
  const timer = useRef<number | undefined>(undefined);
  const abort = useRef<AbortController | null>(null);
  const stop = (cancel: boolean) => {
    cancelled.current = cancel;
    window.clearTimeout(timer.current);
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    if (cancel) {
      generation.current++;
      abort.current?.abort();
      setState("idle");
    }
  };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      generation.current++;
      cancelled.current = true;
      window.clearTimeout(timer.current);
      recorder.current?.state === "recording" && recorder.current.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
      abort.current?.abort();
    };
  }, []);
  async function start() {
    if (!e.project || e.busy) return;
    cancelled.current = false;
    const captureId = ++generation.current;
    setState("asking");
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === "undefined"
      )
        throw new Error("当前浏览器不支持录音，请使用文字输入。");
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (
        !alive.current ||
        cancelled.current ||
        captureId !== generation.current
      ) {
        media.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = media;
      onStart();
      const mimeType = [
        "audio/webm;codecs=opus",
        "audio/webm",
        "audio/mp4",
      ].find((type) => MediaRecorder.isTypeSupported(type));
      const current = new MediaRecorder(
        media,
        mimeType ? { mimeType } : undefined,
      );
      recorder.current = current;
      const chunks: Blob[] = [];
      current.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      current.onerror = () => {
        if (captureId === generation.current) {
          stop(true);
          e.report(new Error("录音失败，请检查麦克风后重试。"));
        }
      };
      const pid = e.project.project_id;
      current.onstop = async () => {
        media.getTracks().forEach((track) => track.stop());
        if (
          cancelled.current ||
          !alive.current ||
          captureId !== generation.current
        )
          return;
        window.clearTimeout(timer.current);
        setState("transcribing");
        abort.current = new AbortController();
        try {
          const result = await api<{ text: string }>(
            `${projectPath(pid)}/voice-inputs`,
            {
              method: "POST",
              body: new Blob(chunks, { type: current.mimeType }),
              signal: abort.current.signal,
            },
          );
          if (
            alive.current &&
            !cancelled.current &&
            captureId === generation.current
          )
            onText(result.text);
        } catch (error) {
          if (
            alive.current &&
            !cancelled.current &&
            captureId === generation.current
          )
            e.report(error);
        } finally {
          if (
            alive.current &&
            !cancelled.current &&
            captureId === generation.current
          )
            setState("idle");
        }
      };
      current.start(1000);
      setState("recording");
      timer.current = window.setTimeout(() => stop(false), 120000);
    } catch (error) {
      if (alive.current && captureId === generation.current) {
        stream.current?.getTracks().forEach((track) => track.stop());
        setState("idle");
        e.report(error);
      }
    }
  }
  return (
    <div className="voice-input">
      {state === "idle" ? (
        <Button
          disabled={!!e.busy || !e.project || !e.perception?.speech.configured}
          onClick={() => void start()}
        >
          录音输入
        </Button>
      ) : (
        <>
          <span role="status">
            {state === "recording"
              ? "● 正在录音 · 最长两分钟"
              : state === "asking"
                ? "等待麦克风权限…"
                : "正在转写…"}
          </span>
          {state === "recording" && (
            <Button onClick={() => stop(false)}>结束并转写</Button>
          )}
          <Button onClick={() => stop(true)}>取消录音</Button>
        </>
      )}
    </div>
  );
}
