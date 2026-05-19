import type { AgentRequest, WorkflowAgentLaunchContext } from "./types.js";

export function promptWithWorkflowContext(prompt: string, context: WorkflowAgentLaunchContext | undefined): string {
  if (context === undefined) {
    return prompt;
  }
  return [
    "Humanize2 workflow agent instructions:",
    "- You are running as a Humanize2-managed agent.",
    "- Read the workflow context block after the task before acting.",
    "- vertexId is the workflow node identity for artifact ownership and routing;",
    "- shortName is the human-facing agent/session alias and should not replace vertexId in workflow state.",
    "Deliver expected artifacts back to Humanize2 through the listed MCP tools or JSON-RPC endpoint.",
    "Do not inspect, signal, attach to, or mutate the Humanize2 hub process or its in-memory runtime state.",
    "Do not repair workflow state directly; use Humanize2 artifact, board, event, message, or view APIs.",
    "",
    "Task:",
    prompt,
    "",
    "Humanize2 workflow context:",
    `- workflowRunId: ${context.workflowRunId}`,
    `- vertexId: ${context.vertexId}`,
    `- shortName: ${context.shortName}`,
    `- jsonRpcUrl: ${context.jsonRpcUrl}`,
    `- expectedArtifacts: ${stableJson(context.expectedArtifacts)}`,
    `- inputs: ${stableJson(context.inputs ?? [])}`,
    `- mcpToolNames: ${context.mcpToolNames.join(", ")}`,
    "",
    ...inputSnapshotSection(context)
  ].join("\n");
}

export function environmentWithWorkflowContext(
  env: AgentRequest["env"],
  context: WorkflowAgentLaunchContext | undefined
): AgentRequest["env"] {
  if (context === undefined) {
    return env;
  }
  return {
    ...env,
    HUMANIZE2_WORKFLOW_RUN_ID: context.workflowRunId,
    HUMANIZE2_WORKFLOW_VERTEX_ID: context.vertexId,
    HUMANIZE2_WORKFLOW_SHORT_NAME: context.shortName,
    HUMANIZE2_WORKFLOW_JSONRPC_URL: context.jsonRpcUrl,
    HUMANIZE2_WORKFLOW_EXPECTED_ARTIFACTS: stableJson(context.expectedArtifacts),
    HUMANIZE2_WORKFLOW_INPUTS: stableJson(context.inputs ?? []),
    HUMANIZE2_WORKFLOW_MCP_TOOLS: context.mcpToolNames.join(",")
  };
}

function inputSnapshotSection(context: WorkflowAgentLaunchContext): string[] {
  if (context.inputs === undefined || context.inputs.length === 0) {
    return [];
  }
  return [
    "Declared workflow input snapshots:",
    stableJson(context.inputs, 2),
    "Treat these input snapshots as part of the current task contract.",
    ""
  ];
}

function stableJson(value: unknown, space?: number): string {
  return JSON.stringify(stableJsonValue(value), null, space);
}

function stableJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stableJsonValue);
  }
  if (value === null || typeof value !== "object") {
    return value;
  }

  const object = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.keys(object).sort().map((key) => [key, stableJsonValue(object[key])])
  );
}
