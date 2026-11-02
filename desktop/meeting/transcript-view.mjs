export function transcriptView(segments) {
  const known = new Map(segments.map((segment) => [segment.id, segment]));
  const aliases = new Map();
  const visible = [];
  for (const segment of segments) {
    if (!segment.final) continue;
    let target = segment;
    const visited = new Set([segment.id]);
    while (target.possible_echo_of) {
      const next = known.get(target.possible_echo_of);
      if (!next?.final || visited.has(next.id)) break;
      visited.add(next.id);
      target = next;
    }
    if (target.id !== segment.id) aliases.set(segment.id, target.id);
    else visible.push(segment);
  }
  return { visible, aliases };
}
