import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import type { Output } from "../api/actions";
import { ApiError, invoke } from "../api/client";
import { useQuery } from "../api/useAction";
import { CardSkeleton, Chip, EmptyState, ErrorPanel, Loading, Notice } from "../components";
import { PayloadFacts } from "./admin/Approvals";
import { Page } from "../shell/AppShell";
import { useMe } from "../session/Session";

type Status = Output<"assistant.status">;
type Usage = Output<"assistant.usage">;
type Ask = Output<"assistant.ask">;
type Step = Ask["steps"][number];
type Stopped = Ask["stopped"];

const OUTCOME: Record<string, { tone: "up" | "info" | "warn" | "down" | "flat"; label: string }> = {
  ok: { tone: "up", label: "Ran" },
  proposed: { tone: "info", label: "Proposed" },
  refused: { tone: "down", label: "Refused" },
  error: { tone: "down", label: "Error" },
};

const STOPPED: Record<Stopped, string> = {
  answered: "",
  max_steps: "The assistant stopped after its step limit. Ask it to continue if it has more to do.",
  budget: "The token budget ran out mid-answer. It will resume once the budget resets or is raised.",
  model_error: "The model could not be reached. Your infrastructure team can check the connection under Integrations.",
  deadline: "The assistant ran out of time on this question. Try a narrower ask.",
};

/** A user's turn or the assistant's reply. Steps are the actions it ran, shown inline. */
export type ChatItem =
  | { kind: "user" | "assistant"; id: string; text: string }
  | { kind: "step"; id: string; step: Step };

/** One action the assistant ran: what it was, how it ended, and (for a proposal) confirm/withdraw. */
export function StepCard({ step }: { step: Step }) {
  const o = OUTCOME[step.outcome] ?? { tone: "flat" as const, label: step.outcome };
  const [resolved, setResolved] = useState<"approved" | "rejected" | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "info" | "neg"; text: string } | null>(null);
  const canAct = step.outcome === "proposed" && step.approval_request_id && !resolved;

  const act = async (kind: "confirm" | "withdraw") => {
    if (!step.approval_request_id) return;
    setBusy(true);
    setNote(null);
    try {
      const out = await invoke(kind === "confirm" ? "platform.approval.confirm" : "platform.approval.withdraw", {
        approval_request_id: step.approval_request_id,
        reason: kind === "withdraw" ? "Withdrawn from the assistant." : "",
      });
      setResolved(out.status === "approved" ? "approved" : "rejected");
    } catch (e) {
      setNote({ tone: "neg", text: e instanceof ApiError ? e.message : "The proposal could not be updated." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kg-card kg-asst-step" aria-label={`Action ${step.action_name}`}>
      <div className="kg-sechead">
        <code className="kg-mono">{step.action_name}</code>
        <span className="kg-spacer" />
        <Chip tone={resolved ? (resolved === "approved" ? "up" : "flat") : o.tone}>
          {resolved === "approved" ? "Approved" : resolved === "rejected" ? "Withdrawn" : o.label}
        </Chip>
      </div>
      {Object.keys(step.params).length ? <PayloadFacts payload={step.params} /> : null}
      {step.outcome === "error" || step.outcome === "refused" ? (
        <p className="kg-cap">{step.error || "The assistant could not run this."}</p>
      ) : null}
      {step.outcome === "proposed" ? (
        <p className="kg-cap">Nothing has changed. This is a proposal waiting for approval.</p>
      ) : null}
      {note ? (
        <Notice tone={note.tone} role="alert">
          {note.text} <Link to="/admin/approvals">Open Approvals</Link>
        </Notice>
      ) : null}
      {canAct ? (
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="kg-btn kg-btn--primary" disabled={busy} onClick={() => act("confirm")}>
            Confirm
          </button>
          <button type="button" className="kg-btn" disabled={busy} onClick={() => act("withdraw")}>
            Withdraw
          </button>
          <Link className="kg-btn kg-btn--link" to="/admin/approvals">
            View in Approvals
          </Link>
        </div>
      ) : null}
    </div>
  );
}

export function ChatThread({ items }: { items: ChatItem[] }) {
  return (
    <div className="kg-stack kg-asst-thread" aria-label="Conversation" aria-live="polite">
      {items.map((item) =>
        item.kind === "step" ? (
          <StepCard key={item.id} step={item.step} />
        ) : (
          <div key={item.id} className={`kg-asst-msg kg-asst-msg--${item.kind}`}>
            <span className="kg-cap">{item.kind === "user" ? "You" : "Assistant"}</span>
            <p>{item.text}</p>
          </div>
        ),
      )}
    </div>
  );
}

function stepItems(prefix: string, steps: Step[]): ChatItem[] {
  return steps.map((step, i) => ({ kind: "step", id: `${prefix}-step-${i}`, step }));
}

/** Turn a loaded conversation into chat items: user/assistant bubbles and the actions between them. */
export function itemsFromMessages(messages: Output<"assistant.conversation.get">["messages"]): ChatItem[] {
  return messages.map((m) =>
    m.role === "tool"
      ? {
          kind: "step",
          id: `m-${m.seq}`,
          step: {
            action_name: m.action_name ?? "",
            params: m.params ?? {},
            outcome: m.outcome ?? "ok",
            result: m.result ?? null,
            error: "",
            approval_request_id: m.approval_request_id ?? null,
          },
        }
      : { kind: m.role, id: `m-${m.seq}`, text: m.content },
  );
}

function Composer({ disabled, busy, onSend }: { disabled: boolean; busy: boolean; onSend: (text: string) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = text.trim();
    if (trimmed && !disabled && !busy) {
      onSend(trimmed);
      setText("");
    }
  };
  return (
    <form className="kg-asst-composer" onSubmit={submit} aria-label="Ask the assistant">
      <div className="kg-field">
        <label htmlFor={id}>Ask the assistant</label>
        <textarea
          id={id}
          className="kg-textarea"
          rows={2}
          value={text}
          disabled={disabled}
          placeholder="Ask about your scorecards, targets, agents…"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit(e);
            }
          }}
        />
      </div>
      <button type="submit" className="kg-btn kg-btn--primary" disabled={disabled || busy || text.trim().length === 0}>
        {busy ? "Thinking…" : "Send"}
      </button>
    </form>
  );
}

