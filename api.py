"""FastAPI backend for MyPalClara assistant."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

from db import SessionLocal
from logging_config import get_logger
from models import Project, Session, Message
from memory_manager import MemoryManager, load_initial_profile
from llm_backends import make_llm

load_dotenv()

logger = get_logger("api")

USER_ID = os.getenv("USER_ID", "demo-user")
DEFAULT_PROJECT = os.getenv("DEFAULT_PROJECT", "Default Project")

app = FastAPI(title="MyPalClara API")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Log validation errors before returning 422."""
    body = await request.body()
    logger.warning(
        f"Validation error on {request.url}",
        extra={"path": str(request.url), "body": str(body)[:200], "errors": str(exc.errors())[:500]}
    )
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

# CORS for frontend - allow all origins in Docker for LAN access
cors_origins_env = os.getenv("CORS_ORIGINS", "")
if cors_origins_env:
    cors_origins = [o.strip() for o in cors_origins_env.split(",")]
    allow_creds = True
else:
    cors_origins = ["*"]
    allow_creds = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=allow_creds,
    allow_methods=["*"],
    allow_headers=["*"],
)

mm: MemoryManager = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ContextRequest(BaseModel):
    message: str
    thread_id: str = None
    project: str = DEFAULT_PROJECT


class StoreRequest(BaseModel):
    user_message: str
    assistant_message: str
    thread_id: str = None
    project: str = DEFAULT_PROJECT


class ThreadRenameRequest(BaseModel):
    title: str


class MessageAppendRequest(BaseModel):
    role: str
    content: str
    id: str = None
    createdAt: str = None


class ChatRequest(BaseModel):
    message: str
    thread_id: str = None
    project: str = DEFAULT_PROJECT


@app.on_event("startup")
def startup():
    """Initialize database and memory manager on startup."""
    global mm
    logger.info("Starting up...")
    llm = make_llm()
    logger.info("LLM created")
    mm = MemoryManager(llm_callable=llm)
    logger.info("MemoryManager initialized")
    load_initial_profile(USER_ID)
    logger.info("Initial profile loaded")
    logger.info("Ready to accept requests on http://localhost:8000")


def ensure_project(name: str) -> str:
    """Ensure project exists and return its ID."""
    db = SessionLocal()
    try:
        proj = db.query(Project).filter_by(owner_id=USER_ID, name=name).first()
        if not proj:
            proj = Project(owner_id=USER_ID, name=name)
            db.add(proj)
            db.commit()
            db.refresh(proj)
        return proj.id
    finally:
        db.close()


