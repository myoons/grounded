import { isIP } from "node:net";

export function sourceLink(value) {
  const url = new URL(value);
  const host = url.hostname.toLowerCase().replace(/\.$/, "");
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password ||
      (url.port && !['80', '443'].includes(url.port)) || !host.includes('.') ||
      host.endsWith('.local') || host.endsWith('.localhost') || host.endsWith('.internal') ||
      host.endsWith('.lan') || isIP(host))
    throw Error('Only public source websites can be opened.');
  return url.href;
}
