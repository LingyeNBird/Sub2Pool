export interface WebRtcNetworkInfo {
  webrtc_supported: boolean;
  webrtc_ips: string[];
}

function candidateAddress(candidate: RTCIceCandidate): string | null {
  if (candidate.address) return candidate.address;
  const parts = candidate.candidate.split(/\s+/);
  return parts.length > 4 ? parts[4] : null;
}

function isIpAddress(value: string): boolean {
  const ipv4 = /^(?:\d{1,3}\.){3}\d{1,3}$/;
  const ipv6 = /^[0-9a-f:]+$/i;
  return ipv4.test(value) || (value.includes(":") && ipv6.test(value));
}

export async function collectWebRtcNetworkInfo(
  enabled: boolean,
  stunUrl: string,
): Promise<WebRtcNetworkInfo> {
  if (!enabled || !("RTCPeerConnection" in window)) {
    return { webrtc_supported: false, webrtc_ips: [] };
  }

  const addresses = new Set<string>();
  const peer = new RTCPeerConnection({
    iceServers: stunUrl ? [{ urls: stunUrl }] : [],
  });
  peer.createDataChannel("network-audit");

  try {
    let finish: (() => void) | undefined;
    const completed = new Promise<void>((resolve) => {
      finish = resolve;
    });
    const timeout = window.setTimeout(() => finish?.(), 1500);
    peer.onicecandidate = (event) => {
      if (!event.candidate) {
        window.clearTimeout(timeout);
        finish?.();
        return;
      }
      const address = candidateAddress(event.candidate);
      // 浏览器常用 *.local 隐藏局域网地址；后端同样会再次严格校验。
      if (address && isIpAddress(address)) addresses.add(address);
    };
    const offer = await peer.createOffer();
    await peer.setLocalDescription(offer);
    await completed;
  } catch {
    // WebRTC 被策略或扩展阻止时仍可正常登录，服务端 IP 继续作为主审计地址。
  } finally {
    peer.close();
  }

  return {
    webrtc_supported: true,
    webrtc_ips: [...addresses].slice(0, 8),
  };
}
