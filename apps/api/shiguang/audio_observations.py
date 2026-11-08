"""汇集当前素材的历史音频观察，让后续任务复用已付费获取的证据。"""
import json

from .audio_query import audio_query_records


def audio_observations(store, pid, assets, limit=24, *, compact=False):
    """读取缓存及升级前断点内的成功听音结果，保留母版时间和不确定性。

    Args:
        store: 工程数据库。
        pid: 当前项目编号。
        assets: 当前工程可用的素材映射，已排除移除素材。
        limit: 注入模型上下文的最多观察数量。
        compact: 仅缩短默认上下文；完整结果仍可由素材分析工具读取。

    Returns:
        去重后的结构化观察；仅凭描述不补造时间标记。
    """
    observations, seen = [], set()

    def append(item):
        if item.get("asset_id") not in assets or not item.get("answer"):
            return
        key = json.dumps([item["asset_id"], item.get("query"), item.get("range"), item.get("timing")],
                         ensure_ascii=False, sort_keys=True)
        if key in seen or len(observations) >= limit:
            return
        seen.add(key)
        observations.append(item)

    for item in audio_query_records(store, pid, asset_id=next(iter(assets)) if len(assets) == 1 else None):
        append(item)
    # 旧版已有断点保存了工具结果，但没有独立听音缓存；只复用观察，不伪造缓存来源。
    with store.connect() as con:
        rows = con.execute("""SELECT r.id,json_extract(c.data,'$.trace') AS trace
            FROM agent_checkpoints c JOIN agent_runs r ON r.id=c.run_id
            WHERE r.project_id=? ORDER BY r.rowid DESC LIMIT 100""", (pid,)).fetchall()
    for row in rows:
        for entry in reversed(json.loads(row["trace"] or "[]")):
            if entry.get("tool") != "query_audio" or not entry.get("result", {}).get("ok"):
                continue
            data = entry["result"].get("data", {})
            args = entry.get("arguments", {})
            append({**data, "query": args.get("query", ""), "source_run_id": row["id"],
                    "provenance": "historical_tool_observation"})
    if not compact:
        return observations
    return [{**item, "answer": item["answer"][:1000],
             "matches": [{**mark, "evidence": mark.get("evidence", "")[:120]} for mark in item.get("matches", [])[:12]],
             "matches_total": len(item.get("matches", [])),
             "details_truncated": len(item["answer"]) > 1000 or len(item.get("matches", [])) > 12}
            for item in observations]