@app.post("/api/context")
def get_context(request: ContextRequest):
    """Get enriched context for a message (system prompt + memories)."""
    logger.info(f"GET /api/context: {request.message[:50]}...", extra={"user_id": USER_ID})
    project_id = ensure_project(request.project or DEFAULT_PROJECT)

    db = SessionLocal()
    try:
        if not request.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        thread = mm.get_thread(db, request.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        recent_msgs = mm.get_recent_messages(db, thread.id)

        user_mems, proj_mems = mm.fetch_mem0_context(
            USER_ID, project_id, request.message
        )

        prompt_messages = mm.build_prompt(
            user_mems,
            proj_mems,
            thread.session_summary,
            recent_msgs,
            request.message,
        )

        return {
            "messages": prompt_messages,
            "session_id": thread.id,
        }
    finally:
        db.close()


@app.post("/api/store")
def store_messages(request: StoreRequest):
    """Store user and assistant messages after completion."""
    project_id = ensure_project(request.project or DEFAULT_PROJECT)

    db = SessionLocal()
    try:
        if not request.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        thread = mm.get_thread(db, request.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        recent_msgs = mm.get_recent_messages(db, thread.id)

        mm.store_message(db, thread.id, USER_ID, "user", request.user_message)
        mm.store_message(db, thread.id, USER_ID, "assistant", request.assistant_message)
        thread.last_activity_at = datetime.now(timezone.utc).replace(tzinfo=None)

        if not thread.title and request.user_message:
            title = request.user_message[:50]
            if len(request.user_message) > 50:
                title += "..."
            thread.title = title

        db.commit()

        if mm.should_update_summary(db, thread.id):
            mm.update_thread_summary(db, thread)

        mm.add_to_mem0(
            USER_ID, project_id, recent_msgs,
            request.user_message, request.assistant_message
        )

        return {"status": "ok", "thread_id": thread.id}
    finally:
        db.close()


@app.post("/api/chat")
def chat(request: ChatRequest):
    """Chat endpoint for web UI."""
    logger.info(f"POST /api/chat: {request.message[:50]}...", extra={"user_id": USER_ID})
    project_id = ensure_project(request.project or DEFAULT_PROJECT)

    db = SessionLocal()
    try:
        if not request.thread_id:
            raise HTTPException(status_code=400, detail="thread_id is required")

        thread = mm.get_thread(db, request.thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        recent_msgs = mm.get_recent_messages(db, thread.id)

        user_mems, proj_mems = mm.fetch_mem0_context(
            USER_ID, project_id, request.message
        )

        prompt_messages = mm.build_prompt(
            user_mems,
            proj_mems,
            thread.session_summary,
            recent_msgs,
            request.message,
        )

        llm = make_llm()
        response = llm(prompt_messages)

        mm.store_message(db, thread.id, USER_ID, "user", request.message)
        mm.store_message(db, thread.id, USER_ID, "assistant", response)
        thread.last_activity_at = datetime.now(timezone.utc).replace(tzinfo=None)

        if not thread.title and request.message:
            title = request.message[:50]
            if len(request.message) > 50:
                title += "..."
            thread.title = title

        db.commit()

        if mm.should_update_summary(db, thread.id):
            mm.update_thread_summary(db, thread)

        mm.add_to_mem0(
            USER_ID, project_id, recent_msgs,
            request.message, response
        )

        logger.info(f"POST /api/chat response: {len(response)} chars", extra={"session_id": thread.id})
        return {
            "content": response,
            "thread_id": thread.id,
        }
    finally:
        db.close()


@app.get("/api/projects")
def get_projects():
    """Get list of projects for the user."""
    db = SessionLocal()
    try:
        projects = db.query(Project).filter_by(owner_id=USER_ID).all()
        return {"projects": [p.name for p in projects] or [DEFAULT_PROJECT]}
    finally:
        db.close()


# ============== Thread Management API ==============

@app.get("/api/threads")
def list_threads():
    """List all threads for the user."""
    project_id = ensure_project(DEFAULT_PROJECT)
    db = SessionLocal()
    try:
        sessions = (
            db.query(Session)
            .filter_by(user_id=USER_ID, project_id=project_id)
            .order_by(Session.last_activity_at.desc())
            .all()
        )
        threads = []
        for sess in sessions:
            if sess.archived == "pinned":
                status = "regular"
            elif sess.archived == "true":
                status = "archived"
            else:
                status = "regular"

            threads.append({
                "remoteId": sess.id,
                "status": status,
                "title": sess.title,
                "isPinned": sess.archived == "pinned",
            })

        threads.sort(key=lambda t: (not t.get("isPinned", False), 0))
        return {"threads": threads}
    finally:
        db.close()


@app.post("/api/threads")
def create_thread():
    """Create a new thread and return its ID."""
    project_id = ensure_project(DEFAULT_PROJECT)
    db = SessionLocal()
    try:
        sess = Session(
            project_id=project_id,
            user_id=USER_ID,
            title=None,
            archived="false",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)
        logger.info(f"Created new thread: {sess.id}", extra={"session_id": sess.id, "user_id": USER_ID})
        return {"remoteId": sess.id}
    finally:
        db.close()


@app.get("/api/threads/{thread_id}/messages")
def get_thread_messages(thread_id: str):
    """Get all messages for a thread."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        messages = (
            db.query(Message)
            .filter_by(session_id=thread_id)
            .order_by(Message.created_at.asc())
            .all()
        )
        return {
            "messages": [
                {
                    "id": str(msg.id),
                    "role": msg.role,
                    "content": [{"type": "text", "text": msg.content}],
                    "createdAt": msg.created_at.isoformat(),
                }
                for msg in messages
            ]
        }
    finally:
        db.close()


@app.post("/api/threads/{thread_id}/messages")
def append_message(thread_id: str, request: MessageAppendRequest):
    """Append a message to a thread."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        msg = Message(
            session_id=thread_id,
            user_id=USER_ID,
            role=request.role,
            content=request.content,
        )
        db.add(msg)
        sess.last_activity_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
        logger.debug(f"Appended {request.role} message to thread", extra={"session_id": thread_id})
        return {"status": "ok", "id": str(msg.id)}
    finally:
        db.close()


@app.put("/api/threads/{thread_id}")
def rename_thread(thread_id: str, request: ThreadRenameRequest):
    """Rename a thread."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        sess.title = request.title
        db.commit()
        logger.info(f"Renamed thread to: {request.title}", extra={"session_id": thread_id})
        return {"status": "ok"}
    finally:
        db.close()


@app.post("/api/threads/{thread_id}/generate-title")
def generate_thread_title(thread_id: str):
    """Generate a title for the thread using LLM based on messages."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        messages = (
            db.query(Message)
            .filter_by(session_id=thread_id)
            .order_by(Message.created_at.asc())
            .limit(4)
            .all()
        )

        if not messages:
            return {"title": "New Chat"}

        context = "\n".join([
            f"{msg.role}: {msg.content[:200]}"
            for msg in messages
            if msg.content
        ])

        title_prompt = [
            {
                "role": "system",
                "content": "Generate a very short title (3-6 words) that summarizes this conversation. Return ONLY the title, nothing else. No quotes, no punctuation at the end."
            },
            {
                "role": "user",
                "content": f"Conversation:\n{context}"
            }
        ]

        try:
            llm = make_llm()
            title = llm(title_prompt).strip()
            title = title.strip('"\'').strip()
            if len(title) > 50:
                title = title[:47] + "..."
        except Exception as e:
            logger.error(f"Error generating title with LLM: {e}", extra={"session_id": thread_id}, exc_info=True)
            first_user = next((m for m in messages if m.role == "user"), None)
            if first_user and first_user.content:
                title = first_user.content[:50]
                if len(first_user.content) > 50:
                    title += "..."
            else:
                title = "New Chat"

        sess.title = title
        db.commit()
        logger.info(f"Generated title for thread: {title}", extra={"session_id": thread_id})

        return {"title": title}
    finally:
        db.close()


@app.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str):
    """Archive (soft delete) a thread."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        if sess.archived == "pinned":
            raise HTTPException(status_code=400, detail="Cannot archive pinned thread")

        sess.archived = "true"
        db.commit()
        logger.info(f"Archived thread", extra={"session_id": thread_id})
        return {"status": "ok"}
    finally:
        db.close()


@app.get("/api/threads/{thread_id}")
def get_thread(thread_id: str):
    """Get thread metadata."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        if sess.archived == "pinned":
            status = "regular"
        elif sess.archived == "true":
            status = "archived"
        else:
            status = "regular"

        return {
            "remoteId": sess.id,
            "status": status,
            "title": sess.title,
            "isPinned": sess.archived == "pinned",
        }
    finally:
        db.close()


