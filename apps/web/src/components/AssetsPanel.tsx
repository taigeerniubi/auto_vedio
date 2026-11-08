// 展示素材库并提供素材导入、文字、转场和调色操作。
import { useEffect, useRef, useState, type RefObject } from "react";
import { api, ApiError, projectPath, thumbnailUrl } from "../api";
import type { Asset, LibraryFile, TextItem } from "../contracts";
import type { Editor } from "../hooks/useEditor";
import { duration, layout, timecode } from "../timeline";
import { Button, Icon, Modal } from "./Ui";
import { AssetAnalysis } from "./AssetAnalysis";
import { AudioPreview } from "./AudioPreview";
import { TransitionsPanel } from "./TransitionsPanel";

export type Section = "assets" | "audio" | "text" | "transitions";

/** 渲染素材与效果侧栏。
 * Args:
 *   editor: 编辑器状态与动作。
 *   section: 当前工具分区。
 * Returns:
 *   可搜索、导入和编辑的素材面板。
 */
export function AssetsPanel({
  editor: e,
  section,
  audioPlayer,
  onAudioPlay,
  onTransitionPreview,
}: {
  editor: Editor;
  section: Section;
  audioPlayer: RefObject<HTMLAudioElement | null>;
  onAudioPlay: () => void;
  onTransitionPreview: (frame: number) => void;
}) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [showLibrary, setShowLibrary] = useState(false);
  const [library, setLibrary] = useState<LibraryFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState<string[]>([]);
  const [analysisAsset, setAnalysisAsset] = useState<string | null>(null);
  const [removing, setRemoving] = useState<Asset | null>(null);
  const [audition, setAudition] = useState<{
    id: string; name: string; src: string; scope: "project" | "library";
  } | null>(null);
  const [auditionPlaying, setAuditionPlaying] = useState(false);
  const auditionSource = useRef(audition?.src);
  auditionSource.current = audition?.src;
  const fileInput = useRef<HTMLInputElement>(null);
  const snapshot = e.project?.snapshot;
  const clip = snapshot?.clips.find((c) => c.clip_id === e.selected);
  const total = snapshot ? layout(snapshot).total : 0;
  const disabled = !!e.busy || !e.project;

  useEffect(() => {
    setAudition(null);
    setAuditionPlaying(false);
  }, [e.project?.project_id, section, showLibrary, analysisAsset, search, filter]);

  useEffect(() => {
    if (audition?.scope === "project" && !e.assets.some((asset) => asset.asset_id === audition.id)) setAudition(null);
  }, [e.assets, audition]);

  function listen(next: NonNullable<typeof audition>) {
    if (audition?.src === next.src && audioPlayer.current) {
      if (audioPlayer.current.paused) {
        void audioPlayer.current.play().catch((error: DOMException) => {
          if (auditionSource.current === next.src && error.name !== "AbortError") e.report(error);
        });
      } else audioPlayer.current.pause();
    } else {
      setAuditionPlaying(false);
      setAudition(next);
    }
  }

  const isPlaying = (scope: "project" | "library", id: string) =>
    auditionPlaying && audition?.scope === scope && audition.id === id;

  useEffect(() => {
    if (!showLibrary) return;
    let active = true;
    setLoading(true);
    setChecked([]);
    api<LibraryFile[]>("/api/v1/library")
      .then(files => { if (active) setLibrary(files); })
      .catch(error => { if (active) e.report(error); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [showLibrary]);

  const libraryId = (file: LibraryFile) => `${file.project_id}:${file.asset_id}`;

  const media = e.assets.filter(
    (a) =>
      (!search ||
        `${a.name} ${JSON.stringify(e.analyses[a.asset_id]?.segments || [])} ${JSON.stringify(e.analyses[a.asset_id]?.speech.utterances || [])}`
          .toLowerCase()
          .includes(search.toLowerCase())) &&
      (section === "audio"
        ? a.kind === "audio"
        : filter === "all" || a.kind === filter),
  );

  return (
    <section className="asset-panel" aria-label="素材与效果">
      <div className="panel-heading">
        <h2>
          {
            {
              assets: "项目素材",
              audio: "配乐与声音",
              text: "文字与标题",
              transitions: "片段转场",
            }[section]
          }
        </h2>
        {(section === "assets" || section === "audio") && (
          <div className="asset-actions">
            <Button
              icon="plus"
              disabled={disabled}
              onClick={() => setShowLibrary(true)}
            >
              导入素材
            </Button>
            <button
              className="quick-upload"
              title="从文件导入"
              aria-label="从文件导入"
              disabled={disabled}
              onClick={() => fileInput.current?.click()}
            >
              <Icon name="folder" />
            </button>
          </div>
        )}
      </div>
      {section === "assets" || section === "audio" ? (
        <>
          <input
            ref={fileInput}
            hidden
            type="file"
            multiple
            accept="video/*,audio/*,.png,.jpg,.jpeg"
            onChange={(event) => {
              const files = Array.from(event.target.files || []);
              if (files.length)
                void e.importFiles(files, () => setShowLibrary(false));
              event.target.value = "";
            }}
          />
          <label className="search">
            <input
              aria-label="搜索素材"
              placeholder="搜索文件名、画面或对白…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <Icon name="search" />
          </label>
          {section === "assets" && (
            <div className="filter-tabs">
              {[
                ["all", "全部"],
                ["video", "视频"],
                ["image", "图片"],
              ].map(([value, label]) => (
                <button
                  key={value}
                  aria-pressed={filter === value}
                  className={filter === value ? "active" : ""}
                  onClick={() => setFilter(value)}
                >
                  {label}
                  {value === "all" ? ` ${e.assets.length}` : ""}
                </button>
              ))}
            </div>
          )}
          <div className="asset-grid">
            {media.map((asset) => (
              <article
                className="asset-card"
                key={asset.asset_id}
                draggable={!disabled}
                onDragStart={(event) =>
                  event.dataTransfer.setData(
                    "application/shiguang-asset",
                    asset.asset_id,
                  )
                }
              >
                <button
                  className="asset-thumb"
                  disabled={asset.kind === "audio" ? false : disabled}
                  onClick={asset.kind === "audio" ? () => listen({
                    id: asset.asset_id, name: asset.name, scope: "project",
                    src: `${projectPath(e.project!.project_id)}/assets/${asset.asset_id}/file`,
                  }) : undefined}
                  onDoubleClick={asset.kind === "audio" ? undefined : () => void e.addAsset(asset)}
                  onKeyDown={(event) => {
                    if (asset.kind !== "audio" && event.key === "Enter") void e.addAsset(asset);
                  }}
                  data-audio-toggle={asset.kind === "audio" || undefined}
                  aria-pressed={asset.kind === "audio" ? isPlaying("project", asset.asset_id) : undefined}
                  aria-label={`${asset.kind === "audio" ? (isPlaying("project", asset.asset_id) ? "暂停试听" : "试听") : "素材"} ${asset.name}`}
                >
                  {asset.kind === "audio" ? (
                    <div className="audio-art">
                      <Icon name={isPlaying("project", asset.asset_id) ? "pause" : "play"} />
                    </div>
                  ) : (
                    <img
                      loading="lazy"
                      alt={asset.name}
                      src={thumbnailUrl(e.project!.project_id, asset.asset_id)}
                    />
                  )}
                  <span>
                    {asset.kind === "image"
                      ? "图片"
                      : timecode(asset.total_frames).slice(0, 5)}
                  </span>
                </button>
                <div className="asset-info">
                  <div className="asset-name" title={asset.name}>
                    {asset.name}
                  </div>
                  <div className="asset-meta">
                    <small>
                      {asset.kind === "audio"
                        ? (isPlaying("project", asset.asset_id) ? "正在试听" : "点击封面试听")
                        : `${asset.width} × ${asset.height}`}
                    </small>
                    <button
                      className="analysis-link"
                      onClick={() => setAnalysisAsset(asset.asset_id)}
                    >
                      {e.analyses[asset.asset_id] ? "查看分析" : "分析素材"}
                    </button>
                  </div>
                </div>
                <div className="asset-card-actions">
                  <button
                    className="asset-add"
                    title={`添加 ${asset.name} 到时间线`}
                    aria-label={`添加素材 ${asset.name}`}
                    disabled={disabled}
                    onClick={() => void e.addAsset(asset)}
                  >
                    <Icon name="plus" />
                  </button>
                  <button
                    className="asset-add asset-remove"
                    title={`删除素材 ${asset.name}`}
                    aria-label={`删除素材 ${asset.name}`}
                    disabled={disabled}
                    onClick={() => setRemoving(asset)}
                  >
                    <Icon name="trash" />
                  </button>
                </div>
              </article>
            ))}
          </div>
          {!media.length && (
            <div className="empty-small">
              <Icon name="folder" />
              <p>{e.assets.length ? "没有匹配的素材" : "素材库为空"}</p>
              <span>
                {e.assets.length
                  ? "可以调整筛选条件或搜索词。"
                  : "导入视频、图片或配乐后，可添加到时间线。"}
              </span>
            </div>
          )}
        </>
      ) : section === "text" ? (
        <div className="effect-content">
          <div className="subtitle-actions">
            <h3>自动对白字幕</h3>
            <p className="muted">
              先在素材分析中识别对白，再生成字幕；文字可在下方修改。
            </p>
            <Button
              disabled={disabled || !total}
              onClick={() =>
                void e.edit("generate_subtitles", {
                  clip_ids: clip ? [clip.clip_id] : [],
                })
              }
            >
              {clip ? "为选中片段生成字幕" : "为所有视频生成字幕"}
            </Button>
          </div>
          <p className="muted">为画面留下一句话。</p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              void e.edit("add_text", {
                text: String(form.get("text")),
                anchor: clip
                  ? {
                      kind: "clip",
                      clip_id: clip.clip_id,
                      end_frame: duration(clip),
                    }
                  : { end_frame: Math.min(total, 150) },
                style_preset: "title",
                font_size: 64,
              });
            }}
          >
            <textarea
              name="text"
              aria-label="标题内容"
              placeholder="输入你的标题…"
              required
              maxLength={1000}
              rows={3}
            />
            <Button type="submit" icon="text" disabled={disabled || !total}>
              添加标题
            </Button>
            <small>
              {clip ? "显示在选中的片段上" : "显示在时间线开头，最长 5 秒"}
            </small>
          </form>
          {snapshot?.text_items.map((item) => (
            <TextEditor
              key={`${item.text_id}-${e.project!.revision}`}
              item={item}
              editor={e}
            />
          ))}
        </div>
      ) : section === "transitions" ? (
        <TransitionsPanel editor={e} onPreview={onTransitionPreview} />
      ) : null}
      {analysisAsset &&
        e.project &&
        e.assets.some((a) => a.asset_id === analysisAsset) && (
          <AssetAnalysis
            key={analysisAsset}
            editor={e}
            aid={analysisAsset}
            onClose={() => setAnalysisAsset(null)}
          />
        )}
      {removing && (
        <Modal title="删除项目素材" onClose={() => { if (!e.busy) setRemoving(null); }}>
          <p>从项目删除「{removing.name}」？</p>
          <p className="muted">
            同时移除该素材在时间线上的片段、配乐及关联效果。可通过撤销恢复，磁盘原文件会保留。
          </p>
          {e.error && <p role="alert" className="failure-text">{e.error.message}</p>}
          <footer>
            <Button disabled={disabled} onClick={() => setRemoving(null)}>取消</Button>
            <Button disabled={disabled} icon="trash" onClick={async () => {
              if (await e.edit("remove_asset", { asset_id: removing.asset_id })) setRemoving(null);
            }}>确认删除</Button>
          </footer>
        </Modal>
      )}
      {showLibrary && (
        <Modal
          title="导入素材"
          onClose={() => setShowLibrary(false)}
        >
          <p className="muted">
            选择之前导入过的素材，或从文件添加新素材。
          </p>
          {e.error && (
            <p role="alert" className="failure-text">
              {e.error.message}
            </p>
          )}
          {e.busy && <p role="status">{e.busy}，正在处理，请稍候…</p>}
          <button className="library-upload-card" disabled={!!e.busy}
            onClick={() => fileInput.current?.click()}>
            <Icon name="folder" />
            <span><strong>从文件导入</strong><small>选择视频、图片或音频，可多选</small></span>
            <Icon name="plus" />
          </button>
          {!loading && <h3 className="library-history-heading">导入历史{library.length ? ` · ${library.length}` : ""}</h3>}
          <div className="library-list">
            {loading ? (
              <p>正在读取导入历史…</p>
            ) : library.length ? (
              library.map((file) => (
                <div className="library-row" key={libraryId(file)}>
                <label>
                  <input
                    type="checkbox"
                    disabled={!!e.busy}
                    checked={checked.includes(libraryId(file))}
                    onChange={(event) =>
                      setChecked((old) =>
                        event.target.checked
                          ? [...old, libraryId(file)]
                          : old.filter((id) => id !== libraryId(file)),
                      )
                    }
                  />
                  {file.kind === "audio" ? <span className="library-history-thumb"><Icon name="music" /></span> :
                    <img className="library-history-thumb" loading="lazy" alt="" src={thumbnailUrl(file.project_id, file.asset_id)} />}
                  <span>
                    <strong>{file.name}</strong>
                    <small>{file.kind === "audio" ? "音频" : file.kind === "video" ? "视频" : "图片"}
                      {file.kind === "image" ? ` · ${file.width} × ${file.height}` : ` · ${timecode(file.total_frames).slice(0, 5)}`}</small>
                  </span>
                  <small>{(file.size / 1024 / 1024).toFixed(1)} MB</small>
                </label>
                {file.kind === "audio" && (
                  <Button icon={isPlaying("library", libraryId(file)) ? "pause" : "play"}
                    aria-pressed={isPlaying("library", libraryId(file))}
                    aria-label={`${isPlaying("library", libraryId(file)) ? "暂停试听" : "试听"} ${file.name}`} onClick={() => listen({
                    id: libraryId(file), name: file.name, scope: "library",
                    src: `${projectPath(file.project_id)}/assets/${file.asset_id}/file`,
                  })}>{isPlaying("library", libraryId(file)) ? "暂停" : "试听"}</Button>
                )}
                </div>
              ))
            ) : (
              <p className="library-empty">还没有导入记录。点击上方“从文件导入”，添加你的第一份素材。</p>
            )}
          </div>
          <footer>
            <Button
              disabled={!!e.busy}
              onClick={() => setShowLibrary(false)}
            >
              取消
            </Button>
            <Button
              tone="primary"
              disabled={!checked.length || !!e.busy}
              onClick={() => {
                void e.importFiles(library.filter(file => checked.includes(libraryId(file))), () => setShowLibrary(false));
              }}
            >
              {e.busy || `导入所选 ${checked.length || ""}`}
            </Button>
          </footer>
        </Modal>
      )}
      {audition && (
        <AudioPreview key={audition.src} name={audition.name} src={audition.src} audio={audioPlayer}
          onPlayingChange={(playing) => {
            if (auditionSource.current !== audition.src) return;
            setAuditionPlaying(playing);
            if (playing) onAudioPlay();
          }}
          onError={() => {
            if (auditionSource.current !== audition.src) return;
            setAuditionPlaying(false);
            e.report(new ApiError(`无法试听「${audition.name}」，请检查音频文件后重试。`));
          }} />
      )}
    </section>
  );
}

/** 编辑已有标题的内容并保留位置和样式。
 * Args:
 *   item: 标题对象。
 *   editor: 工程编辑动作。
 * Returns:
 *   标题编辑表单。
 */
function TextEditor({ item, editor: e }: { item: TextItem; editor: Editor }) {
  return (
    <form
      className="text-item"
      onSubmit={(event) => {
        event.preventDefault();
        const { text_id, ...value } = item;
        void e.edit("update_text", {
          text_id,
          value: {
            ...value,
            text: String(new FormData(event.currentTarget).get("text")),
          },
        });
      }}
    >
      <textarea
        name="text"
        aria-label="修改标题"
        defaultValue={item.text}
        required
        maxLength={1000}
      />
      <div className="row">
        <Button type="submit" disabled={!!e.busy}>
          保存文字
        </Button>
        <Button
          icon="trash"
          aria-label="删除标题"
          disabled={!!e.busy}
          onClick={() => void e.edit("remove_text", { text_id: item.text_id })}
        />
      </div>
    </form>
  );
}
