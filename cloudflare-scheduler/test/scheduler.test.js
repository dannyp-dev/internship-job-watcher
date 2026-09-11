import assert from "node:assert/strict";
import test from "node:test";

import {
  dispatchGitHubWorkflow,
  easternMinutes,
  shouldDispatch,
} from "../src/index.js";

test("converts UTC to Eastern time during daylight saving time", () => {
  assert.equal(easternMinutes(new Date("2026-09-11T14:00:00Z")), 10 * 60);
});

test("converts UTC to Eastern time during standard time", () => {
  assert.equal(easternMinutes(new Date("2026-12-11T15:00:00Z")), 10 * 60);
});

test("dispatches at 10:00 AM Eastern", () => {
  assert.equal(shouldDispatch(new Date("2026-09-11T14:00:00Z")), true);
});

test("dispatches at 11:45 PM Eastern", () => {
  assert.equal(shouldDispatch(new Date("2026-09-12T03:45:00Z")), true);
});

test("does not dispatch at midnight Eastern", () => {
  assert.equal(shouldDispatch(new Date("2026-09-12T04:00:00Z")), false);
});

test("does not dispatch overnight", () => {
  assert.equal(shouldDispatch(new Date("2026-09-11T06:30:00Z")), false);
});

test("dispatches the configured GitHub workflow", async (t) => {
  t.mock.method(globalThis, "fetch", async (url, init) => {
    assert.equal(
      url,
      "https://api.github.com/repos/dannyp-dev/internship-job-watcher/actions/workflows/check-jobs.yml/dispatches",
    );
    assert.equal(init.method, "POST");
    assert.equal(init.headers.Authorization, "Bearer test-token");
    assert.deepEqual(JSON.parse(init.body), { ref: "main" });
    return { ok: true, status: 204 };
  });

  await dispatchGitHubWorkflow({ GITHUB_TOKEN: "test-token" });
});

test("rejects a missing GitHub secret", async () => {
  await assert.rejects(
    dispatchGitHubWorkflow({}),
    /GITHUB_TOKEN secret is not configured/,
  );
});
