"""把画面批注锚定到已校验的工程版本、成片帧和真实素材实例。"""
from ..models import Snapshot, timeline
from ..tools import ToolError


def capture_annotation(snapshot, revision, annotation):
    """由服务端计算片段与母版位置，避免浏览器选区或缩放改变指代。"""
    if annotation is None:
        return None
    project = Snapshot.model_validate(snapshot)
    rows, total = timeline(project)
    frame = annotation.frame
    if frame >= total:
        raise ToolError("INVALID_ARGUMENTS", "批注时间点已不在成片内，请重新选择画面")
    clips = []
    for clip, row in zip(project.clips, rows):
        if not row["start_frame"] <= frame < row["end_frame"]:
            continue
        local = frame - row["start_frame"]
        source = None
        if clip.kind == "video":
            offset = local * clip.rate_num // clip.rate_den
            source = (max(clip.source_in_frame, clip.source_out_frame - 1 - offset)
                      if clip.reverse else min(clip.source_out_frame - 1, clip.source_in_frame + offset))
        clips.append({"clip_id": clip.clip_id, "asset_id": clip.asset_id,
                      "local_frame": local, "source_frame": source})
    return {**annotation.model_dump(), "revision": revision,
            "composition": {"width": project.composition.width, "height": project.composition.height,
                            "fps": project.composition.fps}, "clips": clips}
