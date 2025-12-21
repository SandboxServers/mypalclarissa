import { streamText, extractReasoningMiddleware, wrapLanguageModel } from "ai";
import { z } from "zod";
import { createOpenAI } from "@ai-sdk/openai";
import { createOpenAICompatible } from "@ai-sdk/openai-compatible";
import { createAnthropic } from "@ai-sdk/anthropic";

// Message type from assistant-ui/ai SDK
interface ChatMessage {
  role: string;
  content?: unknown;
  parts?: Array<{ type: string; text?: string }>;
  id?: string;
}

// LLM Provider configuration
const LLM_PROVIDER = (process.env.LLM_PROVIDER || "openrouter")
  .toLowerCase()
  .trim();

// Create OpenRouter client (OpenAI-compatible)
const openrouter = createOpenAI({
  baseURL: "https://openrouter.ai/api/v1",
  apiKey: process.env.OPENROUTER_API_KEY!,
  headers: {
    "HTTP-Referer": process.env.OPENROUTER_SITE || "http://localhost:3000",
    "X-Title": process.env.OPENROUTER_TITLE || "MyPalClara",
  },
});

// Create NanoGPT client using OpenAI-compatible provider
const nanogpt = createOpenAICompatible({
  name: "nanogpt",
  baseURL: "https://nano-gpt.com/api/v1",
  headers: {
    Authorization: `Bearer ${process.env.NANOGPT_API_KEY}`,
  },
});

// Create custom OpenAI-compatible client (uses /chat/completions endpoint)
const customOpenAI = createOpenAICompatible({
  name: "custom-openai",
  baseURL: process.env.CUSTOM_OPENAI_BASE_URL || "https://api.openai.com/v1",
  headers: {
    Authorization: `Bearer ${process.env.CUSTOM_OPENAI_API_KEY}`,
  },
});

// Create Anthropic client for direct Claude API access
const anthropic = process.env.ANTHROPIC_API_KEY
  ? createAnthropic({ apiKey: process.env.ANTHROPIC_API_KEY })
  : null;

// Check if a model name indicates it supports thinking/reasoning
function supportsThinking(modelName: string): boolean {
  const thinkingModels = [
    "claude-opus-4",
    "claude-sonnet-4",
    "claude-3-7-sonnet",
    "kimi-k2-thinking",
    "deepseek-r1",
    "o1",
    "o3",
  ];
  return thinkingModels.some((m) =>
    modelName.toLowerCase().includes(m.toLowerCase()),
  );
}

// Get the appropriate model based on provider
function getModel() {
  let model;
  let modelName: string;

  if (LLM_PROVIDER === "nanogpt") {
    modelName = process.env.NANOGPT_MODEL || "moonshotai/kimi-k2-thinking";
    console.log("[chat] Using NanoGPT with model:", modelName);
    model = nanogpt.chatModel(modelName);
  } else if (LLM_PROVIDER === "openai") {
    modelName = process.env.CUSTOM_OPENAI_MODEL || "gpt-4o";
    console.log("[chat] Using custom OpenAI with model:", modelName);
    model = customOpenAI.chatModel(modelName);
  } else if (LLM_PROVIDER === "anthropic" && anthropic) {
    modelName = process.env.ANTHROPIC_MODEL || "claude-sonnet-4-20250514";
    console.log("[chat] Using Anthropic with model:", modelName);
    model = anthropic(modelName);
  } else {
    modelName = process.env.OPENROUTER_MODEL || "anthropic/claude-sonnet-4";
    console.log("[chat] Using OpenRouter with model:", modelName);
    model = openrouter(modelName);
  }

  // Wrap model with reasoning middleware if it supports thinking
  // This extracts <think>...</think> or <thinking>...</thinking> blocks
  if (supportsThinking(modelName)) {
    console.log(
      "[chat] Model supports thinking, wrapping with reasoning middleware",
    );
    return wrapLanguageModel({
      model,
      middleware: extractReasoningMiddleware({
        tagName: "thinking", // Claude uses <thinking> tags
      }),
    });
  }

  return model;
}

const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8000";

