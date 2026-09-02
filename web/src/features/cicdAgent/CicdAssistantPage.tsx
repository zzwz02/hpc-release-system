import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import { toast } from "../../lib/toast";
import {
  createAssistantConversation,
  deleteAssistantConversation,
  fetchAssistantConversation,
  fetchAssistantConversations,
  regenerateAssistantConversationMessageStream,
  sendAssistantConversationMessageStream,
  type AssistantConversation,
  type AssistantConversationState,
  type AssistantStreamEvent,
  type CicdAssistantMessage,
} from "./cicdAgentApi";

type UiMessage = CicdAssistantMessage & {
  error?: boolean;
  streaming?: boolean;
};

const EXAMPLE_HINTS = [
  "帮我查询 <xx> app 最近发布的镜像",
  "帮我查询 <xx> app 在 maca 分支最近的测试结果",
  "我想发布一个 APP，请帮我生成 app_info.json 和 app_keyword.json 的内容模板",
];

const EMPTY_STATE: AssistantConversationState = {
  conversation_id: "",
  rolling_summary: "",
  slots: {},
  summarized_until_sequence: 0,
  updated_at: "",
};

const SLOT_LABELS: Record<string, string> = {
  intent: "意图",
  app_name: "APP",
  app_version: "版本",
  dockerfile_path: "Dockerfile",
  os: "OS",
  arch: "架构",
  sdk: "SDK",
  sdkversion: "SDK版本",
  supported_chip: "芯片",
  image_aliases: "镜像别名",
  test_cases: "测试用例",
  last_query_summary: "最近查询",
};

function compactTitle(value: string): string {
  const title = value.trim().replace(/\s+/g, " ");
  return title.length > 36 ? `${title.slice(0, 36)}...` : title;
}

function sortConversations(items: AssistantConversation[]): AssistantConversation[] {
  return [...items].sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}

function upsertConversation(
  items: AssistantConversation[],
  conversation: AssistantConversation,
): AssistantConversation[] {
  return sortConversations([
    conversation,
    ...items.filter((item) => item.id !== conversation.id),
  ]);
}

function messageTools(message: CicdAssistantMessage): string[] {
  const tools = message.metadata?.tools;
  return Array.isArray(tools) ? tools.map((item) => String(item)).filter(Boolean) : [];
}

function messageTextMeta(message: CicdAssistantMessage, key: string): string {
  const value = message.metadata?.[key];
  return typeof value === "string" ? value : "";
}

function appendTool(metadata: Record<string, unknown> | undefined, toolName: string): Record<string, unknown> {
  const tools = Array.isArray(metadata?.tools)
    ? metadata.tools.map((item) => String(item)).filter(Boolean)
    : [];
  return {
    ...(metadata ?? {}),
    tools: tools.includes(toolName) ? tools : [...tools, toolName],
  };
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    "name" in error &&
    error.name === "AbortError"
  );
}

function stoppedAssistantContent(content: string): string {
  const text = content.trim();
  if (text) return `${text}\n\n（已停止生成）`;
  return "已停止生成，未产生可展示内容。";
}

async function writeClipboard(text: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("浏览器不允许写入剪贴板");
}

function displayTime(value: string | null | undefined): string {
  if (!value) return "";
  return value.slice(5, 16);
}

function slotValueText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

