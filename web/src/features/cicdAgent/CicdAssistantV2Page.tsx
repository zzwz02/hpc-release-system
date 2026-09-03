import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type AppendMessage,
  type MessageState,
  type TextMessagePartComponent,
  type ThreadMessageLike,
} from "@assistant-ui/react";
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
  updateAssistantConversation,
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

const ROUTE_LABELS: Record<string, string> = {
  static_publish_overview: "发布概要",
  static_publish_onboarding: "发布起步",
  plain_model_publish: "发布配置",
  plain_model_general: "普通问答",
  agent_query_tools: "查询工具",
};

const AssistantMarkdownPart: TextMessagePartComponent = ({ text }) => (
  <Markdown value={text} className="md-view cicd-agent-chat-md" />
);

const UserTextPart: TextMessagePartComponent = ({ text }) => <p>{text}</p>;

const EmptyMessagePart = () => null;

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

function messageTools(message: MessageState): string[] {
  const tools = message.metadata.custom.tools;
  return Array.isArray(tools) ? tools.map((item) => String(item)).filter(Boolean) : [];
}

function customText(message: MessageState, key: string): string {
  const value = message.metadata.custom[key];
  return typeof value === "string" ? value : "";
}

function customFlag(message: MessageState, key: string): boolean {
  return Boolean(message.metadata.custom[key]);
}

function regenerateTargetMessageId(message: MessageState): string {
  const sourceMessageId = customText(message, "regenerated_from_message_id");
  return sourceMessageId || message.id || "";
}

function messageTimings(message: MessageState): Record<string, number> {
  const value = message.metadata.custom.timings;
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value)
      .map(([key, item]) => [key, Number(item)])
      .filter(([, item]) => Number.isFinite(item)),
  );
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

function parseDate(value: string | null | undefined): Date | undefined {
  if (!value) return undefined;
  const date = new Date(value.includes("T") ? value : value.replace(" ", "T"));
  return Number.isNaN(date.getTime()) ? undefined : date;
}

function displayTime(value: string | null | undefined): string {
  if (!value) return "";
  return value.slice(5, 16);
}

