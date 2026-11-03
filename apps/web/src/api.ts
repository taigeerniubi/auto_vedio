// 封装工作台请求、错误解析和日志请求编号传递。
export class ApiError extends Error {
  constructor(
    message: string,
    public code = "",
    public requestId = "",
    public status = 0,
  ) {
    super(message);
  }
}

/** 发送请求并保留服务端返回的诊断编号。
 * Args:
 *   path: 同源接口地址。
 *   options: 请求方法和正文等选项。
 * Returns:
 *   已解析的业务结果。
 */
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError("无法连接服务，请检查后端是否已启动。", "NETWORK_ERROR");
  }
  const requestId = response.headers.get("X-Request-ID") || "";
  let data;
  try {
    data = await response.json();
  } catch {
    throw new ApiError(
      "服务未返回有效数据，请检查后端运行状态。",
      "INVALID_RESPONSE",
      requestId,
      response.status,
    );
  }
  if (!response.ok || data.ok === false) {
    throw new ApiError(
      data.summary ||
        (typeof data.detail === "string"
          ? data.detail
          : "请求失败，请检查输入。"),
      data.error_code || "",
      requestId,
      response.status,
    );
  }
  return data as T;
}

/** 发送 JSON 操作请求。
 * Args:
 *   path: 接口地址。
 *   body: 请求参数。
 * Returns:
 *   解析后的操作结果。
 */
export const post = <T>(path: string, body: unknown = {}) =>
  api<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
export const projectPath = (id: string) =>
  `/api/v1/projects/${encodeURIComponent(id)}`;
export const thumbnailUrl = (pid: string, aid: string) =>
  `${projectPath(pid)}/assets/${encodeURIComponent(aid)}/thumbnail`;
