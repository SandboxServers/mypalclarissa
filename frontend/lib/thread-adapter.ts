import type {
  unstable_RemoteThreadListAdapter as RemoteThreadListAdapter,
  ThreadHistoryAdapter,
} from "@assistant-ui/react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";

// Types for message content parts
type ContentPart = { type: string; text?: string };

/**
 * Remote thread list adapter for managing threads via our backend API.
 */
export const threadListAdapter: RemoteThreadListAdapter = {
  /**
   * List all threads from the backend.
   */
  async list() {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads`);
      if (!response.ok) {
        console.error("[threads] Failed to list threads:", response.status);
        return { threads: [] };
      }
      const data = await response.json();
      return { threads: data.threads };
    } catch (error) {
      console.error("[threads] Error listing threads:", error);
      return { threads: [] };
    }
  },

  /**
   * Initialize a new thread - called when creating a new thread.
   */
  async initialize(_threadId: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok) {
        console.error("[threads] Failed to create thread:", response.status);
        throw new Error("Failed to create thread");
      }
      const data = await response.json();
      console.log("[threads] Created thread:", data.remoteId);
      return { remoteId: data.remoteId, externalId: undefined };
    } catch (error) {
      console.error("[threads] Error creating thread:", error);
      throw error;
    }
  },

  /**
   * Rename a thread.
   */
  async rename(remoteId: string, newTitle: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle }),
      });
      if (!response.ok) {
        console.error("[threads] Failed to rename thread:", response.status);
      }
    } catch (error) {
      console.error("[threads] Error renaming thread:", error);
    }
  },

  /**
   * Archive a thread.
   */
  async archive(remoteId: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        console.error("[threads] Failed to archive thread:", response.status);
      }
    } catch (error) {
      console.error("[threads] Error archiving thread:", error);
    }
  },

  /**
   * Unarchive a thread.
   */
  async unarchive(remoteId: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}/unarchive`, {
        method: "POST",
      });
      if (!response.ok) {
        console.error("[threads] Failed to unarchive thread:", response.status);
      }
    } catch (error) {
      console.error("[threads] Error unarchiving thread:", error);
    }
  },

  /**
   * Delete a thread permanently.
   */
  async delete(remoteId: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        console.error("[threads] Failed to delete thread:", response.status);
      }
    } catch (error) {
      console.error("[threads] Error deleting thread:", error);
    }
  },

  /**
   * Fetch thread metadata.
   */
  async fetch(threadId: string) {
    try {
      const response = await fetch(`${BACKEND_URL}/api/threads/${threadId}`);
      if (!response.ok) {
        console.error("[threads] Failed to fetch thread:", response.status);
        return { remoteId: threadId, status: "regular" as const, title: undefined };
      }
      const data = await response.json();
      return {
        remoteId: data.remoteId || threadId,
        status: data.status || "regular",
        title: data.title,
      };
    } catch (error) {
      console.error("[threads] Error fetching thread:", error);
      return { remoteId: threadId, status: "regular" as const, title: undefined };
    }
  },

  /**
   * Generate a title for the thread based on messages.
   * Returns undefined to skip automatic title generation (we handle it manually).
   */
  async generateTitle(remoteId: string, messages: readonly { role: string; content: unknown }[]) {
    // Find the first user message to use as title
    const firstUserMessage = messages.find((m) => m.role === "user");
    if (!firstUserMessage) return undefined as never;

    // Extract text content
    let titleText = "";
    if (typeof firstUserMessage.content === "string") {
      titleText = firstUserMessage.content;
    } else if (Array.isArray(firstUserMessage.content)) {
      const textPart = (firstUserMessage.content as ContentPart[]).find(
        (p) => p.type === "text"
      );
      if (textPart && textPart.text) {
        titleText = textPart.text;
      }
    }

    // Truncate to reasonable length
    const title = titleText.slice(0, 50) + (titleText.length > 50 ? "..." : "");

    // Save to backend
    await threadListAdapter.rename(remoteId, title);

    return undefined as never;
  },
};

/**
 * Create a history adapter for a specific thread.
 */
export function createHistoryAdapter(remoteId: string | undefined): ThreadHistoryAdapter {
  return {
    /**
     * Load messages for this thread.
     */
    async load() {
      if (!remoteId) {
        return { messages: [] };
      }

      try {
        const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}/messages`);
        if (!response.ok) {
          console.error("[threads] Failed to load messages:", response.status);
          return { messages: [] };
        }
        const data = await response.json();
        console.log("[threads] Loaded", data.messages.length, "messages for thread", remoteId);

        // Convert to the expected format
        interface BackendMessage {
          id: string;
          role: string;
          content: ContentPart[];
          createdAt: string;
        }

        const messages = data.messages.map((m: BackendMessage, index: number) => ({
          message: {
            id: m.id,
            role: m.role as "user" | "assistant",
            content: m.content,
            createdAt: new Date(m.createdAt),
            metadata: {},
            status: { type: "complete" as const },
          },
          parentId: index > 0 ? data.messages[index - 1].id : null,
        }));

        return {
          headId: messages.length > 0 ? messages[messages.length - 1].message.id : null,
          messages,
        };
      } catch (error) {
        console.error("[threads] Error loading messages:", error);
        return { messages: [] };
      }
    },

    /**
     * Append a message to this thread.
     */
    async append(item) {
      if (!remoteId) {
        console.warn("[threads] Cannot append message - no remoteId");
        return;
      }

      const { message } = item;

      // Extract text content
      let textContent = "";
      if (typeof message.content === "string") {
        textContent = message.content;
      } else if (Array.isArray(message.content)) {
        textContent = message.content
          .filter((p: ContentPart) => p.type === "text")
          .map((p: ContentPart) => p.text || "")
          .join("");
      }

      try {
        const response = await fetch(`${BACKEND_URL}/api/threads/${remoteId}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            role: message.role,
            content: textContent,
            id: message.id,
            createdAt: message.createdAt?.toISOString(),
          }),
        });
        if (!response.ok) {
          console.error("[threads] Failed to append message:", response.status);
        }
      } catch (error) {
        console.error("[threads] Error appending message:", error);
      }
    },
  };
}
