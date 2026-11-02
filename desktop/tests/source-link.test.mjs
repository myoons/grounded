import test from "node:test";
import assert from "node:assert/strict";
import { sourceLink } from "../source-link.mjs";

test("source links accept ordinary public websites but not local protocols or IP literals", () => {
  assert.equal(sourceLink("https://www.sqlite.org/wal.html"), "https://www.sqlite.org/wal.html");
  for (const url of ["file:///etc/passwd", "javascript:alert(1)", "http://localhost/a", "http://127.1/a", "http://2130706433/a", "http://[::1]/", "https://name:secret@example.com/", "https://example.com:8877/"])
    assert.throws(() => sourceLink(url));
});
