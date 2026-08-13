export function demoRequest(
  _path: string,
  _options: RequestInit = {},
): Promise<Response> {
  return Promise.reject(new Error("演示接口未包含在正式构建中"));
}
