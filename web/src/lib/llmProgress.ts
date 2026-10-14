/**
 * LLM usage display shared by every feature that calls the LLM (QA AI 分析, 周报, ...).
 *
 * The backend counts every call the same way (app/integrations/llm.py LlmMeter)
 * and reports the totals as `llm` plus `elapsed_seconds` on the job.
 */

/** One job's LLM calls; tokens from the server's own count once it reports them. */
export interface LlmTotals {
  calls: number;
  finished: number;
  sent_chars: number;
  /** Tokens sent, estimated from the characters (used until the server reports usage). */
  sent_tokens_est: number;
  /** Streamed chunks received, about one token each; moves while calls run. */
  received: number;
  reasoning: number;
  prompt_tokens: number;
  completion_tokens: number;
  /** Calls whose server reported token usage. */
  usage_calls: number;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

/** "已运行 85s；LLM 调用 37 次；服务端统计：发送 … / 接收 … token；已收到 … token". */
export function formatLlmProgress(elapsedSeconds: number | undefined, llm: LlmTotals | null | undefined): string {
  const parts: string[] = [];
  if (elapsedSeconds != null) parts.push(`已运行 ${formatDuration(elapsedSeconds)}`);
  if (!llm) return parts.join("；");
  const running = llm.calls - llm.finished;
  parts.push(`LLM 调用 ${llm.calls} 次${running > 0 ? `（进行中 ${running}）` : ""}`);
  if (llm.usage_calls) {
    parts.push(
      `服务端统计：发送 ${llm.prompt_tokens.toLocaleString()} / 接收 ${llm.completion_tokens.toLocaleString()} token`,
    );
  } else if (llm.sent_chars) {
    parts.push(
      `已发送 ${llm.sent_chars.toLocaleString()} 字符（约 ${llm.sent_tokens_est.toLocaleString()} token）`,
    );
  }
  if (llm.reasoning) parts.push(`思考 ${llm.reasoning.toLocaleString()}`);
  if (llm.received) parts.push(`已收到 ${llm.received.toLocaleString()} token`);
  return parts.join("；");
}