@app.post("/api/threads/{thread_id}/unarchive")
def unarchive_thread(thread_id: str):
    """Unarchive a thread."""
    db = SessionLocal()
    try:
        sess = db.query(Session).filter_by(id=thread_id).first()
        if not sess:
            raise HTTPException(status_code=404, detail="Thread not found")

        sess.archived = "false"
        db.commit()
        logger.info(f"Unarchived thread", extra={"session_id": thread_id})
        return {"status": "ok"}
    finally:
        db.close()


@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/api/test")
def test_post(request: ContextRequest):
    """Test endpoint to verify POST requests work."""
    logger.debug(f"Test endpoint called with: {request}")
    return {"received": request.model_dump()}


# ============== Memory Management API ==============

from mem0_config import MEM0


class MemoryUpdateRequest(BaseModel):
    text: str


class ContactImportRequest(BaseModel):
    contact_id: str
    contact_name: str = None
    limit: int = None


@app.get("/api/memories")
def list_memories(project_id: str = None):
    """List all memories for the user, optionally filtered by project."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.get_all(user_id=USER_ID)
        memories = result.get("results", []) if isinstance(result, dict) else result

        if project_id:
            memories = [
                m for m in memories
                if m.get("metadata", {}).get("project_id") == project_id
            ]

        logger.info(f"Listed {len(memories)} memories", extra={"user_id": USER_ID})
        return {"memories": memories}
    except Exception as e:
        logger.error(f"Error listing memories: {e}", extra={"user_id": USER_ID}, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/memories/{memory_id}")
def get_memory(memory_id: str):
    """Get a specific memory by ID."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.get(memory_id)
        if not result:
            raise HTTPException(status_code=404, detail="Memory not found")
        logger.debug(f"Retrieved memory {memory_id}")
        return {"memory": result}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/memories/{memory_id}")
def update_memory(memory_id: str, request: MemoryUpdateRequest):
    """Update a memory's text."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.update(memory_id, request.text)
        logger.info(f"Updated memory {memory_id}")
        return {"status": "ok", "result": result}
    except Exception as e:
        logger.error(f"Error updating memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str):
    """Delete a specific memory."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        MEM0.delete(memory_id)
        logger.info(f"Deleted memory {memory_id}")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error deleting memory: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/memories")
