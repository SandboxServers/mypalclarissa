import { streamText } from "ai";
import { createOpenAI } from "@ai-sdk/openai";

// Message type from assistant-ui/ai SDK
interface ChatMessage {
  role: string;
  content: unknown;
  id?: string;
}

// Create OpenRouter client (OpenAI-compatible)
const openrouter = createOpenAI({
  baseURL: "https://openrouter.ai/api/v1",
  apiKey: process.env.OPENROUTER_API_KEY!,
  headers: {
    "HTTP-Referer": process.env.OPENROUTER_SITE || "http://localhost:3000",
    "X-Title": process.env.OPENROUTER_TITLE || "Clara Assistant",
  },
});

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

// Extract text content from message (can be string or array of parts)
const getTextContent = (content: unknown): string => {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((p: { type: string }) => p.type === "text")
      .map((p: { type: string; text?: string }) => p.text || "")
      .join("");
  }
  return "";
};

export async function POST(req: Request) {
  console.log("[chat] POST /api/chat called");
  console.log("[chat] BACKEND_URL:", BACKEND_URL);

  const body = await req.json();
  const { messages }: { messages: ChatMessage[] } = body;
  console.log("[chat] Received messages count:", messages?.length);

  // Get the last user message
  const lastUserMessage = messages.findLast((m) => m.role === "user");
  if (!lastUserMessage) {
    console.log("[chat] No user message found");
    return new Response("No user message found", { status: 400 });
  }

  const userMessageText = getTextContent(lastUserMessage.content);
  console.log("[chat] User message:", userMessageText.slice(0, 100));

  let contextMessages: { role: string; content: string }[] = [];
  let backendAvailable = false;

  // Try to get enriched context from our backend
  try {
    const requestBody = {
      message: userMessageText,
      project: "Default Project",
    };
    console.log("[chat] Sending to backend:", JSON.stringify(requestBody));

    const contextResponse = await fetch(`${BACKEND_URL}/api/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });

    if (contextResponse.ok) {
      const contextData = await contextResponse.json();
      contextMessages = contextData.messages;
      backendAvailable = true;
      console.log("[chat] Got context from backend:", contextMessages.length, "messages");
    } else {
      const errorText = await contextResponse.text();
      console.error("[chat] Backend returned error:", contextResponse.status, errorText);
    }
  } catch (error) {
    console.error("[chat] Backend not available:", error);
  }

  // Fallback: use messages directly if backend failed
  if (contextMessages.length === 0) {
    console.log("[chat] Using fallback - direct messages");
    contextMessages = messages.map((m) => ({
      role: m.role,
      content: getTextContent(m.content),
    })).filter((m) => m.content.length > 0);
  }

  // Final check
  if (contextMessages.length === 0) {
    return new Response("No valid messages", { status: 400 });
  }

  // Stream the response using OpenRouter
  const result = streamText({
    model: openrouter(process.env.OPENROUTER_MODEL || "anthropic/claude-sonnet-4"),
    messages: contextMessages as any,
    onFinish: async ({ text }) => {
      // Store the conversation in our backend (only if backend is available)
      if (backendAvailable) {
        try {
          await fetch(`${BACKEND_URL}/api/store`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              user_message: userMessageText,
              assistant_message: text,
              project: "Default Project",
            }),
          });
          console.log("[chat] Stored messages in backend");
        } catch (error) {
          console.error("[chat] Backend store error:", error);
        }
      }
    },
  });

  return result.toUIMessageStreamResponse();
}
