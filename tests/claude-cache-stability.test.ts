import { describe, expect, it } from "vitest";

import { promptWithWorkflowContext } from "../src/agents/workflow-context.js";
import type { WorkflowAgentLaunchContext } from "../src/agents/types.js";

describe("Claude workflow prompt cache stability", () => {
  it("keeps more than 90 percent of prompt bytes reusable across 100 dynamic workflow turns", () => {
    const claudeCodeVersion = "2.1.143";
    const rounds = 100;
    const taskPrompt = [
      "Implement the requested workflow task using the declared artifacts.",
      stableTaskBody()
    ].join("\n\n");
    const prompts = Array.from({ length: rounds }, (_, index) =>
      withSameClaudeCodeVersionEnvelope(
        claudeCodeVersion,
        promptWithWorkflowContext(taskPrompt, contextForTurn(index))
      )
    );

    const cache = estimateCacheStability(prompts);

    expect(cache.claudeCodeVersion).toBe(claudeCodeVersion);
    expect(cache.rounds).toBe(rounds);
    expect(cache.averagePromptBytes).toBeGreaterThan(10_000);
    expect(cache.averageReusablePrefixBytes).toBeGreaterThan(10_000);
    expect(cache.cacheHitRatio).toBeGreaterThan(0.9);
  });
});

interface CacheEstimate {
  claudeCodeVersion: string;
  rounds: number;
  averagePromptBytes: number;
  averageReusablePrefixBytes: number;
  cacheHitRatio: number;
}

function withSameClaudeCodeVersionEnvelope(claudeCodeVersion: string, prompt: string): string {
  return [
    `Claude Code version: ${claudeCodeVersion}`,
    "Model: gpt-5.5",
    "Permission mode: bypassPermissions",
    "Output format: stream-json",
    "",
    prompt
  ].join("\n");
}

function estimateCacheStability(prompts: string[]): CacheEstimate {
  const reusablePrefixBytes = prompts.slice(1).map((prompt, index) =>
    commonPrefixLength(prompts[index], prompt)
  );
  const promptBytes = prompts.map((prompt) => prompt.length);
  const averagePromptBytes = average(promptBytes);
  const averageReusablePrefixBytes = average(reusablePrefixBytes);
  const version = /^Claude Code version: (.+)$/m.exec(prompts[0])?.[1] ?? "unknown";

  return {
    claudeCodeVersion: version,
    rounds: prompts.length,
    averagePromptBytes,
    averageReusablePrefixBytes,
    cacheHitRatio: averageReusablePrefixBytes / averagePromptBytes
  };
}

function contextForTurn(index: number): WorkflowAgentLaunchContext {
  return {
    workflowRunId: `workflow-run-${index.toString().padStart(3, "0")}`,
    vertexId: `reviewer-${index % 7}`,
    shortName: `reviewer-${index % 5}`,
    jsonRpcUrl: `http://127.0.0.1:${4772 + index}/jsonrpc`,
    expectedArtifacts: [{
      schema: "rlcr.verdict.v1",
      name: "verdict"
    }],
    inputs: [{
      kind: "artifact",
      name: "draft",
      schema: "draft.v1",
      label: "Current draft",
      optional: false,
      producer: `builder-${index}`,
      iteration: index + 1,
      createdAt: `2026-05-16T10:${String(index % 60).padStart(2, "0")}:00.000Z`,
      content: {
        b: 2,
        a: 1,
        turn: index
      }
    }, {
      kind: "board",
      id: "loop-status",
      label: "Loop status",
      optional: true,
      updatedAt: `2026-05-16T11:${String(index % 60).padStart(2, "0")}:00.000Z`,
      value: {
        status: index % 2 === 0 ? "revise" : "review",
        requiredFollowUp: [`Fix-${index}`]
      }
    }],
    mcpToolNames: [
      "artifact_deliver",
      "workflow_get",
      "board_patch",
      "event_emit"
    ]
  };
}

function stableTaskBody(): string {
  return Array.from({ length: 120 }, (_, index) =>
    `STABLE_TASK_LINE_${String(index + 1).padStart(3, "0")}: This deterministic task body represents reusable workflow instructions and stays unchanged across turns.`
  ).join("\n");
}

function average(values: number[]): number {
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function commonPrefixLength(left: string, right: string): number {
  const limit = Math.min(left.length, right.length);
  for (let index = 0; index < limit; index += 1) {
    if (left[index] !== right[index]) {
      return index;
    }
  }
  return limit;
}