def delete_all_memories(project_id: str = None):
    """Delete all memories for the user, optionally filtered by project."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        if project_id:
            result = MEM0.get_all(user_id=USER_ID)
            memories = result.get("results", []) if isinstance(result, dict) else result
            deleted = 0
            for m in memories:
                if m.get("metadata", {}).get("project_id") == project_id:
                    MEM0.delete(m["id"])
                    deleted += 1
            logger.info(f"Deleted {deleted} memories for project {project_id}", extra={"user_id": USER_ID})
            return {"status": "ok", "deleted": deleted}
        else:
            MEM0.delete_all(user_id=USER_ID)
            logger.info(f"Deleted all memories for user", extra={"user_id": USER_ID})
            return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error deleting memories: {e}", extra={"user_id": USER_ID}, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/search")
def search_memories(request: ContextRequest):
    """Search memories by query."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.search(
            request.message,
            user_id=USER_ID,
        )
        memories = result.get("results", []) if isinstance(result, dict) else result
        logger.info(f"Search found {len(memories)} memories", extra={"user_id": USER_ID})
        return {"memories": memories}
    except Exception as e:
        logger.error(f"Error searching memories: {e}", extra={"user_id": USER_ID}, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Contact/People API ==============

@app.get("/api/contacts")
def list_contacts():
    """List all contacts that have memories stored."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.get_all(user_id=USER_ID)
        memories = result.get("results", []) if isinstance(result, dict) else result

        contacts = {}
        for m in memories:
            metadata = m.get("metadata", {})
            contact_id = metadata.get("contact_id")
            if contact_id:
                if contact_id not in contacts:
                    contacts[contact_id] = {
                        "contact_id": contact_id,
                        "contact_name": metadata.get("contact_name", contact_id),
                        "source": metadata.get("source", "unknown"),
                        "memory_count": 0,
                    }
                contacts[contact_id]["memory_count"] += 1

        return {"contacts": list(contacts.values())}
    except Exception as e:
        logger.error(f"Error listing contacts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/contacts/{contact_id}/memories")
def get_contact_memories(contact_id: str):
    """Get all memories related to a specific contact."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.get_all(user_id=USER_ID)
        memories = result.get("results", []) if isinstance(result, dict) else result

        contact_memories = [
            m for m in memories
            if m.get("metadata", {}).get("contact_id") == contact_id
        ]

        logger.info(f"Found {len(contact_memories)} memories for contact {contact_id}")
        return {"memories": contact_memories}
    except Exception as e:
        logger.error(f"Error getting contact memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/contacts/{contact_id}/memories")
def delete_contact_memories(contact_id: str):
    """Delete all memories related to a specific contact."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        result = MEM0.get_all(user_id=USER_ID)
        memories = result.get("results", []) if isinstance(result, dict) else result

        deleted = 0
        for m in memories:
            if m.get("metadata", {}).get("contact_id") == contact_id:
                MEM0.delete(m["id"])
                deleted += 1

        logger.info(f"Deleted {deleted} memories for contact {contact_id}")
        return {"status": "ok", "deleted": deleted}
    except Exception as e:
        logger.error(f"Error deleting contact memories: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/imessage/contacts")
def list_imessage_contacts():
    """List contacts from the iMessage database with message counts."""
    try:
        from imessage_import import list_contacts as imsg_list_contacts

        contacts = imsg_list_contacts()

        formatted = []
        for contact_id, stats in sorted(
            contacts.items(),
            key=lambda x: x[1]["sent"] + x[1]["received"],
            reverse=True
        )[:100]:
            formatted.append({
                "contact_id": contact_id,
                "sent": stats["sent"],
                "received": stats["received"],
                "total": stats["sent"] + stats["received"],
                "last_message": stats["last_date"],
            })

        return {"contacts": formatted}
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="iMessage database not accessible. Ensure Full Disk Access is enabled."
        )
    except Exception as e:
        logger.error(f"Error listing iMessage contacts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/imessage/import")
def import_imessage_contact(request: ContactImportRequest):
    """Import iMessage conversations for a specific contact into mem0."""
    if MEM0 is None:
        raise HTTPException(status_code=503, detail="Memory system not available")

    try:
        from imessage_import import import_to_mem0

        contact_names = {}
        if request.contact_name:
            contact_names[request.contact_id] = request.contact_name

        import_to_mem0(
            contacts=[request.contact_id],
            contact_names=contact_names,
            limit=request.limit,
            dry_run=False,
            user_id=USER_ID,
        )

        return {"status": "ok", "contact_id": request.contact_id}
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="iMessage database not accessible. Ensure Full Disk Access is enabled."
        )
    except Exception as e:
        logger.error(f"Error importing iMessage contact: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============== Web Search API ==============

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


class SearchRequest(BaseModel):
    query: str
    max_results: int = 5
    search_depth: str = "basic"
    include_answer: bool = True


@app.post("/api/search")
async def web_search(request: SearchRequest):
    """Search the web using Tavily API."""
    if not TAVILY_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Web search not available. Set TAVILY_API_KEY to enable.",
        )

    try:
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": request.query,
                    "max_results": request.max_results,
                    "search_depth": request.search_depth,
                    "include_answer": request.include_answer,
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0),
            })

        logger.info(f"Web search for '{request.query}': {len(results)} results")

        return {
            "query": request.query,
            "answer": data.get("answer"),
            "results": results,
        }
    except httpx.HTTPStatusError as e:
        logger.error(f"Tavily API error: {e.response.status_code}", exc_info=True)
        raise HTTPException(status_code=502, detail="Search API error")
    except Exception as e:
        logger.error(f"Web search error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