export function CicdAssistantPage() {
  const { user } = useAuth();
  const chatLogRef = useRef<HTMLDivElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const shouldStickToBottomRef = useRef(true);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [input, setInput] = useState("");
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [conversationState, setConversationState] = useState<AssistantConversationState>(EMPTY_STATE);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState("");

  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === activeConversationId) ?? null,
    [activeConversationId, conversations],
  );
  const slotEntries = useMemo(
    () =>
      Object.entries(conversationState.slots)
        .filter(([, value]) => value !== undefined && value !== null && value !== "")
        .filter(([, value]) => !Array.isArray(value) || value.length > 0),
    [conversationState.slots],
  );
  const lastAssistantMessageId = useMemo(
    () =>
      [...messages]
        .reverse()
        .find((message) => message.role === "assistant" && !message.streaming)?.id ?? "",
    [messages],
  );

  function scrollChatToBottom(behavior: ScrollBehavior = "auto") {
    window.requestAnimationFrame(() => {
      chatEndRef.current?.scrollIntoView({ block: "end", behavior });
    });
  }

  function updateChatStickiness() {
    const log = chatLogRef.current;
    if (!log) return;
    const distanceToBottom = log.scrollHeight - log.scrollTop - log.clientHeight;
    shouldStickToBottomRef.current = distanceToBottom < 80;
  }

  useEffect(() => {
    let cancelled = false;
    setLoadingConversations(true);
    setError("");
    fetchAssistantConversations()
      .then((data) => {
        if (cancelled) return;
        const sorted = sortConversations(data.conversations);
        setConversations(sorted);
        setActiveConversationId((current) => {
          if (current && sorted.some((item) => item.id === current)) return current;
          return sorted[0]?.id ?? null;
        });
        if (!sorted.length) {
          setMessages([]);
          setConversationState(EMPTY_STATE);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingConversations(false);
      });
    return () => {
      cancelled = true;
    };
  }, [user?.username]);

  useEffect(() => {
    shouldStickToBottomRef.current = true;
    if (!loadingMessages) scrollChatToBottom();
  }, [activeConversationId, loadingMessages]);

  useEffect(() => {
    if (shouldStickToBottomRef.current) scrollChatToBottom();
  }, [messages]);

  useEffect(() => () => abortControllerRef.current?.abort(), []);

  useEffect(() => {
    if (!activeConversationId) {
      setMessages([]);
      setConversationState(EMPTY_STATE);
      return;
    }
    let cancelled = false;
    setLoadingMessages(true);
    setError("");
    fetchAssistantConversation(activeConversationId)
      .then((data) => {
        if (cancelled) return;
        setConversations((current) => upsertConversation(current, data.conversation));
        setMessages(data.messages);
        setConversationState(data.state);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoadingMessages(false);
      });
    return () => {
      cancelled = true;
    };
  }, [activeConversationId]);

  async function createConversation(title?: string): Promise<AssistantConversation> {
    setCreating(true);
    try {
      const data = await createAssistantConversation(title);
      setConversations((current) => upsertConversation(current, data.conversation));
      setActiveConversationId(data.conversation.id);
      setMessages([]);
      setConversationState({ ...EMPTY_STATE, conversation_id: data.conversation.id });
      return data.conversation;
    } finally {
      setCreating(false);
    }
  }

  async function send(event?: FormEvent<HTMLFormElement>, preset?: string) {
    event?.preventDefault();
    const message = (preset ?? input).trim();
    if (!message || sending || creating) return;

    setInput("");
    setError("");
    setSending(true);
    shouldStickToBottomRef.current = true;

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const tempId = `pending-user-${Date.now()}`;
    const assistantTempId = `pending-assistant-${Date.now()}`;
    setMessages((current) => [
      ...current,
      { id: tempId, role: "user", content: message },
      { id: assistantTempId, role: "assistant", content: "", streaming: true },
    ]);

    try {
      let targetConversationId = activeConversationId;
      if (!targetConversationId) {
        setCreating(true);
        try {
          const created = await createAssistantConversation(compactTitle(message));
          targetConversationId = created.conversation.id;
          setConversations((current) => upsertConversation(current, created.conversation));
        } finally {
          setCreating(false);
        }
      }
      if (!targetConversationId) throw new Error("无法创建 CICD助手会话");

      await sendAssistantConversationMessageStream(targetConversationId, message, (streamEvent) => {
        handleStreamEvent(streamEvent, tempId, assistantTempId);
      }, { signal: controller.signal });
    } catch (err) {
      if (isAbortError(err)) {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== assistantTempId) return item;
            return {
              ...item,
              content: stoppedAssistantContent(item.content),
              streaming: false,
              metadata: {
                ...(item.metadata ?? {}),
                agent_error: "用户停止了生成",
                agent_status_code: 499,
              },
            };
          }),
        );
        return;
      }
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
      setMessages((current) => [
        ...current.filter((item) => item.id !== tempId && item.id !== assistantTempId),
        { role: "user", content: message },
        { role: "assistant", content: `CICD助手调用失败：${messageText}`, error: true },
      ]);
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      setSending(false);
    }
  }

  function stopGenerating() {
    abortControllerRef.current?.abort();
  }

  async function copyAssistantMessage(message: UiMessage) {
    if (!message.content.trim()) return;
    try {
      await writeClipboard(message.content);
      toast.success("已复制回答");
    } catch (err) {
      toast.error(`复制失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  async function regenerateAssistantMessage(message: UiMessage) {
    if (!activeConversationId || !message.id || sending || creating) return;
    setError("");
    setSending(true);
    shouldStickToBottomRef.current = true;

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const assistantTempId = `pending-regenerate-${Date.now()}`;
    setMessages((current) => [
      ...current,
      {
        id: assistantTempId,
        role: "assistant",
        content: "",
        streaming: true,
        metadata: {
          regenerated_from_message_id: message.id,
        },
      },
    ]);

    try {
      await regenerateAssistantConversationMessageStream(activeConversationId, message.id, (streamEvent) => {
        handleStreamEvent(streamEvent, null, assistantTempId);
      }, { signal: controller.signal });
    } catch (err) {
      if (isAbortError(err)) {
        setMessages((current) =>
          current.map((item) => {
            if (item.id !== assistantTempId) return item;
            return {
              ...item,
              content: stoppedAssistantContent(item.content),
              streaming: false,
              metadata: {
                ...(item.metadata ?? {}),
                agent_error: "用户停止了生成",
                agent_status_code: 499,
              },
            };
          }),
        );
        return;
      }
      const messageText = err instanceof Error ? err.message : String(err);
      setError(messageText);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantTempId
            ? {
                ...item,
                content: `CICD助手重新生成失败：${messageText}`,
                error: true,
                streaming: false,
              }
            : item,
        ),
      );
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      setSending(false);
    }
  }

  function handleStreamEvent(
    streamEvent: AssistantStreamEvent,
    userTempId: string | null,
    assistantTempId: string,
  ) {
    if (streamEvent.type === "start") {
      setConversations((current) => upsertConversation(current, streamEvent.conversation));
      if (userTempId && streamEvent.user_message) {
        setMessages((current) =>
          current.map((item) => (item.id === userTempId ? streamEvent.user_message! : item)),
        );
      }
      return;
    }

    if (streamEvent.type === "metadata") {
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== assistantTempId) return item;
          return {
            ...item,
            metadata: {
              ...(item.metadata ?? {}),
              provider: streamEvent.provider,
              model: streamEvent.model,
              available_tools: streamEvent.available_tools,
              tool_error: streamEvent.tool_error,
            },
          };
        }),
      );
      return;
    }

    if (streamEvent.type === "tool") {
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== assistantTempId) return item;
          return {
            ...item,
            metadata: appendTool(item.metadata, streamEvent.name),
          };
        }),
      );
      return;
    }

    if (streamEvent.type === "token") {
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== assistantTempId) return item;
          return { ...item, content: `${item.content}${streamEvent.content}` };
        }),
      );
      return;
    }

    if (streamEvent.type === "done") {
      setConversations((current) => upsertConversation(current, streamEvent.conversation));
      setActiveConversationId(streamEvent.conversation.id);
      setConversationState(streamEvent.state);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantTempId ? streamEvent.assistant_message : item,
        ),
      );
      return;
    }

    if (streamEvent.type === "error") {
      const messageText = streamEvent.error || streamEvent.assistant?.agent_error || "CICD助手调用失败";
      const conversation = streamEvent.conversation;
      setError(messageText);
      if (conversation) {
        setConversations((current) => upsertConversation(current, conversation));
        setActiveConversationId(conversation.id);
      }
      if (streamEvent.state) setConversationState(streamEvent.state);
      setMessages((current) =>
        current.map((item) =>
          item.id === assistantTempId
            ? {
                ...(streamEvent.assistant_message ?? item),
                error: true,
                streaming: false,
              }
            : item,
        ),
      );
    }
  }

  async function startNewConversation() {
    if (creating || sending) return;
    setError("");
    setInput("");
    try {
      await createConversation();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function deleteCurrentConversation() {
    if (!activeConversationId || deleting || sending) return;
    const deletedId = activeConversationId;
    setDeleting(true);
    setError("");
    try {
      await deleteAssistantConversation(deletedId);
      const next = conversations.filter((item) => item.id !== deletedId);
      setConversations(next);
      setActiveConversationId(next[0]?.id ?? null);
      if (!next.length) {
        setMessages([]);
        setConversationState(EMPTY_STATE);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }

  return (
    <section className="view active cicd-agent-chat-view">
      <div className="page-toolbar">
        <h2>CICD助手</h2>
        <span className="muted small">
          {user ? `${user.display_name || user.username} · ${user.role}` : "未登录"}
        </span>
        <div className="spacer" />
      </div>

      {error && <div className="error-banner">调用失败：{error}</div>}

      <section className="cicd-agent-chat-shell">
        <aside className="panel cicd-agent-chat-sidebar">
          <div className="cicd-agent-chat-sidebar-head">
            <div className="cicd-agent-chat-sidebar-title">
              <strong>会话</strong>
              <span>{loadingConversations ? "加载中" : `${conversations.length} 条`}</span>
            </div>
            <div className="cicd-agent-chat-sidebar-actions">
              <button className="btn ghost sm" type="button" onClick={() => void startNewConversation()} disabled={creating || sending}>
                新会话
              </button>
              <button
                className="btn ghost sm"
                type="button"
                onClick={() => void deleteCurrentConversation()}
                disabled={!activeConversationId || deleting || sending}
              >
                删除会话
              </button>
            </div>
          </div>
          <div className="cicd-agent-chat-session-list">
            {!conversations.length && !loadingConversations ? (
              <div className="cicd-agent-chat-session-empty">暂无历史会话</div>
            ) : null}
            {conversations.map((conversation) => (
              <button
                type="button"
                key={conversation.id}
                className={`cicd-agent-chat-session ${
                  conversation.id === activeConversationId ? "active" : ""
                }`}
                onClick={() => setActiveConversationId(conversation.id)}
                disabled={sending}
              >
                <span>{conversation.title || "新会话"}</span>
                <small>
                  {conversation.message_count} 条 · {displayTime(conversation.updated_at)}
                </small>
              </button>
            ))}
          </div>
          <div className="cicd-agent-chat-state">
            <strong>已确认信息</strong>
            {slotEntries.length ? (
              <dl>
                {slotEntries.map(([key, value]) => (
                  <div key={key}>
                    <dt>{SLOT_LABELS[key] || key}</dt>
                    <dd>{slotValueText(value)}</dd>
                  </div>
                ))}
              </dl>
            ) : (
              <span>暂无</span>
            )}
          </div>
        </aside>

        <section className="panel cicd-agent-chat-panel">
          <div className="cicd-agent-chat-toolbar">
            <div className="cicd-agent-chat-title">
              <strong>{activeConversation?.title || "新会话"}</strong>
              <span>{activeConversation ? activeConversation.id : "发送消息后自动保存"}</span>
            </div>
          </div>

          <div className="cicd-agent-chat-log" ref={chatLogRef} onScroll={updateChatStickiness}>
            {!messages.length && !loadingMessages ? (
              <div className="cicd-agent-chat-empty">
                <strong>CICD助手</strong>
                <span>可查询 APP 镜像与测试结果，也可生成发布配置内容建议。</span>
                <div className="cicd-agent-chat-examples">
                  {EXAMPLE_HINTS.map((hint) => (
                    <span key={hint}>{hint}</span>
                  ))}
                </div>
              </div>
            ) : null}
            {loadingMessages ? (
              <div className="cicd-agent-chat-empty">
                <strong>加载会话中</strong>
              </div>
            ) : null}

            {messages.map((message, index) => {
              const tools = messageTools(message);
              const toolError = messageTextMeta(message, "tool_error");
              const agentError = messageTextMeta(message, "agent_error");
              const canCopy = message.role === "assistant" && !!message.content.trim();
              const canRegenerate =
                canCopy &&
                !message.streaming &&
                !sending &&
                message.id === lastAssistantMessageId;
              return (
                <article
                  className={`cicd-agent-chat-message ${message.role}${message.error ? " bad" : ""}`}
                  key={message.id || `${message.role}-${index}`}
                >
                  <div className="cicd-agent-chat-role">
                    {message.role === "user" ? "你" : "CICD助手"}
                    {message.created_at ? <span>{displayTime(message.created_at)}</span> : null}
                  </div>
                  {message.role === "assistant" && message.content ? (
                    <Markdown value={message.content} className="md-view cicd-agent-chat-md" />
                  ) : message.role === "assistant" ? (
                    <p className="muted">{message.streaming ? "思考中..." : ""}</p>
                  ) : (
                    <p>{message.content}</p>
                  )}
                  {canCopy ? (
                    <div className="cicd-agent-chat-message-actions">
                      <button className="btn ghost sm" type="button" onClick={() => void copyAssistantMessage(message)}>
                        复制
                      </button>
                      {canRegenerate ? (
                        <button className="btn ghost sm" type="button" onClick={() => void regenerateAssistantMessage(message)}>
                          重新生成
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                  {tools.length ? (
                    <small>{message.streaming ? "正在使用工具" : "使用工具"}：{tools.join(", ")}</small>
                  ) : null}
                  {toolError ? <small className="danger-text">查询工具不可用：{toolError}</small> : null}
                  {agentError ? <small className="danger-text">助手调用异常：{agentError}</small> : null}
                </article>
              );
            })}
            <div className="cicd-agent-chat-end" ref={chatEndRef} />
          </div>

          <form className="cicd-agent-chat-form" onSubmit={(event) => void send(event)}>
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="查询 APP 最近镜像、测试结果，或让我输出 app_info.json / app_keyword.json 内容建议"
              rows={3}
            />
            <div className="cicd-agent-chat-form-actions">
              <button className="btn primary" type="submit" disabled={sending || creating || !input.trim()}>
                {sending ? "发送中" : "发送"}
              </button>
              {sending ? (
                <button className="btn ghost" type="button" onClick={stopGenerating}>
                  停止
                </button>
              ) : null}
            </div>
          </form>
        </section>
      </section>
    </section>
  );
}
