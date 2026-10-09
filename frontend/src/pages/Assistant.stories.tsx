import type { Meta, StoryObj } from "@storybook/react-vite";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "../shell/AppShell";
import { useSession, type Me } from "../session/Session";
import {
  adminMe,
  assistantConversation,
  assistantOff,
  assistantOn,
  assistantProposal,
  assistantUsage,
  assistantUsageExhausted,
  serverError,
  staffMe,
} from "../stories/fixtures";
import { withApp, type Handlers } from "../stories/mockApi";
import { AssistantPage, ChatThread, StepCard, itemsFromMessages } from "./Assistant";

function InShell({ children }: { children: React.ReactNode }) {
  const { state } = useSession();
  if (state.status !== "signed-in") return null;
  return (
    <AppShell me={state.me} onSignOut={() => {}}>
      <Routes>
        <Route path="*" element={children} />
      </Routes>
    </AppShell>
  );
}

const page = (element: React.ReactNode, { me = staffMe, handlers = {} }: { me?: Me; handlers?: Handlers }) => ({
  render: () => <InShell>{element}</InShell>,
  decorators: [withApp({ me, path: "/assistant", handlers })],
});

const meta: Meta = { title: "Pages/Assistant", parameters: { layout: "fullscreen" } };
export default meta;
type Story = StoryObj;

const on: Handlers = { "assistant.status": { data: assistantOn }, "assistant.usage": { data: assistantUsage } };

/** A model is connected: an empty prompt invites the first question, with the budget beneath. */
export const Ready: Story = page(<AssistantPage />, { handlers: on });
/** No model connected: the page says so. A manager gets a link to connect one. */
export const NotConnected: Story = page(<AssistantPage />, { handlers: { ...on, "assistant.status": { data: assistantOff } } });
/** A manager sees where to connect a model. */
export const NotConnectedManager: Story = page(<AssistantPage />, { me: adminMe, handlers: { ...on, "assistant.status": { data: assistantOff } } });
/** The day's token budget is spent: the composer is disabled and says why. */
export const BudgetSpent: Story = page(<AssistantPage />, { handlers: { ...on, "assistant.usage": { data: assistantUsageExhausted } } });
export const AssistantLoading: Story = page(<AssistantPage />, { handlers: { "assistant.status": "pending", "assistant.usage": "pending" } });
export const AssistantError: Story = page(<AssistantPage />, { handlers: { "assistant.status": { error: serverError }, "assistant.usage": { data: assistantUsage } } });

/** A finished exchange: the question, the action the assistant ran, and its answer. */
export const Thread: Story = page(<ChatThread items={itemsFromMessages(assistantConversation.messages)} />, { handlers: on });
/** A proposed change: nothing ran, and the person can confirm or withdraw it. */
export const Proposal: Story = page(<StepCard step={assistantProposal.steps[0]} />, { handlers: on });
