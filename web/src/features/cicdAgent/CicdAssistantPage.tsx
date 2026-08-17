import { FormEvent, useMemo, useState } from "react";
import { Markdown } from "../../components/Markdown";
import {
  sendFailureChat,
  todayString,
  type FailureChatResponse,
  type FailureRecordFilters,
  type FailureRecordListItem,
} from "./cicdAgentApi";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  data?: FailureChatResponse;
  error?: boolean;
}

function compactFilters(filters: FailureRecordFilters): FailureRecordFilters {
  return Object.fromEntries(
    Object.entries(filters).filter(([, value]) => value !== undefined && value !== null && value !== ""),
  );
}

function fmt(value: string | null | undefined): string {
  return value || "N/A";
}

function formatRecord(record: FailureRecordListItem): string {
  const stage = record.normalized_stage || record.failed_stage || "N/A";
  return `#${record.id} ${stage} · ${fmt(record.official_name)} · ${record.job_name} #${record.build_number}`;
}

export function CicdAssistantPage() {
  const [input, setInput] = useState("今天测试失败主要是什么原因？");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [dateFrom, setDateFrom] = useState(todayString());
  const [dateTo, setDateTo] = useState(todayString());

  const filters = useMemo<FailureRecordFilters>(
    () => compactFilters({ date_from: dateFrom, date_to: dateTo }),
    [dateFrom, dateTo],
  );

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const message = input.trim();
    if (!message || loading) return;

    setInput("");
    setError("");
    setLoading(true);
    setMessages((current) => [...current, { role: "user", content: message }]);

    try {
      const data = await sendFailureChat(message, filters);
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: data.answer || "没有返回可展示的分析结果。",
          data,
          error: Boolean(data.error),
        },
      ]);
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `查询失败：${messageText}`, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function clearChat() {
    setMessages([]);
    setError("");
  }

  return (
    <section className="view active cicd-agent-chat-view">
      <div className="page-toolbar">
        <h2>CICD助手</h2>
        <div className="spacer" />
        <button className="btn ghost sm" type="button" onClick={clearChat} disabled={!messages.length && !error}>
          清空
        </button>
      </div>

      {error && <div className="error-banner">查询失败：{error}</div>}

      <section className="panel cicd-agent-chat-panel">
        <div className="cicd-agent-chat-toolbar">
          <label>
            开始时间
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            结束时间
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
        </div>

        <div className="cicd-agent-chat-log">
          {!messages.length && (
            <div className="cicd-agent-chat-empty">
              <strong>暂无聊天记录</strong>
            </div>
          )}

          {messages.map((message, index) => (
            <article
              className={`cicd-agent-chat-message ${message.role}${message.error ? " bad" : ""}`}
              key={`${message.role}-${index}`}
            >
              <div className="cicd-agent-chat-role">{message.role === "user" ? "你" : "CICD助手"}</div>
              {message.role === "assistant" ? (
                <Markdown value={message.content} />
              ) : (
                <p>{message.content}</p>
              )}
              {message.data?.error && (
                <small className="danger-text">
                  LLM 调用失败，当前展示的是数据库确定性摘要：{message.data.error}
                </small>
              )}
              {message.data?.records?.length ? (
                <div className="cicd-agent-chat-records">
                  {message.data.records.slice(0, 8).map((record) => (
                    <span key={record.id}>{formatRecord(record)}</span>
                  ))}
                </div>
              ) : null}
            </article>
          ))}
        </div>

        <form className="cicd-agent-chat-form" onSubmit={send}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="输入你想查询的失败记录问题"
            rows={3}
          />
          <button className="btn primary" type="submit" disabled={loading || !input.trim()}>
            {loading ? "查询中" : "发送"}
          </button>
        </form>
      </section>
    </section>
  );
}
