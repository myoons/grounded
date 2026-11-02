export function trustedMeetingFrame(
  contents,
  expected,
  origin,
  requestingOrigin,
  details,
) {
  if (!contents || contents !== expected || details?.isMainFrame !== true)
    return false;
  try {
    return (
      new URL(requestingOrigin).origin === origin &&
      new URL(details.requestingUrl).origin === origin
    );
  } catch {
    return false;
  }
}

export function allowsMeetingPermission(permission, details) {
  if (permission !== "media") return false;
  if (details.mediaTypes)
    return details.mediaTypes.length === 1 && details.mediaTypes[0] === "audio";
  return ["audio", "unknown"].includes(details.mediaType);
}