// Web search function for tool calling
async function executeWebSearch(query: string) {
  console.log("[chat] Web search for:", query);
  try {
    const response = await fetch(`${BACKEND_URL}/api/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, max_results: 5 }),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error("[chat] Search API error:", error);
      return { error: "Search failed. Please try again." };
    }

    const data = await response.json();

    // Format results for the LLM
    const formattedResults = data.results
      .map(
        (r: { title: string; url: string; content: string }, i: number) =>
          `[${i + 1}] ${r.title}\n${r.url}\n${r.content}`,
      )
      .join("\n\n");

    return {
      answer: data.answer,
      results: formattedResults,
      resultCount: data.results.length,
    };
  } catch (error) {
    console.error("[chat] Web search error:", error);
    return { error: "Search unavailable. Backend may be offline." };
  }
}

// Extract text content from message (handles both content and parts formats)
const getTextContent = (message: ChatMessage): string => {
  // Check parts first (assistant-ui format)
  if (message.parts && Array.isArray(message.parts)) {
    const result = message.parts
      .filter((p) => p.type === "text")
      .map((p) => p.text || "")
      .join("");
    return result;
  }
  // Fall back to content (standard OpenAI format)
  if (typeof message.content === "string") return message.content;
  if (Array.isArray(message.content)) {
    return message.content
      .filter((p: { type: string }) => p.type === "text")
      .map((p: { type: string; text?: string }) => p.text || "")
      .join("");
  }
  return "";
};

export async function POST(req: Request) {
  const body = await req.json();
  const {
    messages,
    threadId: bodyThreadId,
  }: { messages: ChatMessage[]; threadId?: string } = body;

  // Get thread ID from header or body
  const threadId = req.headers.get("X-Thread-Id") || bodyThreadId;

  if (!threadId) {
    console.error("[chat] No thread ID provided");
    return new Response("Thread ID required", { status: 400 });
  }

  console.log("[chat] Thread:", threadId, "Messages:", messages?.length);

  // Get the last user message
  const lastUserMessage = messages.findLast((m) => m.role === "user");
  if (!lastUserMessage) {
    console.log("[chat] No user message found");
    return new Response("No user message found", { status: 400 });
  }

  const userMessageText = getTextContent(lastUserMessage);
  console.log("[chat] User message:", userMessageText.slice(0, 100));

  let contextMessages: { role: string; content: string }[] = [];
  let backendAvailable = false;

  // Try to get enriched context from our backend
  try {
    const requestBody = {
      message: userMessageText,
      thread_id: threadId,
      project: "Default Project",
    };

    const contextResponse = await fetch(`${BACKEND_URL}/api/context`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(requestBody),
    });

    if (contextResponse.ok) {
      const contextData = await contextResponse.json();
      contextMessages = contextData.messages;
      backendAvailable = true;
      console.log(
        "[chat] Got context from backend:",
        contextMessages.length,
        "messages",
      );
    } else {
      const errorText = await contextResponse.text();
      console.error(
        "[chat] Backend returned error:",
        contextResponse.status,
        errorText,
      );
    }
  } catch (error) {
    console.error("[chat] Backend not available:", error);
  }

  // Fallback: use messages directly if backend failed
  if (contextMessages.length === 0) {
    console.log("[chat] Using fallback - direct messages");
    contextMessages = messages
      .map((m) => ({
        role: m.role,
        content: getTextContent(m),
      }))
      .filter((m) => m.content.length > 0);
  }

  // Final check
  if (contextMessages.length === 0) {
    return new Response("No valid messages", { status: 400 });
  }

  // Stream the response using selected provider
  const result = streamText({
    model: getModel(),
    messages: contextMessages as any,
    tools: {
      webSearch: {
        name: "webSearch",
        description:
          "Search the web for current information. Use this when the user asks about recent events, news, weather, or information that may have changed since your knowledge cutoff. Also use when the user explicitly asks you to search or look something up.",
        inputSchema: z.object({
          query: z.string().describe("The search query to look up"),
        }),
        execute: async ({ query }) => executeWebSearch(query),
      },
    },

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
              thread_id: threadId,
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
