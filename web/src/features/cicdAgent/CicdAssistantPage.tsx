import { FormEvent, useState } from "react";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import {
  sendCicdAssistant,
  type CicdAssistantMessage,
  type CicdAssistantResponse,
} from "./cicdAgentApi";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  data?: CicdAssistantResponse;
  error?: boolean;
}

const EXAMPLE_PROMPTS = [
  "帮我查询 hpcg 最近发布的镜像",
  "帮我查询 hpcg maca 最近的测试结果",
  "我想发布一个 APP，请帮我生成 app_info.json 和 app_keyword.json 的内容模板",
];

function createConversationId(): string {
  return `cicd-assistant-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function visibleHistory(messages: ChatMessage[]): CicdAssistantMessage[] {
  return messages
    .slice(-12)
    .filter((item) => item.role === "user" || item.role === "assistant")
    .map((item) => ({ role: item.role, content: item.content }));
}

export function CicdAssistantPage() {
  const { user } = useAuth();
  const [input, setInput] = useState("帮我查询 hpcg 最近发布的镜像");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState(createConversationId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function send(event?: FormEvent<HTMLFormElement>, preset?: string) {
    event?.preventDefault();
    const message = (preset ?? input).trim();
    if (!message || loading) return;

    setInput("");
    setError("");
    setLoading(true);
    setMessages((current) => [...current, { role: "user", content: message }]);

    try {
      const data = await sendCicdAssistant({
        message,
        conversation_id: conversationId,
        history: visibleHistory(messages),
      });
      setMessages((current) => [
        ...current,
        {
          role: "assistant",
          content: data.answer || "没有返回可展示的回答。",
          data,
          error: Boolean(data.error),
        },
      ]);
    } catch (err) {
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
      setMessages((current) => [
        ...current,
        { role: "assistant", content: `CICD助手调用失败：${messageText}`, error: true },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function clearChat() {
    setMessages([]);
    setError("");
    setConversationId(createConversationId());
  }

  return (
    <section className="view active cicd-agent-chat-view">
      <div className="page-toolbar">
        <h2>CICD助手</h2>
        <span className="muted small">
          {user ? `${user.display_name || user.username} · ${user.role}` : "未登录"}
        </span>
        <div className="spacer" />
        <button className="btn ghost sm" type="button" onClick={clearChat} disabled={!messages.length && !error}>
          新会话
        </button>
      </div>

      {error && <div className="error-banner">调用失败：{error}</div>}

      <section className="panel cicd-agent-chat-panel">
        <div className="cicd-agent-chat-toolbar">
          {EXAMPLE_PROMPTS.map((prompt) => (
            <button
              className="btn ghost sm"
              type="button"
              key={prompt}
              onClick={() => void send(undefined, prompt)}
              disabled={loading}
            >
              {prompt}
            </button>
          ))}
        </div>

        <div className="cicd-agent-chat-log">
          {!messages.length && (
            <div className="cicd-agent-chat-empty">
              <strong>CICD助手</strong>
              <span>可查询 APP 镜像与测试结果，也可生成发布配置内容建议。</span>
            </div>
          )}

          {messages.map((message, index) => (
            <article
              className={`cicd-agent-chat-message ${message.role}${message.error ? " bad" : ""}`}
              key={`${message.role}-${index}`}
            >
              <div className="cicd-agent-chat-role">{message.role === "user" ? "你" : "CICD助手"}</div>
              {message.role === "assistant" ? (
                <Markdown value={message.content} className="md-view cicd-agent-chat-md" />
              ) : (
                <p>{message.content}</p>
              )}
              {message.data?.tools?.length ? (
                <small>使用工具：{message.data.tools.join(", ")}</small>
              ) : null}
              {message.data?.tool_error ? (
                <small className="danger-text">查询工具不可用：{message.data.tool_error}</small>
              ) : null}
              {message.data?.error ? (
                <small className="danger-text">助手调用异常：{message.data.error}</small>
              ) : null}
            </article>
          ))}
        </div>

        <form className="cicd-agent-chat-form" onSubmit={(event) => void send(event)}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="查询 APP 最近镜像、测试结果，或让我输出 app_info.json / app_keyword.json 内容建议"
            rows={3}
          />
          <button className="btn primary" type="submit" disabled={loading || !input.trim()}>
            {loading ? "思考中" : "发送"}
          </button>
        </form>
      </section>
    </section>
  );
}