/** The assistant is off: say why, and point a manager at where to connect a model. */
export function NotConnected({ status, canManage }: { status: Status; canManage: boolean }) {
  return (
    <EmptyState kind="none" title="No model is connected">
      {status.reason || "The assistant is off until your infrastructure team connects a model."}
      {canManage ? (
        <>
          {" "}
          Connect one under <Link to="/admin/integrations">Integrations</Link>.
        </>
      ) : (
        " Ask an Admin to switch it on."
      )}
    </EmptyState>
  );
}

function budgetLine(usage: Usage): string | null {
  if (usage.monthly_limit == null && usage.user_daily_limit == null) return null;
  const parts: string[] = [];
  if (usage.user_daily_limit != null) parts.push(`${usage.today_tokens.toLocaleString()} of ${usage.user_daily_limit.toLocaleString()} tokens today`);
  if (usage.monthly_limit != null) parts.push(`${usage.month_tokens.toLocaleString()} of ${usage.monthly_limit.toLocaleString()} this month`);
  return parts.join(" · ");
}

/**
 * The Assistant (R7, PRD AG-3/AG-8): ask in words, and it reads through your own actions
 * and proposes any change for approval. It runs as you, with exactly your permissions. With
 * no model connected the page says so and the rest of kpiGo is unaffected.
 */
export function AssistantPage() {
  const me = useMe();
  const canManage = me.permissions.includes("assistant.manage");
  const [status] = useQuery("assistant.status", {});
  const [usage, reloadUsage] = useQuery("assistant.usage", {});
  const [items, setItems] = useState<ChatItem[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopped, setStopped] = useState<Stopped | null>(null);
  const [error, setError] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [items, busy]);

  const send = async (text: string) => {
    setBusy(true);
    setError(null);
    setStopped(null);
    const turn = `t${items.length}`;
    setItems((prev) => [...prev, { kind: "user", id: `${turn}-user`, text }]);
    try {
      const out = await invoke("assistant.ask", { message: text, conversation_id: conversationId });
      setConversationId(out.conversation_id);
      setItems((prev) => [
        ...prev,
        ...stepItems(turn, out.steps),
        { kind: "assistant", id: `${turn}-reply`, text: out.reply },
      ]);
      if (out.stopped !== "answered") setStopped(out.stopped);
      reloadUsage();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The assistant could not answer.");
    } finally {
      setBusy(false);
    }
  };

  const newChat = () => {
    setConversationId(null);
    setItems([]);
    setStopped(null);
    setError(null);
  };

  if (status.status === "loading") {
    return (
      <Page title="Assistant">
        <Loading label="Checking the assistant">
          <CardSkeleton />
        </Loading>
      </Page>
    );
  }
  if (status.status === "error") {
    return (
      <Page title="Assistant">
        <ErrorPanel error={status.error} what="the assistant" />
      </Page>
    );
  }

  const available = status.data.available;
  const exhausted = usage.status === "ready" ? usage.data.exhausted : "";
  const budget = usage.status === "ready" ? budgetLine(usage.data) : null;

  return (
    <Page
      title="Assistant"
      actions={
        available && items.length ? (
          <button type="button" className="kg-btn" onClick={newChat}>
            New chat
          </button>
        ) : null
      }
    >
      {!available ? (
        <NotConnected status={status.data} canManage={canManage} />
      ) : (
        <div className="kg-asst">
          {items.length === 0 ? (
            <EmptyState kind="none" title="Ask the assistant anything about your data">
              It reads through the same actions you can, in your own scope, and never changes anything without
              putting a proposal up for approval first.
            </EmptyState>
          ) : (
            <ChatThread items={items} />
          )}
          {stopped && STOPPED[stopped] ? <Notice tone="warn" role="status">{STOPPED[stopped]}</Notice> : null}
          {error ? (
            <Notice tone="neg" role="alert">
              {error}
            </Notice>
          ) : null}
          {exhausted ? (
            <Notice tone="warn" role="status">
              {exhausted}
            </Notice>
          ) : null}
          <div ref={endRef} />
          <Composer disabled={!!exhausted} busy={busy} onSend={send} />
          {budget ? <p className="kg-cap">{budget}</p> : null}
        </div>
      )}
    </Page>
  );
}