function displayMessageTime(value: Date): string {
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  const hour = String(value.getHours()).padStart(2, "0");
  const minute = String(value.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

function slotValueText(value: unknown): string {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value);
  return String(value ?? "");
}

function routeLabel(route: string): string {
  return ROUTE_LABELS[route] || route;
}

function formatDuration(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  if (ms >= 10000) return `${(ms / 1000).toFixed(0)}s`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.max(Math.round(ms), 0)}ms`;
}

function timingSummary(timings: Record<string, number>): string[] {
  const items: string[] = [];
  if (timings.total_ms !== undefined) items.push(`总耗时 ${formatDuration(timings.total_ms)}`);
  if (timings.queue_wait_ms !== undefined && timings.queue_wait_ms > 0) {
    items.push(`排队 ${formatDuration(timings.queue_wait_ms)}`);
  }
  if (timings.first_token_ms !== undefined) {
    items.push(`首 token ${formatDuration(timings.first_token_ms)}`);
  }
  if (timings.mcp_load_ms !== undefined) items.push(`MCP ${formatDuration(timings.mcp_load_ms)}`);
  if (timings.prompt_chars !== undefined) items.push(`Prompt ${timings.prompt_chars} 字符`);
  return items;
}

function convertMessage(message: UiMessage, index: number): ThreadMessageLike {
  const createdAt = parseDate(message.created_at);
  const fallbackId = `${message.role}-${message.sequence ?? index}`;
  const custom: Record<string, unknown> = {
    ...(message.metadata ?? {}),
    ...(message.sequence !== undefined ? { sequence: message.sequence } : {}),
    ...(message.conversation_id ? { conversation_id: message.conversation_id } : {}),
    ...(message.error ? { error: true } : {}),
    ...(message.streaming ? { streaming: true } : {}),
  };
  const agentError =
    typeof custom.agent_error === "string" ? custom.agent_error : "助手调用失败";

  return {
    id: message.id || fallbackId,
    role: message.role,
    content: message.content,
    ...(createdAt ? { createdAt } : {}),
    ...(message.role === "assistant"
      ? {
          status: message.streaming
            ? ({ type: "running" } as const)
            : message.error
              ? ({ type: "incomplete", reason: "error", error: agentError } as const)
              : ({ type: "complete", reason: "stop" } as const),
        }
      : {}),
    metadata: {
      custom,
    },
  };
}

function appendMessageText(message: AppendMessage): string {
  return message.content
    .map((part) => (part.type === "text" ? part.text : ""))
    .filter(Boolean)
    .join("\n")
    .trim();
}

function messageContentText(message: MessageState): string {
  return message.content
    .map((part) => {
      if (part.type === "text" || part.type === "reasoning") return part.text;
      return "";
    })
    .filter(Boolean)
    .join("\n")
    .trim();
}

interface AssistantUiMessageProps {
  message: MessageState;
  sending: boolean;
  lastAssistantMessageId: string;
  onCopy: (text: string) => void;
  onRegenerate: (messageId: string) => void;
}

function AssistantUiMessage({
  message,
  sending,
  lastAssistantMessageId,
  onCopy,
  onRegenerate,
}: AssistantUiMessageProps) {
  const content = messageContentText(message);
  const tools = messageTools(message);
  const toolError = customText(message, "tool_error");
  const agentError = customText(message, "agent_error");
  const route = customText(message, "route");
  const statusMessage = customText(message, "status_message");
  const timingItems = timingSummary(messageTimings(message));
  const regenerateTargetId = regenerateTargetMessageId(message);
  const canCopy = message.role === "assistant" && !!content;
  const canRegenerate =
    canCopy &&
    !sending &&
    !!regenerateTargetId &&
    message.id === lastAssistantMessageId &&
    message.status?.type !== "running";

  return (
    <MessagePrimitive.Root
      className={`cicd-agent-chat-message ${message.role}${customFlag(message, "error") ? " bad" : ""}`}
    >
      <div className="cicd-agent-chat-role">
        {message.role === "user" ? "你" : "CICD助手V2"}
        <span>{displayMessageTime(message.createdAt)}</span>
      </div>
      {content ? (
        <MessagePrimitive.Parts
          components={{
            Text: message.role === "assistant" ? AssistantMarkdownPart : UserTextPart,
            Empty: EmptyMessagePart,
          }}
        />
      ) : message.role === "assistant" ? (
        <p className="muted">{statusMessage || "思考中..."}</p>
      ) : null}
      {message.role === "assistant" && message.status?.type === "running" && statusMessage && content ? (
        <small className="cicd-agent-chat-status">{statusMessage}</small>
      ) : null}
      {canCopy ? (
        <div className="cicd-agent-chat-message-actions">
          <button className="btn ghost sm" type="button" onClick={() => onCopy(content)}>
            复制
          </button>
          {canRegenerate ? (
            <button className="btn ghost sm" type="button" onClick={() => onRegenerate(regenerateTargetId)}>
              {customFlag(message, "error") ? "重试" : "重新生成"}
            </button>
          ) : null}
        </div>
      ) : null}
      {tools.length ? (
        <small>{message.status?.type === "running" ? "正在使用工具" : "使用工具"}：{tools.join(", ")}</small>
      ) : null}
      {message.role === "assistant" && (route || timingItems.length) ? (
        <small className="cicd-agent-chat-observation">
          {route ? `路线：${routeLabel(route)}` : ""}
          {route && timingItems.length ? " · " : ""}
          {timingItems.join(" · ")}
        </small>
      ) : null}
      {toolError ? <small className="danger-text">查询工具不可用：{toolError}</small> : null}
      {agentError ? <small className="danger-text">助手调用异常：{agentError}</small> : null}
    </MessagePrimitive.Root>
  );
}

export function CicdAssistantV2Page() {
  const { user } = useAuth();
  const abortControllerRef = useRef<AbortController | null>(null);
  const [conversations, setConversations] = useState<AssistantConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [conversationState, setConversationState] = useState<AssistantConversationState>(EMPTY_STATE);
  const [loadingConversations, setLoadingConversations] = useState(false);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const [slotsCollapsed, setSlotsCollapsed] = useState(true);
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

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoadingConversations(true);
      setError("");
      fetchAssistantConversations("")
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
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [user?.username]);

  useEffect(() => () => abortControllerRef.current?.abort(), []);

  useEffect(() => {
    setEditingTitle(false);
    setTitleDraft(activeConversation?.title || "");
  }, [activeConversation?.id, activeConversation?.title]);

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

  const createConversation = useCallback(async (title?: string): Promise<AssistantConversation> => {
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
  }, []);

  const handleStreamEvent = useCallback((
    streamEvent: AssistantStreamEvent,
    userTempId: string | null,
    assistantTempId: string,
  ) => {
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
          const metadata = { ...(item.metadata ?? {}) };
          if (streamEvent.provider) metadata.provider = streamEvent.provider;
          if (streamEvent.model) metadata.model = streamEvent.model;
          if (streamEvent.available_tools) metadata.available_tools = streamEvent.available_tools;
          if (streamEvent.tools) metadata.tools = streamEvent.tools;
          if (streamEvent.route) metadata.route = streamEvent.route;
          if (streamEvent.timings) metadata.timings = streamEvent.timings;
          if (streamEvent.publish_skill_included !== undefined) {
            metadata.publish_skill_included = streamEvent.publish_skill_included;
          }
          if (streamEvent.query_tools_enabled !== undefined) {
            metadata.query_tools_enabled = streamEvent.query_tools_enabled;
          }
          if (streamEvent.tool_error !== undefined) metadata.tool_error = streamEvent.tool_error;
          return {
            ...item,
            metadata,
          };
        }),
      );
      return;
    }

    if (streamEvent.type === "status") {
      setMessages((current) =>
        current.map((item) => {
          if (item.id !== assistantTempId) return item;
          return {
            ...item,
            metadata: {
              ...(item.metadata ?? {}),
              status_stage: streamEvent.stage,
              status_message: streamEvent.message,
              ...(streamEvent.route ? { route: streamEvent.route } : {}),
              ...(streamEvent.timings ? { timings: streamEvent.timings } : {}),
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
  }, []);

  const sendMessageText = useCallback(async (message: string) => {
    const text = message.trim();
    if (!text || sending || creating) return;

    setError("");
    setSending(true);

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const tempId = `pending-user-${Date.now()}`;
    const assistantTempId = `pending-assistant-${Date.now()}`;
    setMessages((current) => [
      ...current,
      { id: tempId, role: "user", content: text },
      { id: assistantTempId, role: "assistant", content: "", streaming: true },
    ]);

    try {
      let targetConversationId = activeConversationId;
      if (!targetConversationId) {
        setCreating(true);
        try {
          const created = await createAssistantConversation(compactTitle(text));
          targetConversationId = created.conversation.id;
          setConversations((current) => upsertConversation(current, created.conversation));
        } finally {
          setCreating(false);
        }
      }
      if (!targetConversationId) throw new Error("无法创建 CICD助手会话");

      await sendAssistantConversationMessageStream(targetConversationId, text, (streamEvent) => {
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
        { role: "user", content: text },
        { role: "assistant", content: `CICD助手调用失败：${messageText}`, error: true },
      ]);
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null;
      }
      setSending(false);
    }
  }, [activeConversationId, creating, handleStreamEvent, sending]);

  const regenerateAssistantMessageById = useCallback(async (targetMessageId: string) => {
    if (!activeConversationId || !targetMessageId || sending || creating) return;
    setError("");
    setSending(true);

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
          regenerated_from_message_id: targetMessageId,
        },
      },
    ]);

    try {
      await regenerateAssistantConversationMessageStream(activeConversationId, targetMessageId, (streamEvent) => {
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
                metadata: {
                  ...(item.metadata ?? {}),
                  agent_error: messageText,
                },
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
  }, [activeConversationId, creating, handleStreamEvent, sending]);

  const runtime = useExternalStoreRuntime<UiMessage>({
    messages,
    isLoading: loadingMessages,
    isRunning: sending,
    isSendDisabled: sending || creating || loadingMessages,
    convertMessage,
    onNew: async (message) => {
      await sendMessageText(appendMessageText(message));
    },
    onCancel: async () => {
      abortControllerRef.current?.abort();
    },
    unstable_capabilities: {
      copy: true,
    },
  });

  async function startNewConversation() {
    if (creating || sending) return;
    setError("");
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

  function startRenameConversation() {
    if (!activeConversation || sending) return;
    setError("");
    setTitleDraft(activeConversation.title || "");
    setEditingTitle(true);
  }

  function cancelRenameConversation() {
    setEditingTitle(false);
    setTitleDraft(activeConversation?.title || "");
  }

  async function saveConversationTitle(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!activeConversationId || renaming || sending) return;
    const title = titleDraft.trim().replace(/\s+/g, " ");
    if (!title) {
      setError("标题不能为空");
      return;
    }
    setRenaming(true);
    setError("");
    try {
      const data = await updateAssistantConversation(activeConversationId, title);
      setConversations((current) => upsertConversation(current, data.conversation));
      setEditingTitle(false);
      toast.success("会话标题已更新");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRenaming(false);
    }
  }

  async function copyAssistantText(text: string) {
    if (!text.trim()) return;
    try {
      await writeClipboard(text);
      toast.success("已复制回答");
    } catch (err) {
      toast.error(`复制失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return (
    <section className="view active cicd-agent-chat-view cicd-agent-v2-view">
      {error && <div className="error-banner">调用失败：{error}</div>}

      <section className="cicd-agent-chat-shell cicd-agent-v2-shell">
        <aside className="panel cicd-agent-chat-sidebar cicd-agent-v2-sidebar">
          <div className="cicd-agent-v2-sidebar-top">
            <div className="cicd-agent-v2-sidebar-brand">
              <span className="cicd-agent-v2-logo">CI</span>
              <div>
                <strong>CICD助手</strong>
              </div>
            </div>
            <div className="cicd-agent-v2-session-toolbar">
              <div className="cicd-agent-chat-sidebar-title">
                <strong>会话</strong>
                <span>{loadingConversations ? "同步中" : `${conversations.length} 条`}</span>
              </div>
              <div className="cicd-agent-chat-sidebar-actions">
                <button
                  className="cicd-agent-v2-icon-action"
                  type="button"
                  title="新建会话"
                  aria-label="新建会话"
                  onClick={() => void startNewConversation()}
                  disabled={creating || sending}
                >
                  +
                </button>
                <button
                  className="cicd-agent-v2-icon-action danger"
                  type="button"
                  title="删除当前会话"
                  aria-label="删除当前会话"
                  onClick={() => void deleteCurrentConversation()}
                  disabled={!activeConversationId || deleting || sending}
                >
                  -
                </button>
              </div>
            </div>
          </div>
          <div className="cicd-agent-chat-session-list">
            {!conversations.length && !loadingConversations ? (
              <div className="cicd-agent-chat-session-empty">
                暂无历史会话
              </div>
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
          <div className={`cicd-agent-chat-state cicd-agent-v2-state ${slotsCollapsed ? "is-collapsed" : ""}`}>
            <div className="cicd-agent-v2-state-head">
              <strong>已确认信息</strong>
              <button
                className="cicd-agent-v2-pill-toggle"
                type="button"
                aria-expanded={!slotsCollapsed}
                onClick={() => setSlotsCollapsed((current) => !current)}
              >
                {slotsCollapsed ? "展开" : "收起"}
              </button>
            </div>
            {!slotsCollapsed ? (
              slotEntries.length ? (
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
              )
            ) : null}
          </div>
        </aside>

        <AssistantRuntimeProvider runtime={runtime}>
          <section className="panel cicd-agent-chat-panel cicd-agent-v2-panel">
            <div className="cicd-agent-chat-toolbar">
              <div className="cicd-agent-chat-title">
                {editingTitle ? (
                  <form className="cicd-agent-chat-title-edit" onSubmit={(event) => void saveConversationTitle(event)}>
                    <input
                      className="input"
                      value={titleDraft}
                      onChange={(event) => setTitleDraft(event.target.value)}
                      maxLength={80}
                      autoFocus
                    />
                    <button className="btn primary sm" type="submit" disabled={renaming || !titleDraft.trim()}>
                      保存
                    </button>
                    <button className="btn ghost sm" type="button" onClick={cancelRenameConversation} disabled={renaming}>
                      取消
                    </button>
                  </form>
                ) : (
                  <>
                    <strong>{activeConversation?.title || "新会话"}</strong>
                    <span>
                      {activeConversation
                        ? `${activeConversation.message_count} 条消息 · ${displayTime(activeConversation.updated_at)}`
                        : "发送消息后自动保存"}
                    </span>
                  </>
                )}
              </div>
              {activeConversation && !editingTitle ? (
                <button
                  className="btn ghost sm"
                  type="button"
                  onClick={startRenameConversation}
                  disabled={sending || renaming}
                >
                  重命名
                </button>
              ) : null}
            </div>

            <ThreadPrimitive.Root className="cicd-agent-v2-thread">
              <ThreadPrimitive.Viewport
                className={`cicd-agent-v2-viewport ${messages.length ? "" : "is-empty"}`}
                autoScroll
                scrollToBottomOnInitialize
                scrollToBottomOnRunStart
                scrollToBottomOnThreadSwitch
              >
                {loadingMessages ? (
                  <div className="cicd-agent-chat-empty">
                    <strong>加载会话中</strong>
                  </div>
                ) : (
                  <>
                    <ThreadPrimitive.Empty>
                      <div className="cicd-agent-chat-empty cicd-agent-v2-empty">
                        <strong>今天想推进哪件 CICD 事情？</strong>
                        <span>可查询 APP 镜像与测试结果，也可生成发布配置内容建议。</span>
                      </div>
                    </ThreadPrimitive.Empty>
                    <ThreadPrimitive.Messages>
                      {({ message }) => (
                        <AssistantUiMessage
                          message={message}
                          sending={sending}
                          lastAssistantMessageId={lastAssistantMessageId}
                          onCopy={(text) => void copyAssistantText(text)}
                          onRegenerate={(messageId) => void regenerateAssistantMessageById(messageId)}
                        />
                      )}
                    </ThreadPrimitive.Messages>
                  </>
                )}
                <ThreadPrimitive.ViewportFooter className="cicd-agent-v2-footer">
                  <ThreadPrimitive.ScrollToBottom className="btn ghost sm cicd-agent-v2-scroll-bottom">
                    到底部
                  </ThreadPrimitive.ScrollToBottom>
                  <ComposerPrimitive.Root className="cicd-agent-v2-composer">
                    <ComposerPrimitive.Input
                      className="cicd-agent-v2-input"
                      placeholder="查询 APP 最近镜像、测试结果，或让我输出 app_info.json / app_keyword.json 内容建议"
                      minRows={4}
                      maxRows={10}
                    />
                    <div className="cicd-agent-v2-composer-actions">
                      {sending ? (
                        <ComposerPrimitive.Cancel className="btn ghost cicd-agent-v2-stop" type="button">
                          停止
                        </ComposerPrimitive.Cancel>
                      ) : null}
                      <ComposerPrimitive.Send
                        className="cicd-agent-v2-send"
                        type="submit"
                        aria-label={sending ? "发送中" : "发送"}
                      >
                        ↑
                      </ComposerPrimitive.Send>
                    </div>
                  </ComposerPrimitive.Root>
                  {!messages.length && !loadingMessages ? (
                    <div className="cicd-agent-v2-suggestions">
                      {EXAMPLE_HINTS.map((hint) => (
                        <span key={hint}>{hint}</span>
                      ))}
                    </div>
                  ) : null}
                </ThreadPrimitive.ViewportFooter>
              </ThreadPrimitive.Viewport>
            </ThreadPrimitive.Root>
          </section>
        </AssistantRuntimeProvider>
      </section>
    </section>
  );
}
